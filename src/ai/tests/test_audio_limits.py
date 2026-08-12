"""녹음 업로드 상한(크기·길이)과 Azure 인식 타임아웃의 정합성 회귀 테스트.

## 왜 크기 상한만으로는 부족한가
같은 20MB라도 비트레이트에 따라 재생 길이가 3배 넘게 벌어진다(실측: 124kbps m4a는 21분,
브라우저 webm/opus 40kbps는 67분). 그런데 Azure 발음 평가는 **오디오 길이에 비례**해서
시간을 먹는다(실측 2026-08-09: 5분 녹음 → 148초, 실시간의 약 0.48배). 크기로만 막으면
처리 시간이 사실상 무제한이 된다.

## 여기서 고정하는 계약 세 가지
1. 길이 상한을 넘는 파일은 **Azure를 부르기 전에** 거절한다 (안 그러면 돈은 다 쓰고 거절한다)
2. 길이를 못 재는 파일은 통과시킨다 (ffprobe가 모르는 정상 파일을 막으면 안 된다)
3. 인식이 타임아웃되면 **조용히 부분 결과를 돌려주지 않는다** — 이게 제일 중요하다.
   예전엔 `done.wait()`의 반환값을 버려서, 20분 녹음의 앞부분만 처리하고도 정상 완료와
   구분이 안 됐다. 성공처럼 보이는 실패라 오디오 바이트만 봐서는 절대 안 잡힌다.
"""
import io

import pytest
from fastapi.testclient import TestClient

import main
from main import app, MAX_AUDIO_DURATION_SECONDS, MAX_AUDIO_SIZE_BYTES
from azure_speech import azure_client
from db import models
from utils import audio_converter

client = TestClient(app)

# Azure가 오디오를 실시간의 몇 배 속도로 처리하는지 (2026-08-09 실측: 304.3초 → 약 147초)
MEASURED_PROCESSING_RATIO = 0.48


def _project(db_session_factory, script="안녕하세요. 발음 평가 테스트입니다."):
    db = db_session_factory()
    try:
        project = models.Project(name="오디오 상한 테스트", filename=None, topic=None, keywords=[])
        project.slides = [models.Slide(slide_number=1, source_content="원문", script=script)]
        db.add(project)
        db.commit()
        db.refresh(project)
        return project.id
    finally:
        db.close()


def _post_audio(project_id, name="recording.m4a", data=b"fake audio bytes"):
    return client.post(
        "/api/evaluation/audio",
        data={"project_id": str(project_id)},
        files={"audio_file": (name, io.BytesIO(data), "audio/mp4")},
    )


@pytest.fixture
def azure_spy(monkeypatch):
    """Azure가 실제로 불렸는지 기록한다. 길이 초과는 여기 닿기 전에 끊겨야 한다."""
    calls = []

    def _fake(audio_file_path, reference_text):
        calls.append(audio_file_path)
        return {
            "status": "success",
            "overall_scores": {"accuracy": 90.0, "fluency": 90.0,
                               "completeness": 90.0, "pronunciation_score": 90.0},
            "recognized_text": "안녕하세요",
            "words_detail": [],
        }

    monkeypatch.setattr(main.azure_evaluator, "evaluate_audio", _fake)
    monkeypatch.setattr(main.audio_converter, "convert_to_wav",
                        lambda i, o: (open(o, "wb").write(b"wav") or True))
    return calls


# ---------------------------------------------------------------- 길이 상한

def test_too_long_recording_is_rejected_before_azure(monkeypatch, azure_spy, db_session_factory):
    """길이 초과는 Azure 호출 **전에** 끊어야 한다. 나중에 끊으면 비용이 이미 다 나간 뒤다."""
    project_id = _project(db_session_factory)
    monkeypatch.setattr(main.audio_converter, "probe_duration_seconds",
                        lambda path: MAX_AUDIO_DURATION_SECONDS + 1)

    response = _post_audio(project_id)

    assert response.status_code == 422
    assert azure_spy == [], "길이 초과인데 Azure를 불렀다 = 돈이 나갔다"
    detail = response.json()["detail"]
    assert "너무 깁니다" in detail
    assert "슬라이드별로 나눠" in detail, "무엇을 하면 되는지 알려줘야 한다"


def test_recording_at_limit_is_accepted(monkeypatch, azure_spy, db_session_factory):
    """경계값은 통과해야 한다 (상한 '이하'까지 허용)."""
    project_id = _project(db_session_factory)
    monkeypatch.setattr(main.audio_converter, "probe_duration_seconds",
                        lambda path: MAX_AUDIO_DURATION_SECONDS)

    assert _post_audio(project_id).status_code == 200
    assert len(azure_spy) == 1


def test_unknown_duration_passes_through(monkeypatch, azure_spy, db_session_factory):
    """길이를 못 재는 건 '길다'는 뜻이 아니다. 여기서 막으면 정상 파일이 통째로 거절된다."""
    project_id = _project(db_session_factory)
    monkeypatch.setattr(main.audio_converter, "probe_duration_seconds", lambda path: None)

    assert _post_audio(project_id).status_code == 200
    assert len(azure_spy) == 1


def test_probe_failure_returns_none(tmp_path):
    """ffprobe가 읽을 수 없는 파일은 None. 예외를 던지면 업로드 전체가 500이 된다."""
    broken = tmp_path / "broken.m4a"
    broken.write_bytes(b"not actually audio")
    assert audio_converter.probe_duration_seconds(str(broken)) is None


# ---------------------------------------------------------------- 크기 상한

def test_oversized_audio_message_tells_user_what_to_do(monkeypatch, db_session_factory):
    """크기만 알려주면 사용자는 파일을 줄일 방법을 스스로 찾아야 한다."""
    project_id = _project(db_session_factory)
    monkeypatch.setattr(main, "MAX_AUDIO_SIZE_BYTES", 10)

    response = _post_audio(project_id, data=b"x" * 100)

    assert response.status_code == 413
    assert "슬라이드별로 나눠" in response.json()["detail"]


def test_audio_size_limit_fits_under_body_limit():
    """본문 상한보다 크면 파일 검사에 닿기도 전에 미들웨어가 413으로 끊는다.
    그러면 위의 안내 문구가 사용자에게 영영 안 보인다."""
    assert MAX_AUDIO_SIZE_BYTES < main.MAX_REQUEST_BODY_BYTES


# ---------------------------------------------------------------- 타임아웃 정합성

def test_recognition_timeout_covers_max_duration():
    """길이 상한과 인식 타임아웃은 한 쌍이다. 한쪽만 바꾸면 조용히 잘리는 구간이 생긴다."""
    needed = MAX_AUDIO_DURATION_SECONDS * MEASURED_PROCESSING_RATIO
    assert azure_client.CONTINUOUS_RECOGNITION_TIMEOUT_SECONDS >= needed, (
        f"길이 상한 {MAX_AUDIO_DURATION_SECONDS}초를 처리하려면 약 {needed:.0f}초가 필요한데 "
        f"타임아웃이 {azure_client.CONTINUOUS_RECOGNITION_TIMEOUT_SECONDS}초다"
    )


class _FakeSignal:
    """SDK의 이벤트 훅. 등록된 핸들러를 나중에 직접 부를 수 있게 들고 있는다."""
    def __init__(self):
        self.handlers = []

    def connect(self, handler):
        self.handlers.append(handler)

    def fire(self, evt=None):
        for handler in self.handlers:
            handler(evt)


class _FakeRecognizer:
    """기본은 콜백을 한 번도 안 부른다 = Azure 응답이 안 오는 상황.
    `stops_immediately=True`면 세션 종료 콜백을 즉시 쏴서 정상 완료를 흉내낸다."""
    stops_immediately = False

    def __init__(self, *args, **kwargs):
        self.recognized = _FakeSignal()
        self.canceled = _FakeSignal()
        self.session_stopped = _FakeSignal()

    def start_continuous_recognition(self):
        if self.stops_immediately:
            self.session_stopped.fire()

    def stop_continuous_recognition(self):
        pass


class _Settable:
    """speech_recognition_language 같은 속성을 그냥 받아주는 자리."""
    pass


class _FakeSpeechSdk:
    class audio:
        @staticmethod
        def AudioConfig(**kwargs):
            return _Settable()

    @staticmethod
    def SpeechConfig(**kwargs):
        return _Settable()

    class PronunciationAssessmentGradingSystem:
        HundredMark = object()

    class PronunciationAssessmentGranularity:
        Word = object()

    class ResultReason:
        RecognizedSpeech = object()

    class CancellationReason:
        Error = object()

    @staticmethod
    def PronunciationAssessmentConfig(**kwargs):
        class _Cfg:
            def apply_to(self, recognizer):
                pass
        return _Cfg()

    SpeechRecognizer = _FakeRecognizer


def _evaluator(monkeypatch, sdk):
    monkeypatch.setattr(azure_client, "speechsdk", sdk)
    monkeypatch.setattr(azure_client, "CONTINUOUS_RECOGNITION_TIMEOUT_SECONDS", 0.05)
    evaluator = azure_client.PronunciationEvaluator()
    monkeypatch.setattr(evaluator, "use_fallback", False)
    evaluator.speech_key, evaluator.service_region = "key", "koreacentral"
    return evaluator


def test_timeout_does_not_return_a_partial_success(monkeypatch, tmp_path):
    """⚠️ 이 파일의 핵심. 타임아웃인데 status:success를 돌려주면, 뒷부분이 통째로 잘린 채
    정상 점수처럼 화면에 뜬다. 게다가 완성도는 대본 전체 기준이라 사용자는 "뒤를 안 읽었다"는
    낮은 점수를 받는다 — 실제로는 서버가 안 들은 것이다."""
    evaluator = _evaluator(monkeypatch, _FakeSpeechSdk)

    result = evaluator.evaluate_audio(str(tmp_path / "nope.wav"), "안녕하세요")

    assert result["status"] == "error", "타임아웃이 성공으로 둔갑했다"
    assert "시간이 초과" in result["message"]


def test_completed_recognition_is_not_flagged_as_timeout(monkeypatch, tmp_path):
    """정상 완료가 타임아웃으로 오인되면 안 된다 (위 수정의 반대 방향 회귀)."""
    class _StoppingRecognizer(_FakeRecognizer):
        stops_immediately = True

    class _Sdk(_FakeSpeechSdk):
        SpeechRecognizer = _StoppingRecognizer

    evaluator = _evaluator(monkeypatch, _Sdk)

    result = evaluator.evaluate_audio(str(tmp_path / "nope.wav"), "안녕하세요")

    # 인식 결과가 하나도 없으므로 "음성을 인식할 수 없습니다"로 끝나야 한다.
    # 중요한 건 **타임아웃 메시지가 아니라는 것** — 정상 완료 경로를 탔다는 뜻이다.
    assert result["status"] == "error"
    assert "시간이 초과" not in result["message"]
