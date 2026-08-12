"""피드백과 슬라이드 원문에서 **근거 없는 내용이 새어나가지 않는지** 고정한다.

발음 교정 제품에서 **틀린 교정을 자신 있게 말하는 것**은 아무 말도 안 하는 것보다 나쁘다.
사용자는 그걸 믿고 멀쩡한 발음을 고치려 든다.

## 1. 피드백이 음운을 지어내는 문제 (2026-08-09 실측)

Azure가 주는 건 **단어별 정확도 점수와 오류 유형뿐**이다. *어떤 소리를* 어떻게 틀렸는지는
측정되지 않는다 — 한국어는 음소 이름조차 오지 않는다(Azure 문서: phoneme name은 en-US(IPA)와
en-US·zh-CN(SAPI)만, 그 외 로케일은 점수만). 그런데 모델은 빈칸을 상상으로 채웠다:

    "'중요성을', '유연한', '마음으로' 등에서 받침을 생략하거나 잘못 발음하는 경우가 있습니다"
    "숫자 '7'을 발음할 때 영어식 발음이 아닌 한글 표기에 맞게 발음해야 합니다"

## 2. 비전의 "글자 없음" 서술이 슬라이드 원문이 되는 문제

이미지 전용 장표에서 비전이 돌려준 *서술문*이 그대로 `source_content`가 되고, 대본 생성기가
그걸 슬라이드 내용으로 믿었다(제로 PPT 12장 중 4장).
"""
import pytest

from clova.feedback import generator
from clova.feedback.generator import drop_unsupported_claims
from clova.vision.image_text_extractor import NO_TEXT_TOKEN, strip_vision_refusal


# ---------------------------------------------------------------- 걸러내야 하는 것

@pytest.mark.parametrize("line", [
    "'유연한', '마음으로' 등에서 받침을 생략하는 경향이 있습니다.",
    "숫자 '7'을 영어식 발음이 아닌 한글 표기에 맞게 발음해야 합니다.",
    "'마음으로'의 연음이 부자연스럽습니다.",
    "'핵심'에서 경음화가 제대로 이루어지지 않았습니다.",
    "'중요성을' 발음할 때 혀의 위치가 부정확합니다.",
    "'노력합니다'의 끝이 흐릿하게 뭉개집니다.",
    # 단어를 지목하지 **않아도** 알 수 없는 내용이다. 처음 규칙("따옴표 + 음운 용어")은
    # 이걸 놓쳤고 실호출에서 그대로 새어나왔다(2026-08-09).
    "여러 단어에서 끝소리가 제대로 처리되지 않은 것으로 보입니다.",
    "전반적으로 억양이 단조로운 편입니다.",
])
def test_drops_unmeasured_mechanism_claims(line):
    """측정되지 않은 **발음 메커니즘 서술**은 걸러낸다."""
    result = drop_unsupported_claims(
        {"summary": "", "strengths": [], "improvements": [line], "practice_tips": []},
        weak_words=[{"word": "유연한", "accuracy_score": 49}],
    )
    assert line not in result["improvements"]


# ---------------------------------------------------------------- 남겨야 하는 것

@pytest.mark.parametrize("line", [
    "'언제든지'가 28점으로 가장 낮았습니다. 천천히 반복해 연습해보세요.",
    "'노력합니다', '고민해보는'의 정확도가 낮았습니다. 한 음절씩 또박또박 읽어보세요.",
    "전반적으로 안정적인 속도로 읽으셨습니다.",
    "짧은 단어보다 긴 단어에서 점수가 낮았습니다.",
])
def test_keeps_score_based_lines(line):
    """**점수에서 바로 읽히는 사실**은 그대로 둔다. 이게 남아야 피드백이 쓸모 있다."""
    result = drop_unsupported_claims(
        {"summary": "", "strengths": [], "improvements": [line], "practice_tips": []},
        weak_words=[],
    )
    assert result["improvements"] == [line]


def test_drops_the_whole_item_when_its_first_sentence_goes():
    """⚠️ 앞문장을 잘라내고 뒤만 남기면 **말이 안 되는 조각**이 화면에 남는다.

    실측(2026-08-09 재검증): "숫자 '7'을 영어식으로 발음하셨습니다. 한국어로 자연스럽게
    발음하도록 주의하시기 바랍니다." 에서 앞문장만 걸렀더니, 한국어 발표인데 "한국어로
    발음하라"는 말만 남았다. 뒷문장은 앞문장에 기대어 쓰이므로 항목째 버려야 한다.
    """
    item = ("숫자 '7'을 영어식으로 발음하셨습니다. "
            "한국어로 자연스럽게 발음하도록 주의하시기 바랍니다.")
    result = drop_unsupported_claims(
        {"summary": "", "strengths": [item], "improvements": ["'가나다'의 정확도가 낮았습니다."],
         "practice_tips": []},
        weak_words=[],
    )
    assert result["strengths"] == []


def test_keeps_the_grounded_half_of_a_mixed_item():
    """한 항목에 사실과 날조가 섞여 있으면 **사실만** 남긴다. 통째로 버리면 손해다."""
    line = ("몇몇 단어에서 발음이 부정확했으며, 특히 '언제든지'처럼 짧은 단어조차 "
            "낮은 점수를 기록했습니다. 또한 여러 단어에서 끝소리가 제대로 처리되지 "
            "않은 것으로 보입니다.")
    result = drop_unsupported_claims(
        {"summary": "", "strengths": [], "improvements": [line], "practice_tips": []},
        weak_words=[],
    )
    kept = result["improvements"][0]
    assert "'언제든지'처럼 짧은 단어조차" in kept
    assert "끝소리" not in kept


def test_practice_tips_are_never_filtered():
    """⚠️ 연습 팁은 건드리면 안 된다.

    피그마가 자음/끝소리/강세억양/속도 **네 카드**를 그리므로 "받침이 있는 단어에서 자음을
    끝까지" 같은 일반 조언이 정상이다. 여기까지 걸러내면 화면에서 카드가 사라진다.
    """
    tips = [
        {"key": "consonant", "title": "명확한 자음 발음", "description": "받침이 있는 단어에서 자음을 끝까지 발음하세요."},
        {"key": "ending", "title": "정확한 끝소리", "description": "'모든' 단어의 끝소리를 정확히 내보세요."},
    ]
    result = drop_unsupported_claims(
        {"summary": "", "strengths": [], "improvements": ["'가'에서 받침 생략"], "practice_tips": tips},
        weak_words=[],
    )
    assert result["practice_tips"] == tips


def test_strengths_are_filtered_too():
    """칭찬도 지어낼 수 있다 — "받침을 정확히 발음했습니다"는 알 수 없는 내용이다."""
    result = drop_unsupported_claims(
        {"summary": "", "strengths": ["'우리는'의 받침을 정확하게 발음하셨습니다."],
         "improvements": ["'가나다'의 정확도가 낮았습니다."], "practice_tips": []},
        weak_words=[],
    )
    assert result["strengths"] == []


def test_summary_drops_only_the_offending_sentence():
    """총평은 여러 문장이라 통째로 버리면 멀쩡한 내용까지 날아간다."""
    summary = ("전반적으로 발음이 우수합니다. "
               "'유연한'에서 받침을 생략했습니다. "
               "속도는 안정적이었습니다.")
    result = drop_unsupported_claims(
        {"summary": summary, "strengths": [], "improvements": ["x"], "practice_tips": []},
        weak_words=[],
    )
    assert "전반적으로 발음이 우수합니다." in result["summary"]
    assert "속도는 안정적이었습니다." in result["summary"]
    assert "받침을 생략" not in result["summary"]


# ---------------------------------------------------------------- 전부 걸러졌을 때

def test_empty_improvements_are_replaced_with_a_grounded_line():
    """지적이 통째로 사라지면 화면의 '개선할 점'이 빈다 — 사용자는 평가가 실패한 줄 안다.
    점수만으로 쓸 수 있는 문장으로 채운다."""
    result = drop_unsupported_claims(
        {"summary": "", "strengths": [], "practice_tips": [],
         "improvements": ["'가'의 받침이 탈락했습니다.", "'나'의 연음이 어색합니다."]},
        weak_words=[{"word": "언제든지", "accuracy_score": 28},
                    {"word": "노력합니다", "accuracy_score": 35}],
    )

    assert len(result["improvements"]) == 1
    line = result["improvements"][0]
    assert "언제든지" in line and "노력합니다" in line
    # 대체 문장 자신이 또 음운을 단정하면 본말전도다.
    assert not any(term in line for term in ("받침", "연음", "경음화"))


def test_fallback_without_weak_words_still_says_something_useful():
    result = drop_unsupported_claims(
        {"summary": "", "strengths": [], "improvements": [], "practice_tips": []},
        weak_words=[],
    )
    assert result["improvements"] and result["improvements"][0].strip()


def test_none_sections_pass_through():
    assert drop_unsupported_claims(None, weak_words=[]) is None


def test_generate_feedback_applies_the_filter(monkeypatch):
    """실제 생성 경로에도 필터가 걸려 있는지 — 함수만 있고 안 부르면 의미가 없다."""
    raw = ("[총평]\n좋습니다.\n\n[잘한 점]\n- 안정적입니다.\n\n"
           "[개선할 점]\n- '유연한'에서 받침을 생략했습니다.\n\n"
           "[연습 팁]\nconsonant | 자음 | 받침을 끝까지 발음하세요.\n")

    class _Response:
        def json(self):
            return {"result": {"message": {"content": raw}, "usage": {}}}

    gen = generator.PronunciationFeedbackGenerator()
    gen.use_fallback = False
    gen.api_key = "test-key"
    monkeypatch.setattr(generator, "post_with_retry", lambda *a, **k: _Response())
    monkeypatch.setattr(generator, "log_hcx_call", lambda *a, **k: None)

    result = gen.generate_feedback(
        {"accuracy": 80}, [{"word": "유연한", "accuracy_score": 49}],
    )

    assert not any("받침을 생략" in line for line in result["improvements"])
    assert result["practice_tips"], "연습 팁까지 사라지면 안 된다"


# ---------------------------------------------------------------- 비전 "글자 없음"

@pytest.mark.parametrize("raw", [
    NO_TEXT_TOKEN,
    f"  {NO_TEXT_TOKEN}  ",
    "이미지에는 글자가 없습니다. 검은색 배경에 흰색의 수평선이 그어져 있습니다.",
    "이미지에는 텍스트가 없습니다.",
    "이미지에서 확인되는 텍스트는 없습니다.",
    "따라서 빈 문자열을 반환합니다.",
    # ⚠️ 실측으로 한 번 뚫린 표현들. "검은색 배경"만 막아뒀더니 "검은색 바탕"으로 새어나왔다.
    "검은색 바탕에 흰색의 줄이 그어져 있습니다\n전체가 검은색으로 되어 있어서 내용을 확인할 수 없습니다",
    "화면 전체가 흰색으로 채워져 있습니다.",
])
def test_vision_refusal_becomes_empty_source(raw):
    """서술문만 있으면 **빈 문자열**이 되어야 한다.

    빈 원문이 되어야 대본 생성기의 기존 "근거 없이 지어내지 말 것" 가드가 작동하고,
    `thin_source_slide_numbers`에 실려 프론트가 배지를 띄운다.
    """
    assert strip_vision_refusal(raw) == ""


def test_vision_keeps_real_text_next_to_the_refusal():
    """진짜 글자가 섞여 있으면 그건 반드시 살려야 한다 — 슬라이드 내용이다."""
    raw = "이미지에는 글자가 없습니다. 위드 코로나 시대의 연대"
    cleaned = strip_vision_refusal(raw)
    assert "위드 코로나 시대의 연대" in cleaned
    assert "글자가 없습니다" not in cleaned


def test_vision_passes_through_ordinary_slide_text():
    raw = "1. 들어가기에 앞서 리처드 로티란 누구일까?\n2. 자유주의 아이러니스트"
    assert "리처드 로티" in strip_vision_refusal(raw)


def test_vision_does_not_eat_periods_in_slide_text():
    """문장을 나눠 검사하더라도 **원래 구두점은 살아 있어야** 한다.

    마침표까지 날리면 목차 슬라이드의 "1. 들어가기에 앞서"가 "1 들어가기에 앞서"가 된다.
    (문장 분리를 `[\\n.]+`로 했다가 실제로 이렇게 깨졌다)
    """
    raw = "Contents\n1. 들어가기에 앞서 리처드 로티란 누구일까?\n2. 자유주의 아이러니스트"
    cleaned = strip_vision_refusal(raw)
    assert "1. 들어가기에 앞서" in cleaned
    assert "2. 자유주의 아이러니스트" in cleaned


def test_vision_keeps_slide_sentences_that_merely_mention_a_color():
    """색 이름이 나온다고 다 화면 묘사는 아니다 — 칠해짐/그어짐이 함께 있을 때만이다."""
    raw = "검은색 옷을 입은 발표자가 강조한 세 가지 원칙"
    assert "검은색 옷을 입은 발표자" in strip_vision_refusal(raw)
