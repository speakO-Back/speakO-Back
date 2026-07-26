import os
import base64
import requests
from dotenv import load_dotenv

from utils.usage_tracker import log_hcx_call

load_dotenv()

REQUEST_TIMEOUT_SECONDS = 30

# HCX-005 비전이 그대로 받아주는 이미지 포맷. 이 목록에 없는 포맷(EMF/WMF/GIF/TIFF 등)은
# 아래 _prepare_image()에서 PNG로 변환해서 보낸다.
SUPPORTED_MIME_TYPES = ("image/png", "image/jpeg", "image/bmp", "image/webp")
# HCX-005 비전 입력 상한(20MB). base64는 원본보다 약 33% 커지므로 원본 기준으로 넉넉히 자른다.
MAX_IMAGE_BYTES = 14 * 1024 * 1024


def _prepare_image(image_bytes: bytes, mime_type: str):
    """
    (mime_type, image_bytes)를 HCX 비전이 받는 형태로 정규화한다.
    보낼 수 없는 이미지면 None을 반환한다.
    """
    if not image_bytes:
        return None
    if len(image_bytes) > MAX_IMAGE_BYTES:
        print(f"⚠️ 이미지가 너무 커서({len(image_bytes) // 1024}KB) 비전 인식을 건너뜁니다.")
        return None

    normalized = (mime_type or "").lower().split(";")[0].strip()
    if normalized in SUPPORTED_MIME_TYPES:
        return normalized, image_bytes

    # PPT에는 EMF/WMF(도형 붙여넣기)나 TIFF가 종종 섞여 들어온다. PNG로 바꿔서 살려본다.
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as img:
            buffer = io.BytesIO()
            img.convert("RGB").save(buffer, format="PNG")
            return "image/png", buffer.getvalue()
    except Exception as e:
        print(f"⚠️ 비전이 못 읽는 이미지 포맷({mime_type or '알 수 없음'})이고 PNG 변환도 실패해 건너뜁니다: {e}")
        return None


class ImageTextExtractor:
    """
    HCX-005의 비전(이미지 이해) 기능으로 이미지 안의 텍스트를 읽어온다.
    CLOVA OCR과 달리 새 키가 필요 없다 — 이미 대본 생성에 쓰는 HCX_API_KEY를 그대로 재사용한다.
    전용 OCR만큼 글자 단위로 정밀하진 않지만, 발표 슬라이드처럼 크고 또렷한 텍스트에는 충분히 쓸 만하다.
    """

    def __init__(self):
        self.api_key = os.getenv("HCX_API_KEY")
        self.model_name = os.getenv("HCX_MODEL_NAME", "HCX-005")
        self.endpoint = f"https://clovastudio.stream.ntruss.com/v3/chat-completions/{self.model_name}"
        # 키가 없으면 네트워크 호출 없이 곧장 안전 모드(빈 문자열 반환).
        self.use_fallback = not self.api_key or "여기에_" in self.api_key

    def extract_text_from_image(
        self, image_bytes: bytes, context_hint: str = "", mime_type: str = "image/png"
    ) -> str:
        """
        image_bytes: 슬라이드에서 뽑은 이미지 바이너리 (PNG/JPG/BMP/WEBP, 20MB 이하)
        context_hint: 발표 주제/목차 등, 모델이 애매한 글자를 문맥에 맞게 더 정확히 읽도록 돕는 힌트.
                      비워두면 힌트 없이 그대로 읽는다.
        mime_type:    이미지의 실제 MIME 타입(python-pptx의 `shape.image.content_type`).
                      HCX는 dataUri에 이 타입이 접두어로 붙어 있어야만 이미지를 받아준다.
        """
        if self.use_fallback:
            return ""

        prepared = _prepare_image(image_bytes, mime_type)
        if prepared is None:
            return ""
        prepared_mime, prepared_bytes = prepared

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        instruction = (
            "이 이미지 안에 보이는 글자를 전부 그대로 옮겨 적어줘. "
            "이미지에 대한 설명이나 해석, 요약은 하지 말고 실제로 적힌 텍스트만 나열해줘. "
            "글자가 하나도 없으면 빈 문자열로만 답해."
        )
        if context_hint:
            instruction = f"[참고 — 이 이미지가 속한 발표 맥락]\n{context_hint}\n\n{instruction}"

        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        # ⚠️ dataUri.data는 순수 base64가 아니라 "data:<mime>;base64,<payload>" 형태여야 한다.
                        # 접두어를 빼면 HCX가 이미지를 인식조차 못 하고 400(40001)으로 거절한다.
                        {
                            "type": "image_url",
                            "dataUri": {
                                "data": f"data:{prepared_mime};base64,"
                                + base64.b64encode(prepared_bytes).decode("utf-8")
                            },
                        },
                        {"type": "text", "text": instruction},
                    ],
                }
            ],
            "maxTokens": 500,
            "temperature": 0.1,
            "topP": 0.8,
        }

        try:
            response = requests.post(self.endpoint, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
            if response.status_code >= 400:
                # 본문에 실패 원인(잘못된 파라미터/포맷 등)이 들어 있다. 이걸 안 찍으면
                # 비전이 100% 실패하고 있어도 "이미지에 글자가 없나 보다"로 오해하게 된다.
                raise RuntimeError(f"HTTP {response.status_code} — {response.text[:300]}")

            result = response.json()
            text = result["result"]["message"]["content"].strip()

            usage = result.get("result", {}).get("usage", {})
            log_hcx_call(
                "vision_image_text",
                usage.get("promptTokens", 0),
                usage.get("completionTokens", 0),
                usage.get("totalTokens", 0),
            )

            return text

        except Exception as e:
            print(f"❌ HCX 비전 이미지 텍스트 추출 중 에러가 발생했습니다: {e}")
            return ""
