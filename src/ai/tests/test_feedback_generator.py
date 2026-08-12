"""
발음 평가 결과를 코칭 피드백으로 바꾸는 부분에 대한 테스트.

핵심 위험은 두 가지다.
1) 모델이 머리말 형식을 정확히 안 지킨다 → 파서가 관대해야 하고, 형식을 어겨도 내용은 살려야 한다.
2) 근거로 삼을 '약한 단어'를 잘못 고른다 → 안 읽은 단어(Omission)를 발음이 나쁘다고 지적하면 안 된다.
"""

from clova.feedback.generator import (
    PronunciationFeedbackGenerator,
    collect_weak_words,
    parse_feedback_sections,
)


def test_collect_weak_words_picks_low_scores_and_mispronunciation():
    words = [
        {"word": "메타버스와", "accuracy_score": 95.0, "error_type": "None"},
        {"word": "구축의", "accuracy_score": 60.0, "error_type": "Mispronunciation"},
        {"word": "특징을", "accuracy_score": 50.0, "error_type": "Mispronunciation"},
        {"word": "살펴봅시다", "accuracy_score": 92.0, "error_type": "None"},
    ]
    weak = collect_weak_words(words)
    # 점수가 낮은 순으로 정렬된다.
    assert [w["word"] for w in weak] == ["특징을", "구축의"]


def test_collect_weak_words_excludes_omission():
    """Omission은 '발음이 나쁜 것'이 아니라 안 읽은 것이다. 발음 지적 근거로 쓰면 안 된다."""
    words = [
        {"word": "안읽은단어", "accuracy_score": 0.0, "error_type": "Omission"},
        {"word": "틀린단어", "accuracy_score": 55.0, "error_type": "Mispronunciation"},
    ]
    weak = collect_weak_words(words)
    assert [w["word"] for w in weak] == ["틀린단어"]


def test_collect_weak_words_handles_empty_and_malformed():
    assert collect_weak_words(None) == []
    assert collect_weak_words([{"not": "a word"}, "문자열", None]) == []


def test_collect_weak_words_respects_limit():
    words = [{"word": f"단어{i}", "accuracy_score": 10.0 + i, "error_type": "Mispronunciation"} for i in range(30)]
    assert len(collect_weak_words(words, limit=5)) == 5


def test_parse_feedback_sections_extracts_four_sections():
    text = """
[총평]
전반적으로 또렷하게 발음했습니다. 다만 받침 처리가 아쉽습니다.

[잘한 점]
- 속도가 일정합니다.
- 문장 끝을 흐리지 않았습니다.

[개선할 점]
- '특징을'의 받침 ㅇ을 끝까지 발음하세요.

[연습 팁]
- 어려운 단어만 3번씩 천천히 읽어보세요.
- 녹음해서 다시 들어보세요.
"""
    parsed = parse_feedback_sections(text)
    assert "또렷하게" in parsed["summary"]
    assert parsed["strengths"] == ["속도가 일정합니다.", "문장 끝을 흐리지 않았습니다."]
    assert parsed["improvements"] == ["'특징을'의 받침 ㅇ을 끝까지 발음하세요."]
    assert len(parsed["practice_tips"]) == 2


def test_parse_feedback_sections_tolerates_markdown_headers():
    """모델이 머리말을 **총평** / 총평: 처럼 변형해도 파싱돼야 한다."""
    text = "**총평**\n좋았습니다.\n\n개선할 점:\n1. 또박또박 말하세요."
    parsed = parse_feedback_sections(text)
    assert parsed["summary"] == "좋았습니다."
    assert parsed["improvements"] == ["또박또박 말하세요."]


def test_parse_feedback_sections_keeps_content_when_no_headers():
    """머리말을 아예 안 지켜도 내용을 버리지 않는다(전부 총평으로 살린다)."""
    parsed = parse_feedback_sections("발음이 전반적으로 정확했습니다.")
    assert parsed["summary"] == "발음이 전반적으로 정확했습니다."
    assert parsed["strengths"] == []


def test_parse_feedback_sections_returns_none_for_empty():
    assert parse_feedback_sections("") is None
    assert parse_feedback_sections(None) is None


class _FakeResponse:
    def __init__(self, content):
        self.status_code = 200
        self.text = content
        self._content = content

    def json(self):
        return {
            "result": {
                "message": {"content": self._content},
                "usage": {"promptTokens": 1, "completionTokens": 1, "totalTokens": 2},
            }
        }


def test_generate_feedback_sends_scores_and_weak_words_in_prompt(monkeypatch):
    """지적의 근거가 되도록 점수와 틀린 단어가 실제로 프롬프트에 들어가야 한다."""
    import clova.feedback.generator as feedback_module

    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent["prompt"] = json["messages"][-1]["content"][0]["text"]
        return _FakeResponse("[총평]\n좋습니다.")

    monkeypatch.setattr(feedback_module.requests, "post", fake_post)

    generator = PronunciationFeedbackGenerator()
    generator.api_key = "test-key"
    generator.use_fallback = False

    result = generator.generate_feedback(
        {"accuracy": 87.4, "fluency": 82.1, "completeness": 95.0, "pronunciation_score": 86.0},
        [{"word": "특징을", "accuracy_score": 50.0, "error_type": "Mispronunciation"}],
        "Slide 1: 메타버스를 소개합니다.",
    )

    assert "87.4" in sent["prompt"]
    assert "특징을" in sent["prompt"]
    assert result["summary"] == "좋습니다."


def test_generate_feedback_returns_none_without_api_key():
    generator = PronunciationFeedbackGenerator()
    generator.use_fallback = True
    assert generator.generate_feedback({"accuracy": 90}, []) is None


def test_collect_strong_words_picks_high_scores():
    """칭찬에도 근거가 필요하다 — 안 주면 모델이 대본에서 아무 단어나 골라 칭찬한다(실측)."""
    from clova.feedback.generator import collect_strong_words

    words = [
        {"word": "평가", "accuracy_score": 98.0, "error_type": "None"},
        {"word": "발음", "accuracy_score": 92.5, "error_type": "None"},
        {"word": "발전", "accuracy_score": 52.0, "error_type": "Mispronunciation"},
        {"word": "안읽음", "accuracy_score": 100.0, "error_type": "Omission"},
    ]
    strong = collect_strong_words(words)
    # 점수 높은 순, 틀린 단어와 안 읽은 단어는 제외.
    assert [w["word"] for w in strong] == ["평가", "발음"]


def test_strong_words_are_sent_as_praise_evidence(monkeypatch):
    import clova.feedback.generator as feedback_module

    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent["prompt"] = json["messages"][-1]["content"][0]["text"]
        return _FakeResponse("[총평]\n좋습니다.")

    monkeypatch.setattr(feedback_module.requests, "post", fake_post)

    generator = PronunciationFeedbackGenerator()
    generator.api_key = "test-key"
    generator.use_fallback = False
    generator.generate_feedback(
        {"accuracy": 90.0},
        [{"word": "발전", "accuracy_score": 52.0, "error_type": "Mispronunciation"}],
        "Slide 1: 인공지능 서비스를 소개합니다.",
        [{"word": "평가", "accuracy_score": 98.0}],
    )

    assert "잘 발음한 단어" in sent["prompt"]
    assert "평가" in sent["prompt"]
    # 대본 단어를 근거로 쓰지 말라는 지시가 포함돼야 한다.
    assert "여기서 단어를 골라 평가하지 마세요" in sent["prompt"]
