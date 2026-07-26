import os
import xml.etree.ElementTree as ET
import requests
from dotenv import load_dotenv

from utils.usage_tracker import log_stdict_call

load_dotenv()

REQUEST_TIMEOUT_SECONDS = 10
SEARCH_ENDPOINT = "https://stdict.korean.go.kr/api/search.do"
VIEW_ENDPOINT = "https://stdict.korean.go.kr/api/view.do"
LENGTH_MARK = "ː"  # MODIFIER LETTER TRIANGULAR COLON — 표준국어대사전이 장음 표기에 쓰는 문자
# 사이트가 view.do의 req_type=json을 지원하지 않아(빈 응답) XML로 요청한다. search.do는 json이 정상 동작한다.
_BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0"}


class StdictClient:
    """
    국립국어원 표준국어대사전 오픈 API로 단어의 장단음(모음 길이)을 판정한다.
    동음이의어가 여러 개면 검색 결과의 첫 번째 항목을 대표로 사용한다 — 문맥상 어떤 의미인지
    중의성을 해소하지는 않으므로, "이 단어가 장음으로 읽힐 수 있다"는 참고 신호로만 쓴다.
    """

    def __init__(self):
        self.api_key = os.getenv("STDICT_API_KEY")
        self.use_fallback = not self.api_key or "여기에_" in self.api_key
        # 같은 단어를 다시 조회할 때 외부 API를 또 때리지 않도록 프로세스 내 캐시.
        # (단어당 최대 2번의 직렬 HTTP 호출이 드니, 캐시가 없으면 대본에 반복되는 단어마다 낭비가 크다)
        self._cache = {}
        if self.use_fallback:
            print("⚠️ [경고] STDICT_API_KEY가 설정되지 않았습니다.")
            print("⚠️ 장단음 판정은 호출자 쪽 안전 모드(Fallback, 항상 False)에 맡깁니다.\n")

    def has_long_vowel(self, word: str) -> bool:
        if self.use_fallback or not word or not word.strip():
            return False

        word = word.strip()
        if word in self._cache:
            return self._cache[word]

        result = False
        try:
            target_code = self._find_target_code(word)
            if target_code:
                result = self._pronunciation_has_length_mark(target_code)
        except Exception as e:
            print(f"❌ 표준국어대사전 API 호출 중 에러가 발생했습니다: {e}")
            result = False

        self._cache[word] = result
        return result

    def _find_target_code(self, word: str):
        response = requests.get(
            SEARCH_ENDPOINT,
            params={"key": self.api_key, "q": word, "req_type": "json"},
            headers=_BROWSER_HEADERS,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        log_stdict_call()

        # 표준국어대사전 search.do는 매칭이 없으면 빈 JSON이 아니라 아예 빈 본문(0바이트)을 준다.
        # 그대로 .json()을 부르면 JSONDecodeError가 나므로 빈 본문을 먼저 걸러낸다.
        if not response.content.strip():
            return None

        items = response.json().get("channel", {}).get("item") or []
        if isinstance(items, dict):
            items = [items]
        return items[0].get("target_code") if items else None

    def _pronunciation_has_length_mark(self, target_code: str) -> bool:
        response = requests.get(
            VIEW_ENDPOINT,
            params={"key": self.api_key, "q": target_code, "method": "target_code"},
            headers=_BROWSER_HEADERS,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        log_stdict_call()

        # response.text가 아니라 response.content(bytes)를 넘겨야 ElementTree가 XML 선언의 인코딩을
        # 그대로 쓴다. 표준국어대사전은 Content-Type에 charset을 안 붙이는 경우가 있어, response.text로
        # 파싱하면 requests가 ISO-8859-1로 잘못 디코딩해 한글이 깨질 수 있다.
        root = ET.fromstring(response.content)
        for pronunciation_el in root.findall(".//pronunciation_info/pronunciation"):
            if pronunciation_el.text and LENGTH_MARK in pronunciation_el.text:
                return True
        return False
