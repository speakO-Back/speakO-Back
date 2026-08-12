"""스프링·프론트 연동 계약 회귀 테스트.

프론트 → 스프링 → AI 서버 구조라, AI 서버가 지키는 약속이 중간에서 깨지면 원인을 찾기 어렵다.
**AI 서버 쪽에서 단독으로 검증 가능한 부분**을 여기에 고정한다. 스프링이 이걸 그대로
통과시키는지는 별도 확인이 필요하다(`nextStep.md`의 "스프링·프론트 연동" 절 참고).

여기서 검증하지 못하는 것(스프링 설정이라 서버 밖):
  - 스프링의 multipart 상한(Spring Boot 기본 1MB/10MB)이 우리 20MB보다 작으면 우리한테 오지도 않음
  - 스프링이 X-Forwarded-For / X-API-Key / Retry-After를 그대로 넘기는지
  - 스프링의 요청 타임아웃이 발음 평가(Azure 왕복)보다 긴지
"""
import io

from fastapi.testclient import TestClient

import main
from main import app
from utils import rate_limit

client = TestClient(app)


# ------------------------------------------------- 상태 폴링 계약 (프론트가 직접 의존)

def test_job_submission_returns_202_with_job_id(monkeypatch, db_session_factory):
    """프론트는 202 본문의 job_id로 폴링한다. 키 이름이 바뀌면 조용히 무한 로딩이 된다."""
    from db import models

    db = db_session_factory()
    try:
        project = models.Project(name="계약 테스트", filename=None, topic="주제", keywords=[])
        project.slides = [models.Slide(slide_number=1, source_content="원문")]
        db.add(project)
        db.commit()
        project_id = project.id
    finally:
        db.close()

    monkeypatch.setattr(
        main.full_generator, "generate_full_script",
        lambda *a, **k: {"slides": [{"slide_number": 1, "script": "대본"}], "missing_slide_numbers": []},
    )

    accepted = client.post(
        "/api/script/full",
        json={"project_id": project_id, "presentation_time": 3, "style": "격식체"},
    )
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]

    polled = client.get(f"/api/script/jobs/{job_id}")
    assert polled.status_code == 200
    # 프론트는 이 세 값만 분기한다. 새 값이 생기면 프론트가 모르는 상태에 빠진다.
    assert polled.json()["status"] in ("processing", "completed", "failed")


def test_unknown_job_id_is_404_not_500():
    """스프링이 job_id를 잘못 전달했을 때 500이면 원인 추적이 어렵다."""
    assert client.get("/api/script/jobs/없는번호").status_code == 404


# ------------------------------------------------------ 오류 응답이 프론트까지 읽히는가

def test_error_details_are_utf8_json():
    """detail이 한국어다. 인코딩이 깨지면 프론트가 사용자에게 깨진 글자를 보여준다."""
    response = client.post("/api/analysis/words", json={"project_id": 999999})
    assert response.status_code == 404
    assert "application/json" in response.headers["content-type"]
    assert response.json()["detail"] == "프로젝트를 찾을 수 없습니다."


def test_rate_limit_error_is_also_utf8_json(monkeypatch):
    """429는 미들웨어가 직접 만든 응답이라 FastAPI의 직렬화를 안 탄다 — 따로 확인한다."""
    monkeypatch.setattr(rate_limit.limiter, "expensive_per_minute", 1)
    client.post("/api/analysis/words", json={"project_id": 999999})
    blocked = client.post("/api/analysis/words", json={"project_id": 999999})

    assert blocked.status_code == 429
    assert "charset=utf-8" in blocked.headers["content-type"].lower()
    assert "다시 시도" in blocked.json()["detail"]  # 한글이 깨지지 않았다


# ------------------------------------------- 스프링 뒤에서 사용자를 구분할 수 있는가

def test_forwarded_for_gives_each_user_its_own_budget(monkeypatch):
    """스프링을 거치면 소켓 주소가 전부 스프링 한 대다. XFF를 넘겨줘야 사용자별로 집계된다.
    (안 넘기면 전역 상한처럼 동작 — nextStep의 확인 항목)"""
    monkeypatch.setattr(rate_limit.limiter, "expensive_per_minute", 2)
    body = {"project_id": 999999}

    first_user = [
        client.post("/api/analysis/words", json=body, headers={"X-Forwarded-For": "203.0.113.1"}).status_code
        for _ in range(3)
    ]
    assert first_user == [404, 404, 429]  # 이 사용자만 소진

    other_user = client.post(
        "/api/analysis/words", json=body, headers={"X-Forwarded-For": "203.0.113.2"}
    )
    assert other_user.status_code == 404, "다른 사용자가 앞 사용자 때문에 막혔습니다"


# ------------------------------------------------------------- 헬스체크 / 인프라

def test_root_health_check_is_not_rate_limited(monkeypatch):
    """로드밸런서·컨테이너 헬스체크가 레이트 리밋에 걸리면 배포가 통째로 죽는다."""
    monkeypatch.setattr(rate_limit.limiter, "default_per_minute", 1)
    monkeypatch.setattr(rate_limit.limiter, "expensive_per_minute", 1)

    codes = [client.get("/").status_code for _ in range(10)]
    assert codes == [200] * 10


# ------------------------------------------------------------------ 업로드 계약

def test_korean_filename_survives_upload(monkeypatch):
    """프론트가 올리는 파일명은 한국어인 경우가 대부분이다(발표자료.pptx).
    경로에는 확장자만 쓰지만, 프로젝트 이름으로는 원본 파일명이 남아야 한다."""
    monkeypatch.setattr(
        main.ppt_extractor, "extract_structured_data",
        lambda path, topic_hint="", outline_hint="": {
            "metadata": {"topic": "주제", "keywords": []},
            "slides": [{"slide_number": 1, "content": "내용"}],
        },
    )
    response = client.post(
        "/api/projects",
        files={"file": ("2026년_최종_발표자료.pptx", io.BytesIO(b"fake"), "application/vnd.ms-powerpoint")},
    )
    assert response.status_code == 200

    project_id = response.json()["project_id"]
    detail = client.get(f"/api/projects/{project_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["name"] == "2026년_최종_발표자료"


def test_pptx_near_our_limit_is_accepted_by_us(monkeypatch):
    """우리 상한은 20MB다. 이보다 작은 파일이 거절된다면 그건 우리가 아니라 스프링 쪽 제한이다
    (Spring Boot의 multipart 기본값은 파일 1MB / 요청 10MB로 우리보다 훨씬 작다)."""
    monkeypatch.setattr(
        main.ppt_extractor, "extract_structured_data",
        lambda path, topic_hint="", outline_hint="": {
            "metadata": {"topic": "주제", "keywords": []},
            "slides": [{"slide_number": 1, "content": "내용"}],
        },
    )
    fifteen_mb = b"\x00" * (15 * 1024 * 1024)
    response = client.post(
        "/api/projects",
        files={"file": ("big.pptx", io.BytesIO(fifteen_mb), "application/vnd.ms-powerpoint")},
    )
    assert response.status_code == 200, "15MB는 우리 상한(20MB) 안쪽이라 통과해야 한다"
