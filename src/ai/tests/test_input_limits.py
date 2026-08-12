"""입력 길이/범위 상한 회귀 테스트.

왜 필요한가: 여기 값들은 대부분 그대로 HCX 프롬프트에 실려 나간다. 상한이 없으면
호출 한 번으로 유료 토큰을 무제한 태울 수 있다. 파일 크기(20MB)만 막고 본문 텍스트를
열어두면 제한을 우회하는 셈이라, 파일로 들어오는 경로까지 같이 막혔는지 확인한다.

이 테스트들은 전부 검증 단계에서 끊기거나(422) DB만 건드리므로 외부 API를 호출하지 않는다.
"""
import io

from fastapi.testclient import TestClient

import main
from main import (
    app,
    MAX_AUDIENCE_LEN,
    MAX_EXTRA_REQUIREMENT_LEN,
    MAX_OUTLINE_LEN,
    MAX_PRESENTATION_MINUTES,
    MAX_SCRIPT_TEXT_LEN,
    MAX_SLIDE_SCRIPT_LEN,
    MAX_TOPIC_LEN,
)
from db import models

client = TestClient(app)


def _create_project(db_session_factory, script="원래 대본"):
    db = db_session_factory()
    try:
        project = models.Project(name="상한 테스트", filename=None, topic=None, keywords=[])
        project.slides = [models.Slide(slide_number=1, source_content="원문", script=script)]
        db.add(project)
        db.commit()
        db.refresh(project)
        return project.id
    finally:
        db.close()


# ---------------------------------------------------------------- 본문 텍스트

def test_script_text_over_limit_is_rejected():
    response = client.post("/api/projects", data={"script_text": "가" * (MAX_SCRIPT_TEXT_LEN + 1)})
    assert response.status_code == 422


def test_script_text_at_limit_is_accepted():
    """상한 자체는 정상 사용을 막으면 안 된다 — 경계값은 통과해야 한다."""
    response = client.post("/api/projects", data={"script_text": "가" * MAX_SCRIPT_TEXT_LEN})
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_topic_and_outline_over_limit_are_rejected():
    assert client.post("/api/projects", data={
        "topic": "가" * (MAX_TOPIC_LEN + 1), "outline": "목차",
    }).status_code == 422
    assert client.post("/api/projects", data={
        "topic": "주제", "outline": "가" * (MAX_OUTLINE_LEN + 1),
    }).status_code == 422


# ------------------------------------------------- 파일 업로드로 상한 우회 금지

def test_coaching_file_over_text_limit_is_rejected():
    """20MB 제한 안쪽이어도 추출된 글자 수가 상한을 넘으면 거절해야 한다.
    이걸 열어두면 script_text 상한이 무의미해진다(파일로 넣으면 그만이므로)."""
    oversized = ("가" * 1000 + "\n") * ((MAX_SCRIPT_TEXT_LEN // 1000) + 2)
    response = client.post(
        "/api/projects",
        data={"mode": "coaching"},
        files={"file": ("script.txt", io.BytesIO(oversized.encode("utf-8")), "text/plain")},
    )
    assert response.status_code == 413
    # 조용히 자르지 않고 몇 자인지 알려준다.
    assert "너무 깁니다" in response.json()["detail"]


def test_coaching_file_under_text_limit_is_accepted():
    response = client.post(
        "/api/projects",
        data={"mode": "coaching"},
        files={"file": ("script.txt", io.BytesIO("안녕하세요. 발표를 시작하겠습니다.".encode("utf-8")), "text/plain")},
    )
    assert response.status_code == 200


# ------------------------------------------------------------ 생성 요청 파라미터

def test_extra_requirement_and_audience_over_limit_are_rejected():
    base = {"project_id": 1, "presentation_time": 5, "style": "격식체"}
    assert client.post("/api/script/full", json={
        **base, "extra_requirement": "가" * (MAX_EXTRA_REQUIREMENT_LEN + 1),
    }).status_code == 422
    assert client.post("/api/script/full", json={
        **base, "audience": "가" * (MAX_AUDIENCE_LEN + 1),
    }).status_code == 422


def test_presentation_time_out_of_range_is_rejected():
    """발표 시간은 생성할 대본 분량 = 토큰 비용에 직결된다."""
    for bad_time in (0, -1, MAX_PRESENTATION_MINUTES + 1):
        response = client.post(
            "/api/script/full",
            json={"project_id": 1, "presentation_time": bad_time, "style": "격식체"},
        )
        assert response.status_code == 422, f"presentation_time={bad_time}가 통과했습니다"


def test_non_positive_ids_are_rejected():
    assert client.post("/api/script/full", json={
        "project_id": 0, "presentation_time": 5, "style": "격식체",
    }).status_code == 422
    assert client.post("/api/script/partial", json={
        "project_id": 1, "target_slide": 0, "style": "격식체",
    }).status_code == 422
    assert client.post("/api/analysis/words", json={"project_id": -5}).status_code == 422


# --------------------------------------------------------------- 슬라이드 편집

def test_slide_update_script_over_limit_is_rejected(db_session_factory):
    project_id = _create_project(db_session_factory)
    response = client.put(
        f"/api/projects/{project_id}/slides/1",
        json={"script": "가" * (MAX_SLIDE_SCRIPT_LEN + 1)},
    )
    assert response.status_code == 422


def test_slide_update_at_limit_still_saves(db_session_factory):
    project_id = _create_project(db_session_factory)
    long_script = "가" * MAX_SLIDE_SCRIPT_LEN
    assert client.put(
        f"/api/projects/{project_id}/slides/1", json={"script": long_script}
    ).status_code == 200

    db = db_session_factory()
    try:
        slide = db.query(models.Slide).filter_by(project_id=project_id, slide_number=1).one()
        assert slide.script == long_script
    finally:
        db.close()


def test_slide_create_position_must_be_positive(db_session_factory):
    project_id = _create_project(db_session_factory)
    assert client.post(
        f"/api/projects/{project_id}/slides", json={"position": 0, "script": "x"}
    ).status_code == 422
