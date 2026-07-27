import os
import requests
from dotenv import load_dotenv

from utils.usage_tracker import log_hcx_call
from clova.toon_parser import parse_toon_slides
from clova.styles import STYLE_INSTRUCTIONS, audience_instruction  # noqa: F401 (STYLE_INSTRUCTIONS: 기존 임포트 경로 호환)

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

    def generate_partial_script(self, original_script, target_slide, style, extra_requirement="", audience=""):
        if self.use_fallback:
            return None
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        system_prompt = """
        당신은 프레젠테이션 스피치 라이터입니다.
        사용자의 요청을 반영하여 특정 슬라이드의 대본만 다시 작성해주세요.

        [작성 가이드라인]
        1. 대본(script) 내부에 쉼표(,)는 마침표(.)나 띄어쓰기로 대체하세요.
        2. 다른 슬라이드 내용은 건드리지 말고, 요청받은 슬라이드 하나만 다시 쓰세요.
        3. 출력은 반드시 아래의 [TOON 포맷]을 엄격히 준수하며, 다른 텍스트는 덧붙이지 마세요.

        [TOON 출력 포맷 예시]
        slides[1]{slide_number,script}:
         3,네 이번에는 시장 규모에 대해 살펴보겠습니다. 작년 대비 20퍼센트 성장했습니다.
        """

        requirement_text = STYLE_INSTRUCTIONS[style]
        requirement_text += f"\n대상(청중): {audience_instruction(audience)}"
        if extra_requirement and extra_requirement.strip():
            requirement_text += f"\n추가 요구사항: {extra_requirement.strip()}"

        user_prompt = f"""
        [대상 슬라이드 번호]
        Slide {target_slide}

        [기존 대본 내용]
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
            response = requests.post(self.endpoint, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()

            result = response.json()
            toon_text = result['result']['message']['content']

            usage = result.get('result', {}).get('usage', {})
            log_hcx_call(
                "partial",
                usage.get('promptTokens', 0),
                usage.get('completionTokens', 0),
                usage.get('totalTokens', 0),
            )

            parsed = parse_toon_slides(toon_text)
            if not parsed:
                return {"raw_toon": toon_text}
            return parsed[0]
        except Exception as e:
            print(f"❌ 대본 부분 재생성 API 호출 중 에러가 발생했습니다: {e}")
            return None