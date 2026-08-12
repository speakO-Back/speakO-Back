import os
import requests
import json
from dotenv import load_dotenv

from utils.usage_tracker import log_etri_call

load_dotenv()

REQUEST_TIMEOUT_SECONDS = 30

class EtriLanguageAnalyzer:
    def __init__(self):
        self.api_key = os.getenv("ETRI_API_KEY")
        # ETRI 형태소 분석 API 엔드포인트
        self.endpoint = "http://aiopen.etri.re.kr:8000/WiseNLU"

        # API 키가 없거나 '여기에_' 같은 기본값이면, 호출을 시도하지 않고 곧장 안전 모드로 반환
        # (플레이스홀더 값을 Authorization 헤더에 그대로 실으면 latin-1 인코딩 에러로 죽는다)
        self.use_fallback = not self.api_key or "여기에_" in self.api_key

        if self.use_fallback:
            print("⚠️ [경고] ETRI API 키가 설정되지 않았습니다.")
            print("⚠️ 발음 주의 단어 추출은 호출자 쪽 안전 모드(Fallback)에 맡깁니다.\n")

    def extract_difficult_words(self, text):
        """
        텍스트를 입력받아 ETRI API로 형태소를 분석한 뒤,
        발음 주의가 필요한 단어(명사, 고유명사, 외래어 등) 리스트를 반환합니다.
        """
        if self.use_fallback:
            return []

        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "Authorization": self.api_key
        }
        
        # 분석 코드 'morp'는 형태소 분석을 의미합니다.
        payload = {
            "argument": {
                "text": text,
                "analysis_code": "morp"
            }
        }
        
        print("🚀 ETRI 언어 분석 API에 형태소 분석을 요청합니다...")
        
        try:
            response = requests.post(self.endpoint, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            log_etri_call()

            result = response.json()
            
            # ⚠️ dict로 중복을 지운다 — set을 쓰면 안 된다.
            # 파이썬 문자열 해시는 프로세스마다 무작위라(PYTHONHASHSEED) list(set(...))의 순서가
            # 실행할 때마다 바뀐다. 호출자가 MAX_DIFFICULT_WORDS(40)로 앞에서 자르므로,
            # 순서가 흔들리면 같은 대본인데 매번 다른 단어 목록이 나온다.
            # (Kiwi 쪽에서 실제로 터진 결함 — nlp/kiwi_analyzer.py의 주석 참고)
            extracted_words = {}
            sentences = result.get('return_object', {}).get('sentence', [])

            for sentence in sentences:
                for morp in sentence.get('morp', []):
                    # NNG(일반명사), NNP(고유명사), SL(외국어) 품사만 추출
                    if morp['type'] in ['NNG', 'NNP', 'SL']:
                        # 한 글자 단어는 제외 (예: '것', '수' 등)
                        if len(morp['lemma']) > 1:
                            extracted_words.setdefault(morp['lemma'], None)

            return list(extracted_words)
            
        except Exception as e:
            print(f"❌ ETRI API 호출 중 에러가 발생했습니다: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"상세 에러 내용: {e.response.text}")
            return []

if __name__ == "__main__":
    analyzer = EtriLanguageAnalyzer()
    
    sample_text = "이번 SpeaKO 프로젝트는 메타버스 인프라를 활용하여 사용자에게 최적의 발음 피드백을 제공합니다."
    
    words = analyzer.extract_difficult_words(sample_text)
    
    if words:
        print("\n✨ [추출된 발음 주의 단어] ✨")
        print(words)