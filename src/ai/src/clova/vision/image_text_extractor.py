import os
import base64
import re
import requests
from dotenv import load_dotenv

from utils.usage_tracker import log_hcx_call
from clova.hcx_request import post_with_retry

load_dotenv()

REQUEST_TIMEOUT_SECONDS = 30

# ── "글자가 없다"는 **서술**을 슬라이드 내용으로 착각하지 않기 ────────────────
#
# 이미지 전용 장표(사진 한 장, 검은 배경에 선 하나 등)를 주면 비전은 글자를 못 찾았다는 사실을
# **문장으로 설명해서** 돌려준다. 그 문장을 그대로 `source_content`에 넣으면 대본 생성기는
# 그걸 슬라이드 내용으로 믿고 대본을 쓴다. 실측(2026-08-09, 제로 PPT):
#
#   #6 원문: "이미지에는 글자가 없습니다. 검은색 배경에 흰색의 수평선이 그어져 있습니다. …"
#   #6 대본: "여러분, 지금 우리가 살아가는 시대는 바로 '위드 코로나' 시대입니다. …" (629자)
#
# 12장 중 4장(33%)이 이 상태였다. `_SINGLE_SLIDE_PROMPT`의 "근거 없이 지어내지 말 것" 가드는
# 원문이 **비어 있을 때** 작동하는데, 이건 비어 있지 않은 텍스트라 그냥 통과한다.
# 그래서 여기서 빈 문자열로 정규화해 **기존 가드에 태운다** — 그러면 대본은 일반 안내문이 되고,
# `thin_source_slide_numbers`에 실려 프론트가 "내용을 직접 보완해 주세요" 배지를 띄운다.
# 1차 방어 — 글자가 없을 때 답하기로 **약속한 토큰**.
# 자유 문장은 표현이 매번 달라지지만(실측: "이미지에는 글자가 없습니다" /
# "검은색 바탕에 흰색의 줄이 그어져 있습니다" / "전체가 검은색으로 되어 있어서 내용을 확인할 수
# 없습니다") 약속된 토큰은 정확히 일치시킬 수 있다.
NO_TEXT_TOKEN = "__NO_TEXT__"

# 2차 방어 — 그래도 모델이 설명문으로 답할 때.
# 특정 문구를 외우는 대신 **"이미지를 설명하는 문장"의 모양**을 잡는다. 문구를 쫓으면 표현이
# 바뀔 때마다 새어나온다 — 실제로 "검은색 배경"만 막아뒀다가 "검은색 바탕"에 뚫렸다.
_IMAGE_SUBJECT = ("이미지", "그림", "사진", "화면", "슬라이드", "배경", "바탕")
_DESCRIPTION_VERB = (
    "없습니다", "없음", "없다", "확인할 수 없", "보이지 않", "빈 문자열",
    "그어져", "칠해져", "채워져", "표시되어", "포함되어 있지",
)
# "전체가 검은색으로 되어 있어서…"처럼 주어가 이미지가 아닌 경우를 위한 두 번째 신호.
# 색 이름 + 칠해짐/그어짐은 슬라이드 문장이 아니라 화면 묘사다.
_COLOR_WORDS = ("검은색", "검정", "흰색", "하얀", "회색", "파란색", "빨간색", "노란색")
_FILL_VERB = ("그어져", "칠해져", "채워져", "되어 있", "표시되어")
# 모델이 **자기 출력을 설명하는** 말. 발표 슬라이드에 이런 문장이 적혀 있을 리 없다.
_SELF_NARRATION = ("빈 문자열", "출력할 내용이 없", "추출할 텍스트가 없", "답변할 수 없")

# 문장 끝 구두점은 **남긴 문장에 붙여서** 자른다. 마침표까지 날리면 "1. 들어가기에 앞서"가
# "1 들어가기에 앞서"가 되어 실제 슬라이드 내용이 훼손된다.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _is_image_description(sentence: str) -> bool:
    """슬라이드에 적힌 글자가 아니라 **이미지를 설명하는 문장**인가.

    세 신호 중 하나면 서술로 본다:
      · 이미지/배경/바탕을 주어로 삼고 "없습니다/그어져 있습니다"라고 말한다
      · 색 이름과 함께 "칠해져/되어 있다"고 말한다  ("전체가 검은색으로 되어 있어서…")
      · 자기 출력을 설명한다  ("따라서 빈 문자열을 반환합니다")

    발표 슬라이드 문장이 이 조합을 만들 일은 드물다. 설령 잘못 걸러도 손해는 그 문장 하나지만,
    통과시키면 **슬라이드와 무관한 대본 한 장**이 만들어진다 — 한쪽이 훨씬 비싸다.
    """
    if any(marker in sentence for marker in _SELF_NARRATION):
        return True
    has_image_claim = (any(s in sentence for s in _IMAGE_SUBJECT)
                       and any(v in sentence for v in _DESCRIPTION_VERB))
    has_color_claim = (any(c in sentence for c in _COLOR_WORDS)
                       and any(v in sentence for v in _FILL_VERB))
    return has_image_claim or has_color_claim


def strip_vision_refusal(text: str) -> str:
    """비전이 "글자가 없다"고 설명한 문장을 걷어내고 실제로 읽힌 글자만 남긴다.

    남는 게 없으면 **빈 문자열**을 돌려준다 — 그게 사실이기 때문이다(이 장에는 글자가 없다).
    """
    raw = (text or "").strip()
    if not raw or NO_TEXT_TOKEN in raw:
        return ""

    kept = [s.strip() for s in _SENTENCE_SPLIT.split(raw) if s.strip() and not _is_image_description(s)]
    return re.sub(r"[ \t​]+", " ", " ".join(kept)).strip(" -·|,.")

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

        # ⚠️ "빈 문자열로 답해"만으로는 부족하다 — 모델은 글자가 없으면 **그 사실을 설명하려 든다**
        # ("검은색 바탕에 흰색의 줄이 그어져 있습니다"). 그 설명이 슬라이드 원문으로 저장되면
        # 대본 생성기가 그걸 내용으로 믿는다. 그래서 **정해진 한 단어**로만 답하게 한다.
        # 자유 문장은 표현이 매번 달라 걸러내기 어렵지만, 약속된 토큰은 정확히 일치시킬 수 있다.
        instruction = (
            "이 이미지 안에 보이는 글자를 전부 그대로 옮겨 적어줘. "
            "이미지에 대한 설명이나 해석, 요약은 하지 말고 실제로 적힌 텍스트만 나열해줘. "
            f"글자가 하나도 없으면 다른 말은 절대 붙이지 말고 {NO_TEXT_TOKEN} 이 한 단어만 답해. "
            "배경색이나 도형 모양을 설명하지 마."
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
            # 실패 시 응답 본문을 예외에 실어 던진다(post_with_retry가 처리). 이걸 안 찍으면
            # 비전이 100% 실패하고 있어도 "이미지에 글자가 없나 보다"로 오해하게 된다.
            response = post_with_retry(self.endpoint, headers, payload, REQUEST_TIMEOUT_SECONDS, label="vision")
            result = response.json()
            raw_text = result["result"]["message"]["content"].strip()
            # "글자가 없습니다"는 슬라이드 내용이 아니라 비전의 **서술**이다. 걷어내지 않으면
            # 대본 생성기가 이걸 근거로 슬라이드와 무관한 대본을 쓴다(위 _VISION_REFUSAL 주석).
            text = strip_vision_refusal(raw_text)
            if raw_text and not text:
                print("ℹ️ 비전이 '글자 없음'으로 답해 빈 원문으로 처리합니다 (대본 지어내기 방지).")

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
