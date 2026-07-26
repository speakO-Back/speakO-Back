class KiwiAnalyzer:
    """
    Kiwi(kiwipiepy) 로컬 형태소 분석기로 발음 주의 단어(명사/고유명사/외국어)를 추출한다.
    ETRI WiseNLU와 같은 역할이지만 API 키/네트워크가 필요 없다 — 로컬에서 오프라인으로 동작.

    이 프로젝트의 다른 클라이언트와 동일한 안전 모드 패턴을 따른다:
    Kiwi 로드에 실패하면(설치 안 됨/모델 로드 실패) use_fallback=True로 전환하고,
    extract_difficult_words는 빈 리스트를 반환해 호출자 쪽 상위 폴백에 넘긴다.
    """

    # ETRI와 동일한 품사 코드 — NNG(일반명사), NNP(고유명사), SL(외국어)
    TARGET_TAGS = ("NNG", "NNP", "SL")

    def __init__(self):
        self.use_fallback = False
        self.kiwi = None
        try:
            from kiwipiepy import Kiwi
            self.kiwi = Kiwi()
            print("✅ Kiwi 형태소 분석기 로드 완료 (발음 주의 단어 추출에 사용).")
        except Exception as e:
            print(f"⚠️ [경고] Kiwi 로드 실패({e}). 발음 주의 단어 추출은 상위 폴백에 맡깁니다.")
            self.use_fallback = True

    def extract_difficult_words(self, text: str) -> list:
        """
        텍스트에서 명사/고유명사/외국어 중 2글자 이상인 단어를 중복 없이 추출한다.
        (한 글자 단어 '것/수/데' 등은 발음 주의 대상으로 의미가 낮아 제외 — ETRI 로직과 동일)
        """
        if self.use_fallback or not text or not text.strip():
            return []

        try:
            tokens = self.kiwi.tokenize(text)
            words = {
                token.form
                for token in tokens
                if token.tag in self.TARGET_TAGS and len(token.form) > 1
            }
            return list(words)
        except Exception as e:
            print(f"❌ Kiwi 형태소 분석 중 에러가 발생했습니다: {e}")
            return []
