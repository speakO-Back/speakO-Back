import os
import re
import requests
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

from utils.usage_tracker import log_hcx_call
from clova.toon_parser import parse_toon_slides
from clova.styles import (
    FIRST_SLIDE_INSTRUCTION,
    LAST_SLIDE_INSTRUCTION,
    MIDDLE_SLIDE_INSTRUCTION,
    style_instruction,
)

load_dotenv()

REQUEST_TIMEOUT_SECONDS = 30

# 슬라이드별 생성/고도화는 서로 독립적이라 동시에 부를 수 있다. 순차로 30장을 부르면
# 장당 2~4초 × 30 = 1~2분이 걸리지만, 동시에 부르면 그만큼 줄어든다.
# 다만 HCX 레이트리밋이 있으므로 동시 개수에 상한을 둔다(환경변수로 조정 가능).
MAX_CONCURRENT_REQUESTS = max(1, int(os.getenv("HCX_MAX_CONCURRENCY", "4")))

# 고도화(리뷰)는 "Slide N:" 라벨을 그대로 유지하는 작업이라 여러 장을 함께 넘겨도 정렬이 안 밀린다.
# 다만 입력이 길면 슬라이드를 통째로 빠뜨리므로 이 단위로 나눈다.
REFINE_SLIDES_PER_REQUEST = 6


def _is_placeholder_key(api_key):
    """HCX_API_KEY가 없거나 .env.example의 플레이스홀더 그대로면 True (안전 모드로 전환)."""
    return not api_key or "여기에_" in api_key


def _split_slide_blocks(ppt_text):
    """"Slide N: 내용" 평문을 [(슬라이드번호, 해당 블록 원문)] 목록으로 나눈다."""
    matches = list(re.finditer(r"(?m)^\s*Slide\s+(\d+)\s*:", ppt_text))
    if not matches:
        return []

    blocks = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(ppt_text)
        blocks.append((match.group(1), ppt_text[match.start():end].strip()))
    return blocks


def _clean_single_slide_script(text):
    """
    한 장짜리 요청의 응답을 대본 문장만 남기고 정리한다.
    평문으로 답하라고 했지만 모델이 습관적으로 TOON 껍데기나 "Slide 1:" 라벨을 붙이는 경우가 있다.
    """
    if not text:
        return ""

    script = text.strip()

    # 혹시 TOON으로 답했으면 벗겨낸다.
    parsed = parse_toon_slides(script)
    if parsed:
        script = " ".join(item["script"] for item in parsed)

    script = re.sub(r"^\s*Slide\s*\d*\s*:\s*", "", script)
    script = re.sub(r"\s+", " ", script).strip()
    return script


def _position_label(index, total):
    """
    발표 중 이 슬라이드의 위치를 "지시문"으로 돌려준다.

    매 슬라이드를 독립 요청으로 만들면 모델은 자기가 발표 중간에 있다는 걸 모른다.
    위치를 단순 라벨("3번째 슬라이드")로만 주면 무시하고 장마다 "안녕하세요"로 시작한다
    (실측: 19장 중 16장). 그래서 하지 말아야 할 것을 문장으로 못박는다(clova/styles.py).

    ⚠️ 이웃 슬라이드의 "내용"을 맥락으로 함께 넘겨봤지만 역효과였다 — 모델이 그 내용까지
    대본으로 써버려서(3번 대본이 4번 내용을 설명) 정렬이 다시 깨졌다. 위치만 알려준다.
    """
    if total <= 1:
        return ""
    if index == 0:
        return f"전체 {total}장 중 첫 번째 슬라이드입니다. {FIRST_SLIDE_INSTRUCTION}"
    if index == total - 1:
        return f"전체 {total}장 중 마지막 슬라이드입니다. {LAST_SLIDE_INSTRUCTION}"
    return f"전체 {total}장 중 {index + 1}번째 슬라이드입니다. {MIDDLE_SLIDE_INSTRUCTION}"


class FullScriptGenerator:
    def __init__(self):
        self.api_key = os.getenv("HCX_API_KEY")
        self.model_name = os.getenv("HCX_MODEL_NAME", "HCX-005")
        self.endpoint = f"https://clovastudio.stream.ntruss.com/v3/chat-completions/{self.model_name}"
        # 키가 없으면 다른 4개 클라이언트(ETRI/Azure/Clova Voice/stdict)와 동일하게
        # 네트워크 호출 없이 곧장 안전 모드로 빠진다. (플레이스홀더 키로 실제 요청을 보내면
        # 무의미한 30초 타임아웃 대기 + 불필요한 외부 호출이 발생한다)
        self.use_fallback = _is_placeholder_key(self.api_key)
        if self.use_fallback:
            print("⚠️ [경고] HCX_API_KEY가 설정되지 않았습니다. 대본 생성은 안전 모드(None 반환)로 동작합니다.")

    def generate_full_script(self, ppt_text, presentation_time, style, extra_requirement=""):
        """
        슬라이드를 **한 장씩** 요청해서 결과를 합친다.

        여러 장을 한 번에 넣으면 모델이 어떤 장은 건너뛰고 어떤 장은 두 줄로 쪼개는데,
        그러면 슬라이드와 대본의 정렬이 통째로 밀린다. (실측: 목차 장을 건너뛰는 바람에
        2번 대본에 3번 내용이 들어갔고, 전체의 26%가 이웃 슬라이드 내용을 말하고 있었다)
        한 장만 보내면 무슨 응답이 오든 그 장의 대본이므로 정렬이 구조적으로 보장된다.
        대신 대본이 앞뒤로 자연스럽게 이어지도록 이웃 슬라이드를 맥락으로 함께 넘긴다.
        """
        if self.use_fallback:
            return None

        blocks = _split_slide_blocks(ppt_text)
        if len(blocks) <= 1:
            # 원본이 없거나 한 덩어리(주제/목차 브리프)일 때는 모델이 여러 슬라이드로 "확장"하는 것이
            # 정상 동작이다. PPT 없이 주제만 받은 프로젝트는 이 확장 결과가 곧 슬라이드가 된다.
            # 그러니 여기서는 번호를 손대지 않고 모델이 매긴 그대로 돌려준다.
            return self._request_raw(ppt_text, presentation_time, style, extra_requirement, None)

        per_slide_time = max(1, round(presentation_time / len(blocks)))

        # 슬라이드별 호출은 서로 독립적이라(각 장은 자기 내용 + 위치만 씀) 동시에 부른다.
        # 순서는 결과를 슬라이드 번호로 정렬해 맞추므로 완료 순서가 뒤섞여도 문제없다.
        def build_one(item):
            index, (number, block) = item
            slides = self._request_one_slide(
                number, block, _position_label(index, len(blocks)), per_slide_time, style, extra_requirement
            )
            return number, slides

        all_slides = []
        missing = []
        workers = min(MAX_CONCURRENT_REQUESTS, len(blocks))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for number, slides in pool.map(build_one, enumerate(blocks)):
                if slides:
                    all_slides.extend(slides)
                else:
                    missing.append(number)

        if missing:
            print(f"  ⚠️ 슬라이드 {missing} 생성 실패(모델이 포맷을 안 지킴).")
        if not all_slides:
            return None

        all_slides.sort(key=lambda s: int(s["slide_number"]))
        return {"slides": all_slides}

    def _request_one_slide(self, slide_number, block, position, presentation_time, style, extra_requirement):
        """
        한 장만 요청할 때는 **TOON 포맷을 쓰지 않는다.**
        응답 전체가 곧 이 슬라이드의 대본이므로 구분자가 필요 없고, 오히려 껍데기를 요구하면
        모델이 그걸 안 지켜서 멀쩡한 대본이 버려진다. (실측: 내용은 완벽한데 TOON이 아니라는
        이유로 19장 중 18장을 폐기했다)

        한 장이 실패하면 그 슬라이드는 영구 누락이므로 한 번 더 시도한다.
        """
        for attempt in range(2):
            text = self._call_hcx(
                self._SINGLE_SLIDE_PROMPT,
                self._build_user_prompt(block, presentation_time, style, extra_requirement, position),
            )
            script = _clean_single_slide_script(text)
            if script:
                return [{"slide_number": slide_number, "script": script}]
            if attempt == 0:
                print(f"  ↻ 슬라이드 {slide_number} 응답이 비어 한 번 더 시도합니다.")

        return []

    # 슬라이드 한 장의 대본만 쓰는 경우(정상 경로). 지시가 짧을수록 결과가 안정적이다 —
    # 규칙을 8개까지 늘렸더니 오히려 19장 중 13장이 형식을 못 지켰다.
    _SINGLE_SLIDE_PROMPT = """
        당신은 대한민국 최고의 '프레젠테이션 스피치 라이터'입니다.
        주어진 슬라이드 **한 장**에 대해, 발표자가 그 슬라이드를 띄워놓고 말할 대본을 작성하세요.

        [작성 가이드라인]
        1. 슬라이드에 적힌 내용만 다루세요. 없는 내용을 지어내지 마세요.
        2. 발표자가 청중 앞에서 실제로 말하듯 자연스럽게 작성하세요. 문어체(보고서 문장)는 피하되,
           말투(문장 끝맺음)는 아래 [반드시 지킬 것 — 말투]를 그대로 따르세요.
        3. [추가 요구사항]이 주어지면 반드시 반영하세요.
        4. [반드시 지킬 것 — 위치]의 지시를 그대로 따르세요.

        출력은 대본 문장만 쓰세요. "Slide 1:" 같은 라벨이나 머리말, 해설을 덧붙이지 마세요.
        """

    # 원본이 브리프 한 덩어리뿐이라, 모델이 여러 슬라이드로 확장해야 하는 경우.
    _MULTI_SLIDE_PROMPT = """
        당신은 대한민국 최고의 '프레젠테이션 스피치 라이터'입니다.
        사용자가 제공하는 [PPT 텍스트]와 [발표 조건]을 바탕으로 자연스러운 전체 발표 대본을 작성해주세요.

        [작성 가이드라인]
        1. 각 슬라이드의 핵심 메시지를 파악해 발표자가 말하듯 작성하세요.
           말투(문장 끝맺음)는 [반드시 지킬 것 — 말투]를 그대로 따르세요.
        2. 대본 내용(script) 안에는 쉼표(,) 대신 마침표(.)나 띄어쓰기를 사용하세요. (파싱 오류 방지)
        3. [추가 요구사항]이 주어지면 반드시 반영하세요.
        4. 출력은 반드시 토큰 최적화된 아래의 [TOON 포맷]을 엄격히 준수하며, 다른 텍스트는 덧붙이지 마세요.
        5. 슬라이드마다 한 줄씩 출력하고, 각 줄은 슬라이드 번호와 쉼표로 시작하세요.
           전체를 하나의 줄글로 쓰면 안 됩니다.

        [TOON 출력 포맷 예시]
        slides[슬라이드총개수]{slide_number,script}:
         1,안녕하세요 오늘 발표를 맡은 진행자입니다. 첫 번째 슬라이드입니다.
         2,다음으로 넘어가겠습니다. 시장 규모를 살펴보면...
        """

    def _build_user_prompt(self, ppt_text, presentation_time, style, extra_requirement, position):
        # [위치]와 [말투] 지시는 프롬프트 맨 뒤에 둔다. 중간에 끼워 넣었더니 모델이 무시하고
        # 장마다 인사말로 시작하거나(19장 중 16장) 해요체로 흘러내렸다.
        # "발표 스타일: 격식체"처럼 단어만 주면 안 되고, 어미까지 문장으로 못박아야 한다.
        return f"""
        [발표 조건]
        - 발표 시간: {presentation_time}분

        [추가 요구사항]
        {extra_requirement or '없음'}

        [PPT 텍스트]
        {ppt_text}

        [반드시 지킬 것 — 위치]
        {position or '전체 발표 대본을 작성하세요.'}

        [반드시 지킬 것 — 말투]
        {style_instruction(style)}
        """

    def _request_raw(self, ppt_text, presentation_time, style, extra_requirement, valid_slide_numbers):
        """브리프 한 덩어리를 여러 슬라이드로 확장하는 경로. 여기서만 TOON이 필요하다."""
        toon_text = self._call_hcx(
            self._MULTI_SLIDE_PROMPT,
            self._build_user_prompt(ppt_text, presentation_time, style, extra_requirement, ""),
        )
        if toon_text is None:
            return None
        return self._parse_toon_format(toon_text, valid_slide_numbers)

    def _call_hcx(self, system_prompt, user_prompt):
        """HCX 호출 공통부. 응답 본문 문자열을 돌려주고, 실패하면 None."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        payload = {
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "text", "text": user_prompt}]}
            ],
            "topP": 0.8,
            "topK": 0,
            "maxTokens": 2000, # TOON 포맷으로 인해 필요 토큰 수가 대폭 줄어듭니다.
            "temperature": 0.5,
            "repeatPenalty": 5.0
        }

        try:
            response = requests.post(self.endpoint, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
            if response.status_code >= 400:
                # 상태코드만으로는 원인을 알 수 없다. 응답 본문에 진짜 이유가 들어있다.
                raise RuntimeError(f"HTTP {response.status_code} — {response.text[:300]}")

            result = response.json()

            usage = result.get('result', {}).get('usage', {})
            log_hcx_call(
                "full",
                usage.get('promptTokens', 0),
                usage.get('completionTokens', 0),
                usage.get('totalTokens', 0),
            )

            return result['result']['message']['content']

        except Exception as e:
            print(f"❌ 전체 대본 생성 API 호출 중 에러가 발생했습니다: {e}")
            return None

    def _parse_toon_format(self, toon_text, valid_slide_numbers=None):
        try:
            data_list = parse_toon_slides(toon_text, valid_slide_numbers)
            if not data_list:
                return {"raw_toon": toon_text}
            return {"slides": data_list}
        except Exception as e:
            print(f"⚠️ TOON 포맷 파싱 에러: {e}")
            return {"raw_toon": toon_text}

class ScriptRefiner:
    """
    FullScriptGenerator가 만든 초안 대본을 다시 HCX에 넣어
    발표자 1인칭 구어체로 더 자연스럽게 다듬는 2차 리뷰 단계.
    입력/출력 모두 "Slide N: 내용" 형식의 평문 대본이다.
    """

    def __init__(self):
        self.api_key = os.getenv("HCX_API_KEY")
        self.model_name = os.getenv("HCX_MODEL_NAME", "HCX-005")
        self.endpoint = f"https://clovastudio.stream.ntruss.com/v3/chat-completions/{self.model_name}"
        self.use_fallback = _is_placeholder_key(self.api_key)

    def refine_script(self, script_text, style="격식체"):
        """
        생성 단계와 같은 이유로(입력이 길면 모델이 슬라이드를 빠뜨리거나 줄글로 합쳐버림)
        긴 대본은 나눠서 다듬는다. 다듬은 결과에서 슬라이드가 사라졌으면 그 묶음은 초안을 그대로 쓴다.
        """
        if self.use_fallback:
            return None

        blocks = _split_slide_blocks(script_text)
        if not blocks:
            return self._refine_chunk(script_text, style)

        chunks = [blocks[start:start + REFINE_SLIDES_PER_REQUEST] for start in range(0, len(blocks), REFINE_SLIDES_PER_REQUEST)]

        # 묶음(chunk)들도 서로 독립적이라 동시에 다듬는다. pool.map은 입력 순서대로 결과를 돌려주므로
        # refined_parts를 그대로 이어 붙여도 슬라이드 순서가 유지된다.
        def refine_one(chunk):
            chunk_text = "\n\n".join(block for _, block in chunk)
            refined = self._refine_chunk(chunk_text, style)
            # 다듬다가 슬라이드를 잃어버렸거나 번호가 바뀌었으면 자연스러움보다 내용 보존이 우선이다.
            expected = [number for number, _ in chunk]
            if not refined or [number for number, _ in _split_slide_blocks(refined)] != expected:
                print(f"  ⚠️ 슬라이드 {chunk[0][0]}~{chunk[-1][0]} 고도화 결과가 원본과 안 맞아 초안을 유지합니다.")
                refined = chunk_text
            return refined

        workers = min(MAX_CONCURRENT_REQUESTS, len(chunks))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            refined_parts = list(pool.map(refine_one, chunks))

        return "\n\n".join(refined_parts)

    def _refine_chunk(self, script_text, style):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        system_prompt = """
        당신은 발표 대본을 다듬는 전문 감수자입니다.
        입력으로 주어지는 초안 대본은 "Slide N: 내용" 형식의 줄들로 구성되어 있습니다.
        이 대본을 발표자가 청중 앞에서 실제로 말하듯 자연스럽게 다듬어주세요.

        [다듬을 때 기준]
        1. 관찰자 시점("~설명합니다", "~보여줍니다")이 아니라 발표자가 청중에게 직접 말하는 어투("~설명드리겠습니다", "~보여드리겠습니다")로 통일하세요.
        2. 문장이 어색하거나 번역체스러운 부분, 반복되는 표현은 자연스럽게 고치세요.
        3. 문장 끝맺음(말투)은 아래 [반드시 지킬 것 — 말투]를 그대로 따르세요.
        4. 각 슬라이드의 핵심 내용과 정보, 슬라이드 순서는 그대로 유지하세요. 새로운 정보를 추가하거나 빼지 마세요.
        5. "Slide N:" 라벨과 슬라이드 개수는 절대 바꾸지 마세요. 입력에 있던 슬라이드 번호를 그대로 유지하세요.
        6. 과도하게 길이를 늘리거나 줄이지 말고, 자연스러움 개선에만 집중하세요.

        출력은 반드시 입력과 동일하게 "Slide N: 내용" 형식의 줄들로만 구성하고, 다른 설명이나 안내 문구는 절대 덧붙이지 마세요.
        """

        user_prompt = f"""
        [초안 대본]
        {script_text}

        [반드시 지킬 것 — 말투]
        {style_instruction(style)}
        """

        payload = {
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "text", "text": user_prompt}]},
            ],
            "topP": 0.8,
            "topK": 0,
            "maxTokens": 2000,
            "temperature": 0.4,
            "repeatPenalty": 3.0,
        }

        print("🚀 HyperCLOVA X에 대본 자연스러움 고도화(리뷰)를 요청합니다...")

        try:
            response = requests.post(self.endpoint, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()

            result = response.json()
            refined_text = result["result"]["message"]["content"]

            usage = result.get("result", {}).get("usage", {})
            log_hcx_call(
                "refine",
                usage.get("promptTokens", 0),
                usage.get("completionTokens", 0),
                usage.get("totalTokens", 0),
            )

            return refined_text.strip()

        except Exception as e:
            print(f"❌ 대본 고도화 API 호출 중 에러가 발생했습니다: {e}")
            return None


if __name__ == "__main__":
    ai_client = FullScriptGenerator()
    sample_ppt = "Slide 1: 메타버스 개요. Slide 2: 시장 규모."
    print(ai_client.generate_full_script(sample_ppt, 1, "격식체"))