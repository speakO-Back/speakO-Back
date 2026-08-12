"""음운 규칙 판정기 단위 테스트.

이 판정 결과가 그대로 사용자에게 "경음화입니다"라고 표시되므로, 틀리면 **사용자가 잘못된
음운 지식을 배운다.** 대표 사례를 회귀 테스트로 고정한다.
"""
import pytest

from utils import phonology_rules as pr


@pytest.mark.parametrize("word,phoneme,expected", [
    ("국물", "[궁물]", "비음화"),      # 받침 ㄱ이 뒤 ㅁ의 영향으로 ㅇ
    ("협력", "[혐녁]", "비음화"),      # 상호 비음화 (ㅂ→ㅁ, ㄹ→ㄴ)
    ("신라", "[실라]", "유음화"),      # ㄴ+ㄹ → ㄹㄹ
    ("특정", "[특쩡]", "경음화"),      # 받침 뒤 예사소리가 된소리로
    ("학교", "[학꾜]", "경음화"),
    ("맑다", "[막따]", "경음화"),      # 겹받침 ㄺ이 ㄱ으로 줄고 ㄷ이 된소리로
    ("굳이", "[구지]", "구개음화"),    # 받침 ㄷ + '이' → ㅈ
    ("맏이", "[마지]", "구개음화"),
    ("좋고", "[조코]", "격음화"),      # ㅎ + ㄱ → ㅋ
])
def test_detect_rule(word, phoneme, expected):
    assert pr.detect_rule(word, phoneme) == expected


def test_no_rule_when_spelling_equals_pronunciation():
    assert pr.detect_rule("인공지능", "[인공지능]") is None


def test_no_rule_when_syllable_count_differs():
    """음절 수가 다르면(축약 등) 자모를 짝지을 수 없다. 억지로 이름 붙이느니 판정을 포기한다."""
    assert pr.detect_rule("사이", "[새]") is None


def test_length_mark_is_inserted_after_the_right_syllable():
    assert pr.apply_length_marks("[구성]", (0,)) == "[구ː성]"
    assert pr.apply_length_marks("[사건]", (1,)) == "[사건ː]"
    assert pr.apply_length_marks("[학교]", ()) == "[학교]"


def test_length_mark_is_not_duplicated():
    assert pr.apply_length_marks("[구ː성]", (0,)) == "[구ː성]"


def test_description_names_the_specific_rule():
    """카테고리 이름("표기-발음불일치")이 아니라 구체적 현상 이름이 나와야 한다 — 피그마 요구사항."""
    assert pr.describe("특정", "[특쩡]", "표기-발음불일치").startswith("경음화:")
    assert pr.describe("국물", "[궁물]", "표기-발음불일치").startswith("비음화:")


def test_description_falls_back_when_rule_is_unknown():
    """판정 실패 시 틀린 이름을 붙이지 않고 일반 문구로 물러난다."""
    text = pr.describe("사이", "[새]", "표기-발음불일치")
    assert "표기와 발음이 다릅니다" in text


def test_long_vowel_description_uses_actual_position():
    assert "첫 음절" in pr.describe("구성", "[구ː성]", "장단음", (0,))
    assert "2번째 음절" in pr.describe("사건", "[사건ː]", "장단음", (1,))


def test_liaison_description_is_fixed():
    assert pr.describe("발음", "[바름]", "연음").startswith("연음:")


def test_aspiration_detected_when_plain_coda_meets_h_onset():
    """격음화는 두 방향이 있는데 '받침 ㅎ + 예사소리'만 보고 있었다.
    실은 '예사소리 받침 + 초성 ㅎ'(축하, 역할, 입학)이 더 흔한데 전부 일반 문구로 떨어졌다.
    실측(2026-08-06): 제로 녹음 대본의 '역할 › [여칼]'이 "표기와 발음이 다릅니다"로 나왔다."""
    from utils.phonology_rules import detect_rule

    assert detect_rule("역할", "[여칼]") == "격음화"
    assert detect_rule("입학", "[이팍]") == "격음화"
    assert detect_rule("축하", "[추카]") == "격음화"
    assert detect_rule("맏형", "[마텽]") == "격음화"
    assert detect_rule("급히", "[그피]") == "격음화"


def test_aspiration_with_h_coda_still_detected():
    """반대 방향(받침 ㅎ + 예사소리)이 깨지지 않아야 한다."""
    from utils.phonology_rules import detect_rule

    assert detect_rule("좋고", "[조코]") == "격음화"
    assert detect_rule("놓다", "[노타]") == "격음화"


def test_other_rules_are_not_swallowed_by_aspiration():
    """격음화 판정을 넓히면서 다른 현상을 가로채면 사용자가 틀린 음운 지식을 배운다."""
    from utils.phonology_rules import detect_rule

    assert detect_rule("국물", "[궁물]") == "비음화"
    assert detect_rule("신라", "[실라]") == "유음화"
    assert detect_rule("특정", "[특쩡]") == "경음화"
    assert detect_rule("굳이", "[구지]") == "구개음화"
