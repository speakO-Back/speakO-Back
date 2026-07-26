from utils.hangul_phonology import decompose_syllable, has_liaison_pattern


def test_decompose_syllable_returns_cho_jung_jong_indices():
    # "간" = 초성 ㄱ(0), 중성 ㅏ(0), 종성 ㄴ(4)
    assert decompose_syllable("간") == (0, 0, 4)


def test_decompose_syllable_returns_none_for_non_hangul():
    assert decompose_syllable("a") is None
    assert decompose_syllable("1") is None
    assert decompose_syllable("ㄱ") is None  # 자모 단독은 완성형 음절이 아님


def test_has_liaison_pattern_true_when_batchim_meets_null_onset_syllable():
    # "발음" = 발(받침 ㄹ) + 음(초성 ㅇ, 무초성) → 연음이 일어날 수 있는 구조
    assert has_liaison_pattern("발음") is True
    # "밭이" = 밭(받침 ㅌ) + 이(초성 ㅇ) → 마찬가지
    assert has_liaison_pattern("밭이") is True


def test_has_liaison_pattern_false_when_no_batchim_or_next_onset_not_null():
    # "국민" = 국(받침 ㄱ) + 민(초성 ㅁ, 무초성 아님) → 연음 구조 아님
    assert has_liaison_pattern("국민") is False
    # "가구" = 받침이 아예 없음
    assert has_liaison_pattern("가구") is False
