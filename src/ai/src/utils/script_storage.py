import os
import re
from datetime import datetime

# speako-ai-server/
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECTS_DIR = os.path.join(BASE_DIR, "projects")
GENERATED_SCRIPTS_DIR = os.path.join(BASE_DIR, "generated_scripts")


def _timestamped_filename(stem: str, extension: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_stem = re.sub(r"[^0-9A-Za-z가-힣_-]", "_", stem)[:50] or "script"
    return f"{safe_stem}_{timestamp}.{extension}"


def save_generated_script(kind: str, content: str, stem: str = "script", extension: str = "txt", project_dir: str = None) -> str:
    """
    생성된 대본을 저장한다.
    - project_dir이 주어지면 <project_dir>/scripts/<kind>/ 에 저장해서 같은 PPT/녹음과 한 폴더로 묶는다.
    - 없으면 기존 위치인 generated_scripts/<kind>/ 에 저장한다 (project 개념이 없는 호출자를 위한 기본값).
    """
    folder = os.path.join(project_dir, "scripts", kind) if project_dir else os.path.join(GENERATED_SCRIPTS_DIR, kind)
    os.makedirs(folder, exist_ok=True)

    file_path = os.path.join(folder, _timestamped_filename(stem, extension))
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    return file_path


def find_file_with_ext(folder: str, extensions: tuple) -> str:
    """폴더 안에서 주어진 확장자를 가진 첫 번째 파일 경로를 반환한다 (없으면 None)."""
    if not os.path.isdir(folder):
        return None
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith(extensions):
            return os.path.join(folder, name)
    return None
