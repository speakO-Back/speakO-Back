"""
TOON 파서 회귀 테스트.

여기 있는 입력들은 전부 HCX-005가 실제로 뱉은 응답에서 가져온 것이다.
모델은 프롬프트의 출력 포맷을 자주 어기는데, 그 변형들이 조용히 오해석되면
"슬라이드 19장 중 1장만 생성"처럼 대본이 통째로 유실된다.
"""

from clova.toon_parser import parse_toon_slides


def test_parses_documented_format():
    toon = "slides[2]{slide_number,script}:\n 1,첫 번째 슬라이드입니다.\n 2,두 번째 슬라이드입니다."

    slides = parse_toon_slides(toon)

    assert [s["slide_number"] for s in slides] == ["1", "2"]
    assert slides[0]["script"] == "첫 번째 슬라이드입니다."


def test_parses_header_repeated_on_every_row():
    """실측 변형: 모델이 행마다 헤더를 반복한다. 헤더를 통째로 지우면 대본이 날아간다."""
    toon = "slides[18]{1,오늘은 AHP를 알아보겠습니다.}.\nslides[18]{2,먼저 기본 함수를 보겠습니다.}."

    slides = parse_toon_slides(toon)

    assert [s["slide_number"] for s in slides] == ["1", "2"]
    assert slides[0]["script"] == "오늘은 AHP를 알아보겠습니다."
    assert slides[1]["script"] == "먼저 기본 함수를 보겠습니다."


def test_header_without_brackets_is_not_a_slide():
    """
    실측 사고: 모델이 "slides{15,script}:" 뒤에 19장 분량을 줄글로 써버렸는데,
    파서가 이 헤더를 "15번 슬라이드의 대본"으로 오인해 통과시켰다.
    그 결과 19장짜리 발표가 15번 한 장짜리 대본으로 저장됐다.
    """
    toon = "slides{15,script}:  \n오늘은 계층분석과정의 장단점에 대해 알아보겠습니다."

    assert parse_toon_slides(toon) == []


def test_brace_row_is_split_not_absorbed():
    """헤더 없이 "{2,..."로 이어붙는 행이 앞 슬라이드 대본에 섞이면 안 된다."""
    toon = "slides[1]{1,목차를 소개하겠습니다. {2,첫 번째로 개념을 보겠습니다."

    slides = parse_toon_slides(toon)

    assert [s["slide_number"] for s in slides] == ["1", "2"]
    assert "{2," not in slides[0]["script"]
    assert slides[0]["script"] == "목차를 소개하겠습니다."


def test_valid_slide_numbers_rejects_body_numbers():
    """본문 속 연도/금액이 슬라이드 번호로 오인되지 않아야 한다."""
    toon = "slides[2]{slide_number,script}:\n 1,첫 슬라이드입니다.\n 2024,매출이 늘었습니다."

    slides = parse_toon_slides(toon, valid_slide_numbers={"1", "2"})

    assert [s["slide_number"] for s in slides] == ["1"]


def test_empty_and_unparsable_input():
    assert parse_toon_slides("") == []
    assert parse_toon_slides("죄송합니다. 요청을 처리할 수 없습니다.") == []


# ── 한 장짜리 대본 정리 (clean_script_text) ──────────────────────────────────
# 실측: 부분 재생성에서 모델이 TOON 헤더만 붙이고 본문은 평문으로 써서
# ("slides[2]{slide_number,script}:" + 줄바꿈 + 대본), 파서가 빈 결과를 내고
# 내용이 멀쩡한 대본이 502로 폐기됐다.

from clova.toon_parser import clean_script_text


def test_clean_script_keeps_body_when_only_header_present():
    raw = "slides[2]{slide_number,script}: \n자 그럼 이제 발표를 시작해볼게요! 집중해서 들어보세요."
    assert clean_script_text(raw) == "자 그럼 이제 발표를 시작해볼게요! 집중해서 들어보세요."


def test_clean_script_uses_toon_row_when_present():
    raw = "slides[1]{slide_number,script}:\n 3,시장 규모를 살펴보겠습니다."
    assert clean_script_text(raw) == "시장 규모를 살펴보겠습니다."


def test_clean_script_strips_slide_label():
    assert clean_script_text("Slide 3: 다음 내용입니다.") == "다음 내용입니다."


def test_clean_script_strips_markdown_emphasis():
    """소리 내어 읽는 대본에 마크다운 기호가 남으면 안 된다."""
    assert clean_script_text("첫 번째 주제인 '**발표**'에 대해 말씀드립니다.") == "첫 번째 주제인 '발표'에 대해 말씀드립니다."


def test_clean_script_collapses_whitespace_and_handles_empty():
    assert clean_script_text("  여러   줄\n\n대본입니다.  ") == "여러 줄 대본입니다."
    assert clean_script_text("") == ""
    assert clean_script_text(None) == ""
