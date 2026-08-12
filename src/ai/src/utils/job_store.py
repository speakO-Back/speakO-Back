"""
비동기 작업(대본 생성 등)의 진행 상태를 담아두는 저장소.

왜 필요한가: 대본 생성은 20~30초가 걸린다. 요청 하나로 끝까지 기다리면 프록시/게이트웨이
타임아웃에 끊길 수 있으므로, "접수번호(job_id)를 즉시 돌려주고 → 뒤에서 처리 → 프론트가
상태만 물어본다"는 구조로 만든다. 그 접수번호별 상태(처리중/완료/실패와 결과)를 여기에 담는다.

**저장 위치는 DB다.** 예전엔 프로세스 메모리(dict)였는데 두 가지가 실제로 문제였다.
  - `--workers 2` 이상으로 띄우면 접수한 워커와 폴링을 받는 워커가 달라 **즉시 404**가 난다.
    (프론트는 1~2초마다 폴링하므로 그대로 사용자에게 드러난다)
  - 서버를 재시작하면 진행 중이던 작업이 증발한다.
DB로 옮기면서 워커 수 제약이 사라졌다. 다만 **작업을 실제로 돌리는 스레드풀은 여전히
프로세스 안에** 있으므로, 처리 중이던 작업은 재시작 시 'processing'인 채로 남는다
(아래 `fail_stale_jobs` 참고).

바깥에는 create/complete/fail/get 네 개만 노출한다.
"""
import uuid
from datetime import datetime, timedelta, timezone

from db.database import SessionLocal
from db import models

# 테스트에서 인메모리 DB로 갈아끼울 수 있게 모듈 변수로 둔다.
session_factory = SessionLocal

# 완료된 작업 기록을 무한정 쌓아두지 않는다. 프론트가 결과를 받아가면 더 볼 일이 없다.
_MAX_JOBS = 1000

# 피그마 AI Set Page (Loading)은 진행을 **4단계**로 그린다:
#   ① 파일 수령 → ② 텍스트 추출 → ③ 대본 작성 중 → ④ 완료
# ①②는 `POST /api/projects`(업로드·추출)에서 이미 끝난 뒤에 이 화면이 뜬다 — 피그마에서도
# 그 둘은 채워진 상태로, ③만 스피너가 돈다. 그래서 작업은 ③에서 시작한다.
STEP_LABELS = {1: "파일 수령", 2: "텍스트 추출", 3: "대본 작성", 4: "완료"}
TOTAL_STEPS = 4
INITIAL_STEP = 3
DONE_STEP = 4


def step_label(step) -> str:
    return STEP_LABELS.get(step, "")


def _prune(db) -> None:
    """상한을 넘으면 가장 오래된 작업부터 지운다."""
    total = db.query(models.ScriptJob).count()
    if total <= _MAX_JOBS:
        return
    stale_ids = [
        row.id
        for row in db.query(models.ScriptJob.id)
        .order_by(models.ScriptJob.created_at.asc())
        .limit(total - _MAX_JOBS)
        .all()
    ]
    if stale_ids:
        db.query(models.ScriptJob).filter(models.ScriptJob.id.in_(stale_ids)).delete(
            synchronize_session=False
        )


def create_job() -> str:
    """새 작업을 '처리중'으로 등록하고 접수번호(job_id)를 돌려준다."""
    job_id = uuid.uuid4().hex
    db = session_factory()
    try:
        db.add(models.ScriptJob(id=job_id, status="processing", step=INITIAL_STEP))
        _prune(db)
        db.commit()
    finally:
        db.close()
    return job_id


def _finish(job_id: str, **fields) -> None:
    db = session_factory()
    try:
        job = db.get(models.ScriptJob, job_id)
        if job is None:
            return  # 이미 정리된 작업이면 조용히 무시한다(예전 메모리 구현과 동일한 계약).
        for key, value in fields.items():
            setattr(job, key, value)
        db.commit()
    finally:
        db.close()


def complete_job(job_id: str, data) -> None:
    """작업을 '완료'로 표시하고 결과(data)를 저장한다."""
    _finish(job_id, status="completed", data=data, error=None, step=DONE_STEP)


def fail_job(job_id: str, error: str) -> None:
    """작업을 '실패'로 표시하고 사유(error)를 저장한다."""
    _finish(job_id, status="failed", data=None, error=error)


def get_job(job_id: str):
    """작업 상태를 평범한 dict로 돌려준다(없으면 None)."""
    db = session_factory()
    try:
        job = db.get(models.ScriptJob, job_id)
        if job is None:
            return None
        # step이 None인 건 이 컬럼이 생기기 전에 만들어진 작업이다(마이그레이션 직후).
        # 화면이 "0단계"를 그리지 않도록 시작 단계로 채워준다.
        return {
            "status": job.status, "data": job.data, "error": job.error,
            "step": job.step or INITIAL_STEP,
        }
    finally:
        db.close()


def fail_stale_jobs(older_than_minutes: int = 30) -> int:
    """재시작 전에 'processing'인 채로 남은 작업을 실패로 정리하고, 정리한 개수를 돌려준다.

    작업을 돌리는 스레드풀은 프로세스 안에 있으므로, 서버가 죽으면 그 작업은 아무도 이어받지
    않는다. 그대로 두면 프론트가 영원히 폴링한다. 부팅 시 한 번 불러서 끊어준다.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=older_than_minutes)
    db = session_factory()
    try:
        stale = (
            db.query(models.ScriptJob)
            .filter(models.ScriptJob.status == "processing", models.ScriptJob.created_at < cutoff)
            .all()
        )
        for job in stale:
            job.status = "failed"
            job.error = "서버가 재시작되어 작업이 중단되었습니다. 다시 시도해주세요."
        db.commit()
        return len(stale)
    finally:
        db.close()


def _reset_for_test() -> None:
    """테스트 격리용: 저장소를 비운다."""
    db = session_factory()
    try:
        db.query(models.ScriptJob).delete()
        db.commit()
    finally:
        db.close()
