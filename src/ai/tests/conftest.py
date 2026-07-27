import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

SRC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC_PATH)


@pytest.fixture(autouse=True)
def _isolate_usage_log(monkeypatch, tmp_path):
    """테스트가 실제 usage_log.md / .usage_state.json에 기록을 남기지 않도록 격리한다."""
    from utils import usage_tracker
    monkeypatch.setattr(usage_tracker, "USAGE_LOG_PATH", str(tmp_path / "usage_log.md"))
    monkeypatch.setattr(usage_tracker, "USAGE_STATE_PATH", str(tmp_path / ".usage_state.json"))


@pytest.fixture(autouse=True)
def _isolate_stdict(monkeypatch):
    """
    로컬 .env에 실제 STDICT_API_KEY가 있으면 /api/analysis/words 테스트가 표준국어대사전 API를
    진짜로 호출하게 되어 느리고 네트워크 의존적이 된다. 기본적으로 장단음 조회를 꺼 둔다.
    (장단음 판정 자체를 검증하는 test_stdict_client.py는 자체 StdictClient 인스턴스를 쓰므로 영향 없음)
    """
    import main
    monkeypatch.setattr(main.stdict_client, "use_fallback", True)


@pytest.fixture(autouse=True)
def db_session_factory():
    """
    테스트가 실제 speako-ai-server/data/speako.db를 건드리지 않도록,
    테스트마다 격리된 인메모리 SQLite로 FastAPI의 get_db 의존성을 교체한다.
    autouse라 모든 테스트에 자동 적용되고, DB에 직접 데이터를 심어야 하는 테스트는
    이 픽스처를 인자로 받아 세션 팩토리로 사용하면 된다.
    """
    import main
    from db.database import Base, get_db

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[get_db] = _override_get_db

    # 대본 생성은 백그라운드 스레드(job_executor)에서 자체 세션(job_session_factory)으로 돈다.
    # 테스트에서는 (1) 그 자체 세션을 같은 인메모리 DB로 향하게 하고,
    # (2) 스레드 대신 그 자리에서 즉시 실행(_SyncExecutor)해서 폴링 없이 결정적으로 끝나게 한다.
    # 이렇게 해도 엔드포인트의 실제 코드 경로(job 등록→실행→완료, 상태 조회)는 그대로 검증된다.
    from utils import job_store
    prev_job_factory = main.job_session_factory
    prev_job_executor = main.job_executor
    main.job_session_factory = TestSessionLocal
    main.job_executor = _SyncExecutor()
    job_store._reset_for_test()

    yield TestSessionLocal

    main.job_session_factory = prev_job_factory
    main.job_executor = prev_job_executor
    job_store._reset_for_test()
    main.app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


class _SyncExecutor:
    """테스트용: submit된 작업을 백그라운드 스레드가 아니라 그 자리에서 바로 실행한다.
    덕분에 POST 응답이 돌아온 시점엔 이미 작업이 끝나 있어, 폴링 타이밍에 따른 테스트 흔들림이 없다."""

    def submit(self, fn, *args, **kwargs):
        from concurrent.futures import Future
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:  # 실제 executor와 동일하게 예외를 Future에 담는다
            future.set_exception(exc)
        return future
