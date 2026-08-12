"""블로킹 호출이 이벤트 루프에서 벗어나 도는지 검증한다.

왜 이런 방식인가: 오프로드를 되돌려도(= `run_in_threadpool`을 지워도) 기능 테스트는 전부
그대로 통과한다. 응답 내용이 같기 때문이다. 그래서 "어느 스레드에서 돌았는가"를 직접 본다.

판정 방법: 블로킹 함수 안에서 `asyncio.get_running_loop()`를 불러본다.
  - 성공 = 이벤트 루프 스레드에서 실행됨 → 그 요청이 도는 동안 서버 전체가 멈춘다 (실패)
  - RuntimeError = 루프 밖 워커 스레드에서 실행됨 → 정상

FastAPI는 `async def` 라우트를 스레드풀로 자동 오프로드해주지 않으므로, 핸들러가
명시적으로 감싸지 않으면 이 테스트가 깨진다.
"""
import asyncio
import io

import pytest
from fastapi.testclient import TestClient

import main
from main import app
from db import models

client = TestClient(app)


def _where_did_it_run(record, key):
    """호출된 스레드가 이벤트 루프인지 기록하는 헬퍼를 만든다."""
    def note():
        try:
            asyncio.get_running_loop()
            record[key] = "event-loop"
        except RuntimeError:
            record[key] = "worker-thread"
    return note


def _create_project(db_session_factory, script="안녕하세요. 국물과 신라의 발음을 연습합니다."):
    db = db_session_factory()
    try:
        project = models.Project(name="오프로드 테스트", filename=None, topic=None, keywords=[])
        project.slides = [models.Slide(slide_number=1, source_content="원문", script=script)]
        db.add(project)
        db.commit()
        db.refresh(project)
        return project.id
    finally:
        db.close()


def test_word_analysis_runs_off_the_event_loop(monkeypatch, db_session_factory):
    """단어 분석은 형태소 분석(CPU) + 표준국어대사전 조회(단어당 HTTP 1회)라 가장 오래 잡는다."""
    project_id = _create_project(db_session_factory)
    record = {}
    note = _where_did_it_run(record, "analysis")

    def _fake_analyze(script_text):
        note()
        return [{"word": "국물", "phoneme": "[궁물]", "is_different": True, "category": "표기-발음불일치"}], \
               {"장단음": 0, "연음": 0, "표기-발음불일치": 1}

    monkeypatch.setattr(main, "_analyze_difficult_words", _fake_analyze)

    response = client.post("/api/analysis/words", json={"project_id": project_id})
    assert response.status_code == 200
    assert record["analysis"] == "worker-thread"


def test_ffmpeg_conversion_runs_off_the_event_loop(monkeypatch, db_session_factory):
    """ffmpeg는 subprocess.run으로 완료까지 블로킹한다 — 루프에서 돌리면 서버가 멈춘 것처럼 보인다."""
    project_id = _create_project(db_session_factory, script="테스트 문장입니다.")
    record = {}
    note = _where_did_it_run(record, "ffmpeg")

    def _fake_convert(input_path, output_path):
        note()
        with open(output_path, "wb") as f:
            f.write(b"fake wav bytes")
        return True

    monkeypatch.setattr(main.audio_converter, "convert_to_wav", _fake_convert)
    monkeypatch.setattr(
        main.azure_evaluator,
        "evaluate_audio",
        lambda audio_file_path, reference_text: {
            "status": "success",
            "overall_scores": {"accuracy": 88.0, "fluency": 88.0, "completeness": 88.0, "pronunciation_score": 88.0},
            "words_detail": [],
        },
    )

    response = client.post(
        "/api/evaluation/audio",
        data={"project_id": str(project_id)},
        files={"audio_file": ("recording.webm", io.BytesIO(b"fake webm bytes"), "audio/webm")},
    )
    assert response.status_code == 200
    assert record["ffmpeg"] == "worker-thread"


def test_slide_extraction_runs_off_the_event_loop(monkeypatch):
    """PPTX 추출은 이미지 전용 장표를 만나면 HCX 비전을 유료로 호출한다(네트워크 왕복 여러 번)."""
    record = {}
    note = _where_did_it_run(record, "extract")

    def _fake_extract(file_path, topic_hint="", outline_hint=""):
        note()
        return {
            "metadata": {"topic": "테스트 주제", "keywords": []},
            "slides": [{"slide_number": 1, "content": "슬라이드 내용"}],
        }

    monkeypatch.setattr(main.ppt_extractor, "extract_structured_data", _fake_extract)

    response = client.post(
        "/api/projects",
        files={"file": ("deck.pptx", io.BytesIO(b"fake pptx bytes"),
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
    )
    assert response.status_code == 200
    assert record["extract"] == "worker-thread"


def test_coaching_text_extraction_runs_off_the_event_loop(monkeypatch):
    record = {}
    note = _where_did_it_run(record, "coaching")

    real_extract = main._extract_coaching_text

    def _fake_extract(temp_file_path, ext):
        note()
        return real_extract(temp_file_path, ext)

    monkeypatch.setattr(main, "_extract_coaching_text", _fake_extract)

    response = client.post(
        "/api/projects",
        data={"mode": "coaching"},
        files={"file": ("script.txt", io.BytesIO("발표 대본입니다.".encode("utf-8")), "text/plain")},
    )
    assert response.status_code == 200
    assert record["coaching"] == "worker-thread"


def test_tts_synthesis_runs_off_the_event_loop(monkeypatch, db_session_factory):
    """Clova Voice 합성은 requests.post로 최대 30초까지 블로킹한다(REQUEST_TIMEOUT_SECONDS)."""
    db = db_session_factory()
    try:
        project = models.Project(name="TTS 오프로드", filename=None, topic=None, keywords=[])
        project.slides = [models.Slide(slide_number=1, source_content="원문", script="각자 책임을 다합시다.")]
        db.add(project)
        db.commit()
        db.refresh(project)
        project_id = project.id
        db.add(models.DifficultWord(project_id=project_id, word="각자", phoneme="[각짜]",
                                    category="표기-발음불일치", description="경음화"))
        db.commit()
    finally:
        db.close()

    record = {}
    note = _where_did_it_run(record, "tts")

    def _fake_synthesize(text, speaker="ndain", speed=0):
        note()
        return b"fake mp3 bytes"

    monkeypatch.setattr(main.tts_client, "synthesize_bytes", _fake_synthesize)

    response = client.post("/api/tts/word", json={"project_id": project_id, "word": "각자"})
    assert response.status_code == 200
    assert record["tts"] == "worker-thread"


@pytest.mark.parametrize("handler_name", [
    "create_project",
    "extract_and_convert_words",
    "evaluate_pronunciation",
    "create_full_script",
    "create_partial_script",
    "synthesize_word_pronunciation",
])
def test_blocking_handlers_stay_async(handler_name):
    """이 핸들러들이 `def`로 바뀌면 FastAPI가 통째로 스레드풀에 넣어버려서
    위 오프로드 검증이 무의미해진다(항상 통과). 계약을 명시적으로 고정한다."""
    handler = getattr(main, handler_name)
    assert asyncio.iscoroutinefunction(handler), f"{handler_name}이 async def가 아닙니다"
