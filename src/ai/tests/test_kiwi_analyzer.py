from nlp.kiwi_analyzer import KiwiAnalyzer


def test_kiwi_extracts_nouns_proper_nouns_and_foreign_words():
    analyzer = KiwiAnalyzer()
    # kiwipiepy가 설치돼 있으면 로드된다(requirements). 혹시 로드 실패 환경이면 이 테스트는 스킵 성격.
    if analyzer.use_fallback:
        return
    words = analyzer.extract_difficult_words(
        "오늘 발표에서는 메타버스와 인프라 구축의 특징을 살펴봅니다. ChatGPT로 분석했습니다."
    )
    # 명사/고유명사/외국어가 뽑히고, 조사('와','의','을')와 한 글자 단어는 빠져야 한다.
    assert "메타버스" in words   # NNP
    assert "인프라" in words     # NNG
    assert "특징" in words       # NNG
    assert "ChatGPT" in words    # SL
    assert all(len(w) > 1 for w in words)
    assert "와" not in words and "의" not in words


def test_kiwi_returns_empty_for_blank_input():
    analyzer = KiwiAnalyzer()
    assert analyzer.extract_difficult_words("") == []
    assert analyzer.extract_difficult_words("   ") == []


def test_kiwi_fallback_returns_empty_when_not_loaded():
    analyzer = KiwiAnalyzer()
    analyzer.use_fallback = True  # 로드 실패 상황을 강제로 재현
    assert analyzer.extract_difficult_words("메타버스 인프라") == []
