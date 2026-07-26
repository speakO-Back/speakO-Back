import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

pptx_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "projects", "부산대_체교과_교수지도안_발표")
pptx_file = next(f for f in os.listdir(pptx_path) if f.endswith(".pptx"))
full_pptx_path = os.path.join(pptx_path, pptx_file)

print("1) /api/projects 실제 PPT로 프로젝트 생성")
with open(full_pptx_path, "rb") as f:
    r = client.post(
        "/api/projects",
        files={"file": (pptx_file, f, "application/octet-stream")},
        data={"project_name": "DB 스모크 테스트"},
    )
print(r.status_code, r.json()["success"], "project_id =", r.json()["project_id"])
project_id = r.json()["project_id"]

print("\n2) /api/script/full 실제 HCX 호출로 대본 생성")
r = client.post("/api/script/full", json={"project_id": project_id, "presentation_time": 3, "style": "격식체"})
print(r.status_code, "slides:", len(r.json().get("data", {}).get("slides", [])))

print("\n3) /api/projects/{id} 로 슬라이드에 대본이 실제 저장됐는지 확인")
r = client.get(f"/api/projects/{project_id}")
slides = r.json()["data"]["slides"]
scripted = [s for s in slides if s["script"]]
print(r.status_code, f"{len(scripted)}/{len(slides)} 슬라이드에 대본 저장됨")
if scripted:
    print("예시:", scripted[0]["script"][:80])

print("\n4) /api/script/partial 로 방금 그 슬라이드 재생성 (원본 재전송 없이 project_id만)")
target = scripted[0]["slide_number"] if scripted else 1
r = client.post(
    "/api/script/partial",
    json={"project_id": project_id, "target_slide": target, "style": "편안한 말투"},
)
print(r.status_code, r.json().get("data"))

print("\n5) /api/analysis/words 로 단어 추출 (ETRI 키 없어서 fallback 경로)")
r = client.post("/api/analysis/words", json={"project_id": project_id})
print(r.status_code, r.json().get("data"))

print("\n6) /api/projects 목록에 방금 만든 프로젝트가 보이는지 확인")
r = client.get("/api/projects")
found = any(p["id"] == project_id for p in r.json()["data"])
print(r.status_code, "found in list:", found)
