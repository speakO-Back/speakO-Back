"""
비동기 작업(대본 생성 등)의 진행 상태를 담아두는 저장소.

왜 필요한가: 대본 생성은 20~30초가 걸린다. 요청 하나로 끝까지 기다리면 프록시/게이트웨이
타임아웃에 끊길 수 있으므로, "접수번호(job_id)를 즉시 돌려주고 → 뒤에서 처리 → 프론트가
상태만 물어본다"는 구조로 만든다. 그 접수번호별 상태(처리중/완료/실패와 결과)를 여기에 담는다.

지금은 프로세스 메모리(dict)에 담는다. 단순하고 준비할 게 없어서다. 다만 이 방식은
  - 서버를 재시작하면 진행 중 상태가 사라지고,
  - 서버(프로세스)를 여러 개로 띄우면 서로의 작업을 모른다.
는 한계가 있다. 이 상황이 실제로 문제되면 이 모듈만 DB 구현으로 갈아끼우면 되도록,
바깥에는 create/complete/fail/get 네 개의 함수만 노출한다.
"""

import threading
import time
import uuid

# 메모리가 무한정 늘지 않도록 상한을 둔다. 넘으면 가장 오래된 항목부터 버린다.
# (완료된 작업은 프론트가 결과를 받아가면 더 볼 일이 없으므로 오래된 것부터 정리해도 안전하다.)
_MAX_JOBS = 1000

_jobs = {}
_lock = threading.Lock()


def _prune_locked():
    """_lock을 잡은 상태에서 호출. 상한을 넘으면 가장 먼저 들어온 항목부터 제거한다."""
    while len(_jobs) > _MAX_JOBS:
        # dict는 삽입 순서를 보존하므로 첫 키가 가장 오래된 작업이다.
        oldest = next(iter(_jobs))
        del _jobs[oldest]


def create_job() -> str:
    """새 작업을 '처리중'으로 등록하고 접수번호(job_id)를 돌려준다."""
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {"status": "processing", "data": None, "error": None, "created_at": time.time()}
        _prune_locked()
    return job_id


def complete_job(job_id: str, data) -> None:
    """작업을 '완료'로 표시하고 결과(data)를 저장한다. 이미 정리된 job_id면 조용히 무시한다."""
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(status="completed", data=data, error=None)


def fail_job(job_id: str, error: str) -> None:
    """작업을 '실패'로 표시하고 사유(error)를 저장한다."""
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(status="failed", data=None, error=error)


def get_job(job_id: str):
    """작업 상태의 사본을 돌려준다(없으면 None). 사본이라 호출자가 만져도 저장소가 안 바뀐다."""
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job is not None else None


def _reset_for_test() -> None:
    """테스트 격리용: 저장소를 비운다."""
    with _lock:
        _jobs.clear()
