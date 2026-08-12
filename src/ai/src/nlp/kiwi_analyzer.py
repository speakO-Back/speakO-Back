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

        ⚠️ **대본에 처음 나온 순서를 반드시 유지한다.** 예전엔 집합(set)으로 중복을 지우고
        `list(set(...))`으로 돌려줬는데, 파이썬 문자열 해시는 프로세스마다 무작위라
        (PYTHONHASHSEED) **같은 대본인데 실행할 때마다 순서가 달라졌다.** 호출자가
        `MAX_DIFFICULT_WORDS`(40개)로 앞에서 자르기 때문에, 순서가 흔들리면 **잘려나가는
        단어가 매번 바뀐다** — 실측(2026-08-09, 제로 대본): 후보 144개 중 40개만 남는데
        연속 3회 실행에서 앞 8개가 전부 달랐고, 최종 목록도 16개 vs 20개로 갈렸다.
        사용자 눈에는 "같은 대본을 다시 분석했더니 발음 주의 단어가 딴 게 나온다"로 보인다.

        처음 나온 순서로 두면 결정적일 뿐 아니라, 잘릴 때 **발표 앞부분 단어가 남아서**
        사용자가 먼저 마주치는 단어들이 목록에 들어간다.
        """
        if self.use_fallback or not text or not text.strip():
            return []

        try:
            tokens = self.kiwi.tokenize(text)
            # dict는 삽입 순서를 보존한다 — 첫 등장 순서 유지 + 중복 제거를 한 번에.
            words = {
                token.form: None
                for token in tokens
                if token.tag in self.TARGET_TAGS and len(token.form) > 1
            }
            return list(words)
        except Exception as e:
            print(f"❌ Kiwi 형태소 분석 중 에러가 발생했습니다: {e}")
            return []
