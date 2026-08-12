"""발음 듣기 목소리 카탈로그 — 제품 확정(2026-08-12): 남 2 + 여 2, 속도 -5~+5.

화자 코드는 NCP CLOVA Voice tts-premium 문서의 speaker 값(2026-08-12 문서 대조 확인).
여기 없는 화자는 서버가 422로 막는다 — speaker를 자유 입력으로 열어두면 오타·임의 코드가
그대로 과금 호출로 나가고, Clova가 모르는 코드면 합성 실패(502)로만 보여 원인 추적이 어렵다.
"""

# 이름(제품/프론트 표기) → Clova speaker 코드와 성별.
VOICES = {
    "동현": {"speaker": "ndonghyun", "gender": "남성"},
    "대성": {"speaker": "ndaeseong", "gender": "남성"},
    "혜리": {"speaker": "nes_c_hyeri", "gender": "여성"},
    "고은": {"speaker": "ngoeun", "gender": "여성"},
}

# Clova 자체는 -5(2배속)~10(0.5배속)을 받지만 제품은 대칭 범위 -5~+5만 연다(PM 확정).
# 음수 = 빠르게, 양수 = 느리게, 0 = 보통.
SPEED_MIN = -5
SPEED_MAX = 5


def speaker_code(voice_name: str) -> str:
    """카탈로그의 이름을 Clova speaker 코드로 바꾼다. 검증은 요청 모델(Literal)이 이미 했다."""
    return VOICES[voice_name]["speaker"]
