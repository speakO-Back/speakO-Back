"""발음 듣기(TTS) 엔드포인트 회귀 테스트.

## 이 파일이 지키는 계약 중 제일 중요한 것: **철자가 아니라 발음을 합성한다**

2026-08-09 실측으로 Clova Voice가 한국어 음운 규칙을 *일부만* 적용한다는 게 확인됐다.
네 단어를 철자 그대로 넣어 귀로 들어본 결과:

    역할 → [여칼] ✅ 격음화 적용      권리 → [궐리] ✅ 유음화 적용
    각자 → [각자] ❌ 경음화 미적용    책임 → [책임] ❌ 연음 미적용

즉 철자를 보내면 '각자'·'책임'처럼 **틀린 발음을 정답이라고 들려주게 된다.** 발음 주의 단어
기능의 존재 이유가 거기서 무너지므로, "무엇을 합성했는가"를 눈으로 확인하는 테스트를 둔다.
(오디오 바이트만 보면 이 회귀는 절대 안 잡힌다 — 응답은 어느 쪽이든 그럴듯한 MP3다)

외부 API는 전부 monkeypatch로 막으므로 이 테스트는 과금되지 않는다.
"""
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

import main
from main import app, MAX_TTS_TEXT_LEN
from db import models
from tts import tts_cache, voices
from utils import usage_tracker

client = TestClient(app)

FAKE_MP3 = b"\xff\xfb\x90\x00fake-mp3-bytes"


@pytest.fixture
def synth_spy(monkeypatch):
    """실제 합성 호출을 가로채, 무엇을 몇 번 합성했는지 기록한다."""
    calls = []

    def _fake_synthesize(text, speaker="ndain", speed=0):
        calls.append(text)
        return FAKE_MP3

    monkeypatch.setattr(main.tts_client, "synthesize_bytes", _fake_synthesize)
    return calls


@pytest.fixture
def synth_spy_full(monkeypatch):
    """텍스트만이 아니라 화자·속도까지 기록하는 스파이 — 목소리/속도 설정 테스트용."""
    calls = []

    def _fake_synthesize(text, speaker="ndain", speed=0):
        calls.append({"text": text, "speaker": speaker, "speed": speed})
        return FAKE_MP3

    monkeypatch.setattr(main.tts_client, "synthesize_bytes", _fake_synthesize)
    return calls


def _project_with_word(db_session_factory, word="각자", phoneme="[각짜]"):
    """단어 분석까지 끝난 프로젝트를 만든다 (/api/analysis/words가 남기는 상태와 같은 모양)."""
    db = db_session_factory()
    try:
        project = models.Project(name="TTS 테스트", filename=None, topic=None, keywords=[])
        project.slides = [models.Slide(slide_number=1, source_content="원문", script="각자 책임을 다합시다.")]
        db.add(project)
        db.commit()
        db.refresh(project)
        db.add(models.DifficultWord(
            project_id=project.id, word=word, phoneme=phoneme,
            category="표기-발음불일치", description="경음화",
        ))
        db.commit()
        return project.id
    finally:
        db.close()


# ---------------------------------------------------------------- 무엇을 합성하는가

def test_uses_stored_pronunciation_not_spelling(synth_spy, db_session_factory):
    """저장된 발음기호가 있으면 철자가 아니라 그걸 합성한다. 이 파일의 핵심 계약."""
    project_id = _project_with_word(db_session_factory, word="각자", phoneme="[각짜]")

    response = client.post("/api/tts/word", json={"project_id": project_id, "word": "각자"})

    assert response.status_code == 200
    assert synth_spy == ["각짜"], "철자('각자')를 보내면 경음화가 안 걸린 틀린 소리가 나간다"
    assert unquote(response.headers["X-TTS-Text"]) == "각짜"


def test_falls_back_to_g2p_without_project(synth_spy, monkeypatch):
    """project_id가 없어도 즉석 G2P로 발음을 찾아낸다 (단어 목록 밖에서 부를 때)."""
    monkeypatch.setattr(
        main.g2p_converter, "convert_words",
        lambda words: [{"word": words[0], "phoneme": "[채김]", "is_different": True}],
    )

    response = client.post("/api/tts/word", json={"word": "책임"})

    assert response.status_code == 200
    assert synth_spy == ["채김"]


def test_explicit_pronunciation_wins(synth_spy, db_session_factory):
    """`pronunciation`을 주면 저장값을 제치고 그걸 쓴다.

    이게 '철자로 듣기'를 위해 열어둔 문. 정책이 뒤집히거나 특정 단어만 철자로 들려주고 싶을 때
    서버 코드를 고치지 않고 클라이언트가 선택할 수 있어야 한다.
    """
    project_id = _project_with_word(db_session_factory, word="각자", phoneme="[각짜]")

    response = client.post(
        "/api/tts/word",
        json={"project_id": project_id, "word": "각자", "pronunciation": "각자"},
    )

    assert response.status_code == 200
    assert synth_spy == ["각자"]


def test_strips_brackets_and_length_marks(synth_spy, db_session_factory):
    """'[구ː성]' 같은 표기가 그대로 나가면 안 된다.

    대괄호는 G2P 결과의 포장이고, 장음 기호(ː)는 Clova Voice 평문 입력이 표현할 수 없는
    문자다. 남겨두면 모르는 글자가 그대로 발음에 섞인다.
    """
    project_id = _project_with_word(db_session_factory, word="구성", phoneme="[구ː성]")

    response = client.post("/api/tts/word", json={"project_id": project_id, "word": "구성"})

    assert response.status_code == 200
    assert synth_spy == ["구성"]


def test_returns_audio_bytes_as_mpeg(synth_spy, db_session_factory):
    project_id = _project_with_word(db_session_factory)

    response = client.post("/api/tts/word", json={"project_id": project_id, "word": "각자"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == FAKE_MP3


# ---------------------------------------------------------------- 캐시 (= 과금 방어)

def test_second_call_hits_cache_and_does_not_resynthesize(synth_spy, db_session_factory):
    """같은 단어를 또 눌러도 재합성하지 않는다. Clova Voice는 글자 수만큼 과금된다."""
    project_id = _project_with_word(db_session_factory)
    payload = {"project_id": project_id, "word": "각자"}

    first = client.post("/api/tts/word", json=payload)
    second = client.post("/api/tts/word", json=payload)

    assert first.headers["X-TTS-Cache"] == "miss"
    assert second.headers["X-TTS-Cache"] == "hit"
    assert synth_spy == ["각짜"], "두 번째 호출이 합성을 또 불렀다 = 돈이 두 번 나간다"
    assert second.content == FAKE_MP3


def test_cache_key_separates_different_text(synth_spy, db_session_factory):
    """철자와 발음은 서로 다른 소리이므로 캐시가 섞이면 안 된다."""
    project_id = _project_with_word(db_session_factory, word="각자", phoneme="[각짜]")

    client.post("/api/tts/word", json={"project_id": project_id, "word": "각자"})
    client.post("/api/tts/word", json={"project_id": project_id, "word": "각자", "pronunciation": "각자"})

    assert synth_spy == ["각짜", "각자"]


def test_cache_survives_empty_file(monkeypatch):
    """잘린(0바이트) 캐시 파일을 적중으로 취급하면 무음이 재생된다. 미스로 떨어져야 한다."""
    import os
    os.makedirs(tts_cache.CACHE_DIR, exist_ok=True)
    path = tts_cache._cache_path("각짜", "ndain")
    with open(path, "wb") as f:
        f.write(b"")

    assert tts_cache.get("각짜", "ndain") is None


# ---------------------------------------------------------------- 목소리·속도 설정 (2026-08-12 제품 확정)

def test_each_voice_maps_to_its_clova_speaker(synth_spy_full, db_session_factory):
    """이름("동현")이 정확한 Clova 코드("ndonghyun")로 합성돼야 한다.

    코드가 틀리면 Clova가 합성을 거부해 502로만 보이므로, 매핑을 여기서 못박는다.
    (코드값은 2026-08-12 NCP tts-premium 문서 대조 확인)
    """
    expected = {
        "동현": "ndonghyun",
        "대성": "ndaeseong",
        "혜리": "nes_c_hyeri",
        "고은": "ngoeun",
    }
    project_id = _project_with_word(db_session_factory)

    for voice in expected:
        client.post("/api/tts/word", json={"project_id": project_id, "word": "각자", "voice": voice})

    assert [c["speaker"] for c in synth_spy_full] == list(expected.values())


def test_speed_is_passed_through(synth_spy_full, db_session_factory):
    project_id = _project_with_word(db_session_factory)

    response = client.post(
        "/api/tts/word",
        json={"project_id": project_id, "word": "각자", "voice": "고은", "speed": -3},
    )

    assert response.status_code == 200
    assert synth_spy_full == [{"text": "각짜", "speaker": "ngoeun", "speed": -3}]


def test_omitting_voice_keeps_default_speaker(synth_spy_full, db_session_factory):
    """설정 UI가 없는 기존(배포된) 프론트가 이 API를 그대로 써도 동작이 바뀌면 안 된다."""
    project_id = _project_with_word(db_session_factory)

    client.post("/api/tts/word", json={"project_id": project_id, "word": "각자"})

    assert synth_spy_full == [{"text": "각짜", "speaker": main.TTS_SPEAKER, "speed": 0}]


def test_unknown_voice_rejected(db_session_factory):
    """카탈로그 밖 화자는 422 — 자유 입력을 열어두면 오타·임의 코드가 그대로 과금 호출로 나간다."""
    response = client.post("/api/tts/word", json={"word": "각자", "voice": "철수"})
    assert response.status_code == 422


@pytest.mark.parametrize("speed", [-6, 6])
def test_speed_out_of_range_rejected(speed):
    """제품 범위는 -5~+5. Clova 자체는 10까지 받지만 제품이 정한 범위 밖은 막는다."""
    response = client.post("/api/tts/word", json={"word": "각자", "speed": speed})
    assert response.status_code == 422


def test_voice_literal_matches_catalog():
    """요청 모델의 Literal과 카탈로그(VOICES)가 어긋나면, 목록 API에는 나오는데
    정작 합성 요청은 422가 나는(또는 그 반대) 반쪽 화자가 생긴다."""
    from typing import get_args
    literal = main.TtsWordRequest.model_fields["voice"].annotation
    allowed = set(get_args(get_args(literal)[0]))  # Optional[Literal[...]] → Literal 값들
    assert allowed == set(voices.VOICES.keys())


def test_cache_separates_voice_and_speed(synth_spy_full, db_session_factory):
    """같은 단어라도 목소리·속도가 다르면 다른 소리다 — 캐시가 섞이면
    '고은 -5'를 고른 사용자가 '동현 +5' 오디오를 듣는다."""
    project_id = _project_with_word(db_session_factory)
    base = {"project_id": project_id, "word": "각자"}

    client.post("/api/tts/word", json={**base, "voice": "동현"})
    client.post("/api/tts/word", json={**base, "voice": "고은"})            # 화자 다름 → 재합성
    client.post("/api/tts/word", json={**base, "voice": "고은", "speed": 3})  # 속도 다름 → 재합성
    repeat = client.post("/api/tts/word", json={**base, "voice": "고은", "speed": 3})  # 완전 동일 → 캐시

    assert len(synth_spy_full) == 3
    assert repeat.headers["X-TTS-Cache"] == "hit"


def test_voices_endpoint_lists_catalog():
    """프론트 설정 화면이 그릴 목록. 이름·성별과 속도 범위가 그대로 내려가야 한다."""
    response = client.get("/api/tts/voices")

    assert response.status_code == 200
    body = response.json()
    assert {v["name"] for v in body["voices"]} == {"동현", "대성", "혜리", "고은"}
    assert {v["gender"] for v in body["voices"]} == {"남성", "여성"}
    assert body["speed"] == {"min": -5, "max": 5, "default": 0}


# ---------------------------------------------------------------- 실패·상한

def test_synthesis_failure_returns_502(monkeypatch, db_session_factory):
    project_id = _project_with_word(db_session_factory)
    monkeypatch.setattr(main.tts_client, "synthesize_bytes", lambda text, speaker="ndain", speed=0: None)

    response = client.post("/api/tts/word", json={"project_id": project_id, "word": "각자"})

    assert response.status_code == 502


def test_failed_synthesis_is_not_cached(monkeypatch, db_session_factory):
    """실패를 캐시하면 그 단어는 영원히 안 들린다."""
    project_id = _project_with_word(db_session_factory)
    monkeypatch.setattr(main.tts_client, "synthesize_bytes", lambda text, speaker="ndain", speed=0: None)
    client.post("/api/tts/word", json={"project_id": project_id, "word": "각자"})

    assert tts_cache.get("각짜", main.TTS_SPEAKER) is None


def test_rejects_text_over_limit():
    """Clova Voice는 글자 수만큼 과금되므로 길이 상한이 곧 호출 1회당 비용 상한이다."""
    response = client.post("/api/tts/word", json={"word": "가" * (MAX_TTS_TEXT_LEN + 1)})
    assert response.status_code == 422


def test_rejects_blank_word():
    response = client.post("/api/tts/word", json={"word": "   "})
    assert response.status_code == 422


def test_korean_word_does_not_break_headers(synth_spy, db_session_factory):
    """헤더는 latin-1만 담을 수 있어서, 한글을 그대로 넣으면 응답 자체가 깨진다."""
    project_id = _project_with_word(db_session_factory)

    response = client.post("/api/tts/word", json={"project_id": project_id, "word": "각자"})

    assert response.status_code == 200
    assert "각자" in unquote(response.headers["Content-Disposition"])


# ---------------------------------------------------------------- 단가 (NCP 콘솔 공식)

def test_overage_matches_ncp_formula():
    """콘솔 문서의 예시를 그대로 검산한다.

        {(2,000,000 − 1,000,000) / 1,000} × 100원 = 100,000원
        월 이용 요금 = 90,000원 + 100,000원 = 190,000원
    """
    assert usage_tracker.clova_voice_overage_krw(2_000_000) == 100_000
    assert usage_tracker.clova_voice_monthly_krw(2_000_000) == 190_000


def test_no_overage_within_free_tier():
    """100만 자까지는 기본료에 포함이라 추가 비용이 붙지 않는다."""
    assert usage_tracker.clova_voice_overage_krw(1_000_000) == 0
    assert usage_tracker.clova_voice_monthly_krw(999) == 90_000
