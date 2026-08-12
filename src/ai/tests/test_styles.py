"""
발표 어투(스타일) 지시가 프롬프트에 실제로 주입되는지에 대한 회귀 테스트.

배경: 예전엔 프롬프트에 "발표 스타일: 격식체"라는 단어만 넣어서, 모델이 제멋대로
해석해 해요체(~이고요/~해보았고요/~에요)로 흘러내렸다(ClipRoute 대본 실측).
"격식체"가 무슨 어미를 쓰고 무슨 어미를 금지하는지 문장으로 못박아야 한다.
"""

import clova.full_generation.generator as generator_module
from clova.full_generation.generator import FullScriptGenerator
from clova.styles import STYLE_INSTRUCTIONS, audience_instruction, style_instruction


def test_formal_style_forbids_casual_endings():
    formal = STYLE_INSTRUCTIONS["formal"]
    assert "하십시오체" in formal or "습니다" in formal
    for banned in ("이고요", "해요", "답니다"):
        assert banned in formal, f"금지 어미 '{banned}'가 지시에 명시돼야 한다"


def test_casual_style_targets_casual_endings_and_avoids_stiff():
    """어미 고정은 스타일별로 달라야 한다 — casual은 해요체를 지향하고 딱딱한 문어체를 피한다."""
    casual = STYLE_INSTRUCTIONS["casual"]
    assert "해요" in casual and "네요" in casual
    assert "하십시오" in casual, "지나치게 격식 있는 어미로 새지 않도록 명시돼야 한다"


def test_each_style_locks_different_endings():
    """두 스타일의 지시가 서로 달라야 의미가 있다(같은 문장을 재사용하면 안 됨)."""
    assert STYLE_INSTRUCTIONS["formal"] != STYLE_INSTRUCTIONS["casual"]


def test_unknown_style_falls_back_to_formal():
    assert style_instruction("존재하지-않는-스타일") == STYLE_INSTRUCTIONS["formal"]


def test_korean_aliases_still_work():
    """스프링 연동(8/12)부터 공식 값은 formal/casual이지만, 기존 문서·프론트가 쓰던
    한국어 값도 같은 지시로 이어져야 한다. 여기가 깨지면 한국어 값이 **조용히 formal로
    폴백**해서, 편안한 말투를 고른 사용자가 격식체 대본을 받는다."""
    assert style_instruction("격식체") == STYLE_INSTRUCTIONS["formal"]
    assert style_instruction("편안한 말투") == STYLE_INSTRUCTIONS["casual"]


class _FakeResponse:
    def __init__(self, content):
        self._content = content
        self.status_code = 200
        self.text = content

    def raise_for_status(self):
        pass

    def json(self):
        return {
            "result": {
                "message": {"content": self._content},
                "usage": {"promptTokens": 1, "completionTokens": 1, "totalTokens": 2},
            }
        }


def _capture_prompts(monkeypatch):
    sent = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(json["messages"][-1]["content"][0]["text"])
        return _FakeResponse("한 문장 대본입니다.")

    monkeypatch.setattr(generator_module.requests, "post", fake_post)
    return sent


def test_style_instruction_is_injected_into_generation_prompt(monkeypatch):
    generator = FullScriptGenerator()
    generator.api_key = "test-key"
    generator.use_fallback = False

    sent = _capture_prompts(monkeypatch)
    generator.generate_full_script("Slide 1: 첫 내용\nSlide 2: 둘째 내용", 4, "격식체")

    # 단어("격식체")만이 아니라 어미 지시가 프롬프트에 들어가야 한다.
    assert all("하십시오체" in prompt for prompt in sent)
    assert all("말투" in prompt for prompt in sent)


def test_audience_default_when_empty():
    """대상은 선택 입력 — 비어 있으면 특정 청중을 가정하지 말라고 지시해야 한다(피그마: 발표 주제만 필수)."""
    inst = audience_instruction("")
    assert "일반 청중" in inst


def test_audience_is_woven_into_instruction():
    inst = audience_instruction("면접관")
    assert "면접관" in inst


def test_audience_is_injected_into_generation_prompt(monkeypatch):
    """피그마 '대상' 필드가 생성 프롬프트에 실제로 반영되는지."""
    generator = FullScriptGenerator()
    generator.api_key = "test-key"
    generator.use_fallback = False

    sent = _capture_prompts(monkeypatch)
    generator.generate_full_script("Slide 1: 첫 내용\nSlide 2: 둘째 내용", 4, "격식체", audience="교수님")

    assert all("교수님" in prompt for prompt in sent)
    assert all("대상" in prompt for prompt in sent)


def test_topic_is_injected_into_generation_prompt(monkeypatch):
    """피그마의 유일한 필수 입력 '발표 주제'가 생성 프롬프트에 실제로 반영되는지."""
    generator = FullScriptGenerator()
    generator.api_key = "test-key"
    generator.use_fallback = False

    sent = _capture_prompts(monkeypatch)
    generator.generate_full_script("Slide 1: 첫 내용\nSlide 2: 둘째 내용", 4, "격식체", topic="Clip Route 서비스 소개")

    assert all("Clip Route 서비스 소개" in prompt for prompt in sent)
    assert all("발표 주제" in prompt for prompt in sent)
