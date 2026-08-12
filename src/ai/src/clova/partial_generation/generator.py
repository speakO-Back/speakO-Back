import os
import requests
from dotenv import load_dotenv

from utils.usage_tracker import log_hcx_call
from clova.hcx_request import post_with_retry
from clova.toon_parser import clean_script_text
from clova.styles import audience_instruction, style_instruction

load_dotenv()

REQUEST_TIMEOUT_SECONDS = 30


class PartialScriptGenerator:
    def __init__(self):
        self.api_key = os.getenv("HCX_API_KEY")
        self.model_name = os.getenv("HCX_MODEL_NAME", "HCX-005")
        self.endpoint = f"https://clovastudio.stream.ntruss.com/v3/chat-completions/{self.model_name}"
        # 키가 없으면 네트워크 호출 없이 곧장 안전 모드(None 반환) — 다른 클라이언트와 동일한 패턴.
        self.use_fallback = not self.api_key or "여기에_" in self.api_key
        if self.use_fallback:
            print("⚠️ [경고] HCX_API_KEY가 설정되지 않았습니다. 부분 재생성은 안전 모드(None 반환)로 동작합니다.")

    def generate_partial_script(self, original_script, target_slide, style, extra_requirement="", audience="", source_content=""):
        if self.use_fallback:
            return None
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        # 한 장만 다시 쓰는 요청이므로 TOON 껍데기를 요구하지 않는다.
        # 요구했더니 모델이 헤더만 붙이고 본문은 평문으로 써서(실측: "slides[2]{slide_number,script}:" +
        # 줄바꿈 + 대본), 파서가 빈 결과를 내고 멀쩡한 대본이 502로 폐기됐다.
        # 전체 생성에서 이미 같은 이유로 TOON을 뺐다(clova/full_generation/generator.py 참고).
        system_prompt = """
        당신은 프레젠테이션 스피치 라이터입니다.
        사용자의 요청을 반영하여 요청받은 슬라이드 **하나의 대본만** 다시 작성해주세요.

        [작성 가이드라인]
        1. 요청받은 슬라이드 하나만 다시 쓰고, 다른 슬라이드 내용은 건드리지 마세요.
        2. 발표자가 청중 앞에서 실제로 말하듯 자연스럽게 쓰되, [재생성 요구사항]을 그대로 지키세요.
        3. 기존 대본은 흐름을 파악하는 참고용입니다. 앞뒤 슬라이드 내용을 끌어와 쓰지 마세요.

        출력은 대본 문장만 쓰세요. "Slide 3:" 같은 라벨, 머리말, 해설, 마크다운(**굵게**)을
        덧붙이지 마세요.
        """

        # 직접 인덱싱 금지 — 한국어 별칭(격식체 등)이 오면 KeyError가 난다.
        # style_instruction()이 별칭 매핑과 formal 폴백까지 처리한다 (clova/styles.py).
        requirement_text = style_instruction(style)
        requirement_text += f"\n대상(청중): {audience_instruction(audience)}"
        if extra_requirement and extra_requirement.strip():
            requirement_text += f"\n추가 요구사항: {extra_requirement.strip()}"

        # 대상 슬라이드의 원문이 있으면 먼저 보여준다. 없으면 모델은 이 장이 무슨 내용인지 모른 채
        # 앞뒤 대본만 보고 지어내게 된다(특히 아직 대본이 없는 슬라이드를 다시 만들 때).
        source_block = (source_content or "").strip()
        source_section = f"""
        [대상 슬라이드 원문 — 이 내용을 근거로 쓰세요]
        {source_block}
        """ if source_block else ""

        user_prompt = f"""
        [대상 슬라이드 번호]
        Slide {target_slide}
        {source_section}
        [전체 대본 (앞뒤 흐름 참고용 — 다른 장 내용을 끌어오지 마세요)]
        {original_script}

        [재생성 요구사항]
        {requirement_text}
        """

        payload = {
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "text", "text": user_prompt}]}
            ],
            "topP": 0.8,
            "topK": 0,
            "maxTokens": 800, # 부분 재생성 & TOON 포맷 최적화
            "temperature": 0.7, # 문장 출력 다양성을 위해 0.7로 상향 조정
            "repeatPenalty": 5.0
        }

        try:
            response = post_with_retry(self.endpoint, headers, payload, REQUEST_TIMEOUT_SECONDS, label="partial")
            result = response.json()
            toon_text = result['result']['message']['content']

            usage = result.get('result', {}).get('usage', {})
            log_hcx_call(
                "partial",
                usage.get('promptTokens', 0),
                usage.get('completionTokens', 0),
                usage.get('totalTokens', 0),
            )

            script = clean_script_text(toon_text)
            if not script:
                print("⚠️ 부분 재생성 응답이 비어 있습니다.")
                return None
            # 대상 슬라이드는 요청자가 지정한 번호다. 모델이 매긴 번호를 믿으면 엉뚱한 장에 저장된다.
            return {"slide_number": str(target_slide), "script": script}
        except Exception as e:
            print(f"❌ 대본 부분 재생성 API 호출 중 에러가 발생했습니다: {e}")
            return None