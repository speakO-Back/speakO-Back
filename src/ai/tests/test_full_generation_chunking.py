"""
슬라이드를 한 장씩 나눠서 생성하는 로직의 회귀 테스트.

배경 (전부 실측):
- 19장짜리 PPT를 한 번에 넣었더니 모델이 TOON 포맷을 버리고 전체를 줄글 하나로 써서 18장이 유실됐다.
- 모델은 입력 번호가 13~18이든 상관없이 **응답을 항상 1번부터** 매긴다. 절대 번호를 믿으면 통째로 버려진다.
- 여러 장을 한 번에 넣으면 어떤 장은 건너뛰고 어떤 장은 두 줄로 쪼개서 **정렬이 밀린다**.
  (2번 대본에 3번 내용이 들어감 / 전체의 26%가 이웃 슬라이드 내용을 말하고 있었다)
  그래서 지금은 한 장씩 요청한다 — 한 장만 보내면 무슨 응답이 오든 그 장의 대본이다.
"""

import clova.full_generation.generator as generator_module
from clova.full_generation.generator import FullScriptGenerator, _split_slide_blocks


def _generator():
    instance = FullScriptGenerator()
    instance.api_key = "test-key"
    instance.use_fallback = False
    return instance


def _ppt_text(count):
    return "\n".join(f"Slide {i}: {i}번 슬라이드 내용" for i in range(1, count + 1))


class _FakeResponse:
    def __init__(self, content, status_code=200):
        self._content = content
        self.status_code = status_code
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


def _stub_hcx(monkeypatch, responder):
    """responder(user_prompt) -> 모델이 반환할 문자열. None을 반환하면 API 실패로 취급한다."""
    sent = []

    def fake_post(url, headers=None, json=None, timeout=None):
        user_prompt = json["messages"][-1]["content"][0]["text"]
        sent.append(user_prompt)
        content = responder(user_prompt)
        if content is None:
            return _FakeResponse("", status_code=500)
        return _FakeResponse(content)

    monkeypatch.setattr(generator_module.requests, "post", fake_post)
    return sent


def test_split_slide_blocks_keeps_numbers_and_content():
    blocks = _split_slide_blocks("Slide 1: 첫 내용\nSlide 2: 둘째 내용")

    assert [num for num, _ in blocks] == ["1", "2"]
    assert "첫 내용" in blocks[0][1]


def test_each_slide_gets_its_own_request(monkeypatch):
    def responder(user_prompt):
        return "slides[1]{slide_number,script}:\n 1,이 슬라이드의 대본입니다."

    sent = _stub_hcx(monkeypatch, responder)
    result = _generator().generate_full_script(_ppt_text(5), 10, "격식체")

    assert len(sent) == 5, "슬라이드마다 한 번씩 요청해야 정렬이 보장된다"
    assert [s["slide_number"] for s in result["slides"]] == ["1", "2", "3", "4", "5"]


def test_each_request_contains_only_its_own_slide(monkeypatch):
    """
    정렬이 밀리는 근본 원인은 한 요청에 여러 장이 들어가는 것이었다.
    대본을 쓸 대상 슬라이드는 항상 하나여야 한다(이웃은 [앞뒤 맥락]으로만 들어간다).
    """

    def responder(user_prompt):
        return "slides[1]{slide_number,script}:\n 1,대본"

    sent = _stub_hcx(monkeypatch, responder)
    _generator().generate_full_script(_ppt_text(4), 8, "격식체")

    for prompt in sent:
        target_section = prompt.split("[PPT 텍스트]")[1]
        assert target_section.count("Slide ") == 1


def test_position_is_given_so_middle_slides_do_not_greet(monkeypatch):
    """
    매 슬라이드가 독립 요청이라 모델이 자기가 발표 중간인 걸 모르고 장마다 인사한다.
    위치를 알려줘야 첫 장에서만 인사하고 마지막 장에서만 마무리한다.

    ⚠️ 이웃 슬라이드의 "내용"까지 넘겼더니 모델이 그 내용의 대본을 써버려서(3번이 4번을 설명)
    정렬이 다시 깨졌다. 그래서 내용이 아니라 위치만 넘긴다.
    """

    def responder(user_prompt):
        return "slides[1]{slide_number,script}:\n 1,대본"

    sent = _stub_hcx(monkeypatch, responder)
    _generator().generate_full_script(_ppt_text(3), 6, "격식체")

    # 슬라이드별 호출이 병렬이라 sent의 순서는 보장되지 않는다. 위치가 아니라 내용으로 프롬프트를 찾는다.
    def position_for(slide_marker):
        prompt = next(p for p in sent if slide_marker in p.split("[PPT 텍스트]")[1])
        return prompt.split("[반드시 지킬 것 — 위치]")[1]

    assert "첫 번째" in position_for("1번 슬라이드 내용")
    middle = position_for("2번 슬라이드 내용")
    assert "인사말" in middle and "시작하지 말고" in middle
    assert "마지막" in position_for("3번 슬라이드 내용")
    # 이웃 슬라이드 내용이 2번 프롬프트에 새어 들어가면 안 된다.
    second = next(p for p in sent if "2번 슬라이드 내용" in p.split("[PPT 텍스트]")[1])
    assert "3번 슬라이드 내용" not in second


def test_failed_slide_is_retried_once(monkeypatch):
    """한 장이 실패하면 그 슬라이드는 영구 누락이므로 한 번은 다시 시도해야 한다."""
    import threading

    lock = threading.Lock()
    failed_once = set()

    def responder(user_prompt):
        # 병렬 실행이라 스레드 안전하게, 1번 슬라이드만 첫 시도에서 실패시키고 재시도에서 성공시킨다.
        if "1번 슬라이드 내용" in user_prompt.split("[PPT 텍스트]")[1]:
            with lock:
                first_try = "s1" not in failed_once
                failed_once.add("s1")
            return None if first_try else "두 번째 시도 대본"
        return "2번 슬라이드 대본"

    _stub_hcx(monkeypatch, responder)
    result = _generator().generate_full_script(_ppt_text(2), 4, "격식체")

    scripts = {s["slide_number"]: s["script"] for s in result["slides"]}
    assert scripts["1"] == "두 번째 시도 대본"  # 재시도로 살아남음
    assert scripts["2"] == "2번 슬라이드 대본"


def test_local_numbering_is_mapped_back_to_real_slide_numbers(monkeypatch):
    """13~18번을 보내도 모델은 1~6으로 답한다. 그대로 두면 전부 버려진다."""

    def responder(user_prompt):
        return "slides[6]{slide_number,script}:\n" + "\n".join(f" {i},{i}번째 대본" for i in range(1, 7))

    _stub_hcx(monkeypatch, responder)
    ppt_text = "\n".join(f"Slide {i}: 내용 {i}" for i in range(13, 19))
    result = _generator().generate_full_script(ppt_text, 5, "격식체")

    assert [s["slide_number"] for s in result["slides"]] == ["13", "14", "15", "16", "17", "18"]


def test_extra_rows_are_merged_into_the_requested_slide(monkeypatch):
    """
    한 장을 보냈는데 모델이 2~3번까지 써버려도, 그 내용은 결국 그 장에서 할 말이다.
    한 장의 대본으로 합쳐야 한다 — 새 슬라이드로 만들면 원본에 없는 슬라이드가 생긴다.
    """

    def responder(user_prompt):
        return "slides[1]{1,앞부분입니다.}\n{2,뒷부분입니다.}"

    _stub_hcx(monkeypatch, responder)
    result = _generator().generate_full_script(_ppt_text(2), 4, "격식체")

    assert [s["slide_number"] for s in result["slides"]] == ["1", "2"]
    assert all(s["script"] == "앞부분입니다. 뒷부분입니다." for s in result["slides"])


def test_failed_slide_does_not_shift_the_others(monkeypatch):
    """한 장이 끝내 실패해도 나머지 슬라이드 번호가 밀리면 안 된다."""

    def responder(user_prompt):
        if "2번 슬라이드 내용" in user_prompt.split("[PPT 텍스트]")[1]:
            return None
        return "정상 대본"

    _stub_hcx(monkeypatch, responder)
    result = _generator().generate_full_script(_ppt_text(3), 6, "격식체")

    assert [s["slide_number"] for s in result["slides"]] == ["1", "3"]


def test_toon_wrapper_is_stripped_from_plain_response(monkeypatch):
    """평문으로 답하라고 해도 모델이 습관적으로 TOON 껍데기를 붙이는 경우가 있다."""

    def responder(user_prompt):
        return "slides[1]{slide_number,script}:\n 1,실제 대본 문장입니다."

    _stub_hcx(monkeypatch, responder)
    result = _generator().generate_full_script(_ppt_text(2), 4, "격식체")

    assert all(s["script"] == "실제 대본 문장입니다." for s in result["slides"])


def test_slide_label_is_stripped_from_plain_response(monkeypatch):
    def responder(user_prompt):
        return "Slide 1: 라벨이 붙은 대본입니다."

    _stub_hcx(monkeypatch, responder)
    result = _generator().generate_full_script(_ppt_text(2), 4, "격식체")

    assert all(s["script"] == "라벨이 붙은 대본입니다." for s in result["slides"])


def test_single_source_slide_may_expand_into_many(monkeypatch):
    """
    반대로 원본이 브리프 한 덩어리뿐이면(PPT 없이 주제/목차만 받은 프로젝트),
    모델이 여러 슬라이드로 확장하는 것이 정상이므로 합치지 말고 그대로 둔다.
    """

    def responder(user_prompt):
        return "slides[3]{slide_number,script}:\n 1,도입부\n 2,본론\n 3,마무리"

    _stub_hcx(monkeypatch, responder)
    result = _generator().generate_full_script("Slide 1: 주제와 목차 브리프", 5, "격식체")

    assert [s["slide_number"] for s in result["slides"]] == ["1", "2", "3"]


def test_non_slide_input_is_sent_as_is(monkeypatch):
    """주제/목차 브리프처럼 "Slide N:" 형식이 아닌 입력은 나누지 않고 그대로 보낸다."""

    def responder(user_prompt):
        return "slides[1]{slide_number,script}:\n 1,브리프 기반 대본"

    sent = _stub_hcx(monkeypatch, responder)
    result = _generator().generate_full_script("주제: 메타버스\n목차: 개요, 사례", 5, "격식체")

    assert len(sent) == 1
    assert result["slides"][0]["script"] == "브리프 기반 대본"
