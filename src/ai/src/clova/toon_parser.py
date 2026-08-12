import re

# 모델이 실제로 뱉는 헤더/행 변형들:
#   slides[19]{slide_number,script}:   ← 프롬프트대로의 헤더
#   slides{15,script}:                 ← 개수 대괄호를 빠뜨린 변형
#   slides[18]{1,오늘은 ...}.          ← 행마다 헤더를 반복하는 변형
# 어느 쪽이든 "slides[...]{" 접두어만 걷어내면 "번호,대본" 형태만 남는다.
# (접두어부터 닫는 중괄호까지 통째로 지우면 세 번째 변형의 대본 본문이 통째로 날아간다)
_PREFIX_PATTERN = re.compile(r"slides\s*(?:\[\s*\d+\s*\])?\s*\{", re.IGNORECASE)
# 대본이 아니라 포맷 정의 문구가 그대로 넘어온 행을 걸러내기 위한 표식
_FORMAT_LITERALS = ("script", "slide_number", "슬라이드총개수")


def parse_toon_slides(toon_text: str, valid_slide_numbers=None) -> list:
    """
    "slides[N]{slide_number,script}:" 형태의 TOON 응답에서 (slide_number, script) 쌍을 추출한다.
    모델이 프롬프트의 헤더+행 구조를 정확히 지키지 않는 경우가 많아,
    엄격한 헤더 파싱 대신 "숫자,텍스트" 패턴 자체를 관대하게 찾아내는 방식으로 복구한다.

    valid_slide_numbers: 허용할 슬라이드 번호 집합(문자열). 주어지면 여기 없는 번호는 버린다.
    본문 속 연도/금액 같은 숫자가 슬라이드 번호로 오인되는 것을 막는다.
    """
    cleaned = _PREFIX_PATTERN.sub("\n", toon_text)
    # 중괄호는 행 경계로만 쓰인다. 남은 중괄호를 줄바꿈으로 바꿔야 모델이 헤더 없이 뱉는
    # "{2,두 번째 슬라이드..." 같은 행이 앞 행의 대본 안에 섞여 들어가지 않는다.
    cleaned = cleaned.replace("}", "\n").replace("{", "\n")

    pattern = re.compile(r"(\d+)\s*,\s*(.+?)(?=\n\s*\d+\s*,|\Z)", re.DOTALL)

    slides = []
    for num, script in pattern.findall(cleaned):
        script = re.sub(r"\s+", " ", script).strip()
        # 행 구분자였던 중괄호를 지우고 나면 " ." 같은 구두점 찌꺼기가 끝에 남는다.
        # (문장 끝 마침표 "…습니다."는 앞에 공백이 없으므로 영향받지 않는다)
        script = re.sub(r"\s+[.,]+$", "", script).strip()
        if not script:
            continue
        # "15,script): 오늘은..." 처럼 포맷 정의가 데이터 행으로 오인된 경우를 버린다.
        if script.split()[0].strip(":)},").lower() in _FORMAT_LITERALS:
            continue
        if valid_slide_numbers is not None and num.strip() not in valid_slide_numbers:
            continue
        slides.append({"slide_number": num.strip(), "script": script})

    return slides


# 대본 한 장을 평문으로 받았을 때 남는 껍데기들:
#   slides[2]{slide_number,script}:   ← 포맷 헤더만 남고 정작 "번호,"가 없는 경우(실측)
#   Slide 3:                          ← 라벨
#   **강조**                          ← 마크다운. 소리 내어 읽는 대본에는 들어가면 안 된다.
_HEADER_ONLY_PATTERN = re.compile(r"^\s*slides\s*(?:\[\s*\d+\s*\])?\s*\{[^}]*\}\s*:?\s*", re.IGNORECASE)
_LABEL_PATTERN = re.compile(r"^\s*Slide\s*\d*\s*:\s*", re.IGNORECASE)
_MARKDOWN_EMPHASIS = re.compile(r"(\*\*|__|\*|`)")


def clean_script_text(text: str) -> str:
    """
    모델이 돌려준 '한 장짜리 대본'에서 대본 문장만 남긴다.

    평문으로 답하라고 해도 모델은 습관적으로 TOON 껍데기나 "Slide 1:" 라벨, 마크다운 강조를
    붙인다. 이걸 실패로 처리하면 내용이 멀쩡한 대본이 통째로 버려진다(실측: 부분 재생성이
    헤더만 붙었다는 이유로 502로 폐기됐다).
    """
    if not text:
        return ""

    script = text.strip()

    # TOON 행("번호,내용")이 실제로 있으면 그걸 우선 사용한다.
    parsed = parse_toon_slides(script)
    if parsed:
        script = " ".join(item["script"] for item in parsed)
    else:
        # 헤더만 붙고 본문은 평문인 경우 — 헤더만 걷어내고 본문은 그대로 살린다.
        script = _HEADER_ONLY_PATTERN.sub("", script)

    script = _LABEL_PATTERN.sub("", script)
    script = _MARKDOWN_EMPHASIS.sub("", script)
    script = re.sub(r"\s+", " ", script).strip()
    return script
