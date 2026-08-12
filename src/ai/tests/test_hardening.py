"""배포 방어 장치 회귀 테스트 — 본문 크기 상한 / 슬라이드 개수 상한 / 레이트 리밋 / job_store.

전부 "로컬에선 안 터지고 배포하면 터지는" 종류라, 실제로 동작하는지 여기서 고정한다.
"""
import asyncio
import io

from fastapi.testclient import TestClient

import main
from main import app, MAX_SLIDES_PER_PROJECT
from utils import job_store, rate_limit
from utils.body_limit import MaxBodySizeMiddleware
from db import models

client = TestClient(app)


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------- 요청 본문 크기 상한

def _call_body_limit(max_bytes, headers, body_chunks):
    """미들웨어를 직접 돌리고 (앱이 호출됐는지, 보낸 메시지들)을 돌려준다."""
    app_calls = []
    messages = []

    async def fake_app(scope, receive, send):
        app_calls.append(scope)
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            if not message.get("more_body"):
                break

    async def send(message):
        messages.append(message)

    chunks = list(body_chunks)

    async def receive():
        if chunks:
            chunk = chunks.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}
        return {"type": "http.request", "body": b"", "more_body": False}

    middleware = MaxBodySizeMiddleware(fake_app, max_bytes=max_bytes)
    scope = {"type": "http", "method": "POST", "path": "/api/projects", "headers": headers}
    _run(middleware(scope, receive, send))
    return app_calls, messages


def test_oversized_content_length_is_rejected_before_reaching_the_app():
    """핵심: 본문을 한 바이트도 받기 전에 끊어야 한다. 앱까지 가면 이미 파싱된 뒤라 늦다."""
    app_calls, messages = _call_body_limit(
        max_bytes=1000, headers=[(b"content-length", b"5000")], body_chunks=[b"x" * 5000]
    )
    assert app_calls == []  # 앱이 아예 호출되지 않아야 한다
    assert messages[0]["status"] == 413
    assert "너무 큽니다" in messages[1]["body"].decode("utf-8")


def test_body_within_limit_passes_through():
    app_calls, messages = _call_body_limit(
        max_bytes=1000, headers=[(b"content-length", b"10")], body_chunks=[b"x" * 10]
    )
    assert len(app_calls) == 1
    assert messages == []  # 미들웨어가 응답을 가로채지 않는다


def test_actual_bytes_are_counted_when_content_length_is_absent():
    """Content-Length가 없거나(chunked) 거짓일 수 있으므로 실제 흘러온 양도 세야 한다."""
    received = []

    async def fake_app(scope, receive, send):
        while True:
            message = await receive()
            received.append(message["type"])
            if message["type"] != "http.request" or not message.get("more_body"):
                break

    chunks = [b"x" * 600, b"x" * 600]

    async def receive():
        if chunks:
            return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    middleware = MaxBodySizeMiddleware(fake_app, max_bytes=1000)
    _run(middleware({"type": "http", "method": "POST", "path": "/api/projects", "headers": []},
                    receive, send))

    # 첫 청크는 통과(600 < 1000), 두 번째에서 상한을 넘으므로 연결을 끊는다.
    assert received == ["http.request", "http.disconnect"]


def test_cors_middleware_is_outermost():
    """미들웨어는 나중에 추가한 것이 바깥이다. CORS가 가장 바깥이 아니면 413/429 응답에
    CORS 헤더가 안 붙어서, 배포 프론트에선 원인 모를 '네트워크 오류'로만 보인다."""
    names = [m.cls.__name__ for m in app.user_middleware]
    assert names[0] == "CORSMiddleware", f"CORS가 가장 바깥이 아닙니다: {names}"
    assert "RateLimitMiddleware" in names
    assert "MaxBodySizeMiddleware" in names


# ----------------------------------------------------------- 슬라이드 개수 상한

def _fake_slides(count):
    return {
        "metadata": {"topic": "주제", "keywords": []},
        "slides": [{"slide_number": i, "content": f"{i}번 내용"} for i in range(1, count + 1)],
    }


def test_too_many_slides_is_rejected(monkeypatch):
    """대본 생성은 슬라이드 한 장당 HCX 호출 1회다. 길이 상한은 개수를 못 막는다."""
    monkeypatch.setattr(
        main.ppt_extractor, "extract_structured_data",
        lambda path, topic_hint="", outline_hint="": _fake_slides(MAX_SLIDES_PER_PROJECT + 1),
    )
    response = client.post(
        "/api/projects",
        files={"file": ("deck.pptx", io.BytesIO(b"fake"), "application/vnd.ms-powerpoint")},
    )
    assert response.status_code == 413
    assert "슬라이드가 너무 많습니다" in response.json()["detail"]


def test_slide_count_at_limit_is_accepted(monkeypatch):
    monkeypatch.setattr(
        main.ppt_extractor, "extract_structured_data",
        lambda path, topic_hint="", outline_hint="": _fake_slides(MAX_SLIDES_PER_PROJECT),
    )
    response = client.post(
        "/api/projects",
        files={"file": ("deck.pptx", io.BytesIO(b"fake"), "application/vnd.ms-powerpoint")},
    )
    assert response.status_code == 200


# ---------------------------------------------------------------- 레이트 리밋

def test_expensive_endpoint_is_limited(monkeypatch):
    monkeypatch.setattr(rate_limit.limiter, "expensive_per_minute", 3)

    codes = [
        client.post("/api/analysis/words", json={"project_id": 999999}).status_code
        for _ in range(5)
    ]
    assert codes[:3] == [404, 404, 404]  # 상한까지는 정상 처리(대상이 없어 404)
    assert codes[3:] == [429, 429]


def test_rate_limited_response_tells_the_client_when_to_retry(monkeypatch):
    monkeypatch.setattr(rate_limit.limiter, "expensive_per_minute", 1)
    client.post("/api/analysis/words", json={"project_id": 999999})
    blocked = client.post("/api/analysis/words", json={"project_id": 999999})

    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1
    assert "다시 시도" in blocked.json()["detail"]


def test_polling_is_not_throttled_by_the_expensive_budget(monkeypatch):
    """프론트는 생성 상태를 1~2초마다 폴링한다. 이게 유료 등급에 걸리면 정상 흐름이 깨진다."""
    monkeypatch.setattr(rate_limit.limiter, "expensive_per_minute", 1)
    monkeypatch.setattr(rate_limit.limiter, "default_per_minute", 100)

    codes = [client.get("/api/script/jobs/none-such").status_code for _ in range(10)]
    assert codes == [404] * 10  # 전부 통과(없는 작업이라 404), 429 없음


def test_cors_preflight_is_never_throttled(monkeypatch):
    """preflight가 막히면 브라우저가 본 요청을 아예 못 보낸다."""
    monkeypatch.setattr(rate_limit.limiter, "default_per_minute", 1)
    monkeypatch.setattr(rate_limit.limiter, "expensive_per_minute", 1)

    for _ in range(5):
        response = client.options(
            "/api/projects",
            headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
        )
        assert response.status_code != 429


def test_limits_are_tracked_per_client():
    limiter = rate_limit.RateLimiter(default_per_minute=1, expensive_per_minute=1)
    assert limiter.check("1.1.1.1", "expensive", now=0.0)[0] is True
    assert limiter.check("1.1.1.1", "expensive", now=0.0)[0] is False
    # 다른 클라이언트는 영향받지 않아야 한다.
    assert limiter.check("2.2.2.2", "expensive", now=0.0)[0] is True


def test_budget_resets_in_the_next_window():
    limiter = rate_limit.RateLimiter(default_per_minute=1, expensive_per_minute=1, window_seconds=60)
    assert limiter.check("1.1.1.1", "expensive", now=0.0)[0] is True
    assert limiter.check("1.1.1.1", "expensive", now=30.0)[0] is False
    assert limiter.check("1.1.1.1", "expensive", now=60.0)[0] is True


def test_forwarded_for_identifies_the_real_client():
    """스프링을 거치면 소켓 주소가 전부 스프링 한 대가 된다. XFF가 있으면 그걸 우선한다."""
    scope = {"headers": [(b"x-forwarded-for", b"203.0.113.9, 10.0.0.1")], "client": ("10.0.0.1", 1234)}
    assert rate_limit._client_id(scope) == "203.0.113.9"
    assert rate_limit._client_id({"headers": [], "client": ("10.0.0.1", 1234)}) == "10.0.0.1"


# ------------------------------------------------------------------ job_store

def test_job_state_is_persisted_in_the_database(db_session_factory):
    """핵심: 상태가 프로세스 메모리가 아니라 DB에 있어야 워커를 늘려도 폴링이 404가 안 난다."""
    job_id = job_store.create_job()

    db = db_session_factory()
    try:
        row = db.get(models.ScriptJob, job_id)
        assert row is not None and row.status == "processing"
    finally:
        db.close()


def test_job_lifecycle():
    job_id = job_store.create_job()
    assert job_store.get_job(job_id)["status"] == "processing"

    job_store.complete_job(job_id, {"project_id": 1})
    done = job_store.get_job(job_id)
    assert done["status"] == "completed" and done["data"] == {"project_id": 1}

    other = job_store.create_job()
    job_store.fail_job(other, "터졌습니다")
    failed = job_store.get_job(other)
    assert failed["status"] == "failed" and failed["error"] == "터졌습니다"


def test_unknown_job_returns_none():
    assert job_store.get_job("존재하지-않는-접수번호") is None


def test_stale_processing_jobs_are_failed_on_restart(db_session_factory):
    """작업을 돌리는 스레드풀은 프로세스 안에 있다. 서버가 죽으면 아무도 이어받지 않으므로,
    그대로 두면 프론트가 영원히 폴링한다."""
    from datetime import datetime, timedelta, timezone

    job_id = job_store.create_job()
    db = db_session_factory()
    try:
        row = db.get(models.ScriptJob, job_id)
        row.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        db.commit()
    finally:
        db.close()

    assert job_store.fail_stale_jobs(older_than_minutes=30) == 1
    assert job_store.get_job(job_id)["status"] == "failed"


def test_recent_processing_jobs_are_left_alone():
    job_id = job_store.create_job()
    assert job_store.fail_stale_jobs(older_than_minutes=30) == 0
    assert job_store.get_job(job_id)["status"] == "processing"
