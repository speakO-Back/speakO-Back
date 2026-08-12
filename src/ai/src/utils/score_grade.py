"""100점 만점 점수 → 등급(A/B/C/D/F).

피그마 Feedback Page ㊶: "종합(정확,유창,완성) 점수(A,B,C,D,F) 및 짧은 피드백".
점수 자체는 계속 0~100으로 내려보내고(피그마 Feedback Page도 `87점/100`으로 그린다),
등급은 **함께** 준다. 프론트가 임의 기준으로 변환하면 화면마다 달라질 수 있어서
서버가 한 곳에서 정한다.

경계는 학교 성적의 통상 기준 그대로다(사용자 지정, 2026-08-09):
    A 90 이상 / B 80 이상 / C 70 이상 / D 60 이상 / F 그 미만
"""

# (최소 점수, 등급). 높은 것부터 확인한다.
GRADE_THRESHOLDS = (
    (90, "A"),
    (80, "B"),
    (70, "C"),
    (60, "D"),
)
LOWEST_GRADE = "F"

# 등급이 붙는 점수 항목. Azure가 주는 네 가지 그대로다.
GRADED_SCORE_KEYS = ("accuracy", "fluency", "completeness", "pronunciation_score")


def to_grade(score):
    """0~100 점수를 등급 문자로 바꾼다. 점수가 없으면(None) None.

    100을 넘거나 0 미만인 값은 그대로 경계 비교에 태운다 — Azure가 범위를 벗어난 값을
    주는 일은 없지만, 있더라도 등급 계산이 조용히 틀리는 것보다 낫다(100 초과는 A).
    """
    if score is None:
        return None
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None

    for minimum, grade in GRADE_THRESHOLDS:
        if value >= minimum:
            return grade
    return LOWEST_GRADE


def grades_for(overall_scores):
    """{"accuracy": 93.2, ...} → {"accuracy": "A", ...}.

    값이 없는 항목은 결과에서 뺀다(등급 자리에 null을 넣으면 화면이 'null등급'을 그린다).
    """
    if not overall_scores:
        return {}
    result = {}
    for key in GRADED_SCORE_KEYS:
        grade = to_grade(overall_scores.get(key))
        if grade is not None:
            result[key] = grade
    return result
