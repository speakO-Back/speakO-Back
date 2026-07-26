import os
import sys
import glob
import json
from dotenv import load_dotenv

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from clova.full_generation.generator import FullScriptGenerator, ScriptRefiner
from utils.ppt_extractor import PptExtractor
from utils.script_storage import save_generated_script, find_file_with_ext

load_dotenv()

STYLE = "신뢰감을 주는 발표자 스타일"
PRESENTATION_TIME = 8


def _find_existing_plain_draft(project_dir):
    pattern = os.path.join(project_dir, "scripts", "full", "*_plain_*.txt")
    matches = sorted(glob.glob(pattern))
    return matches[-1] if matches else None


def _generate_draft(project_dir, project_name):
    pptx_path = find_file_with_ext(project_dir, (".pptx",))
    if not pptx_path:
        print(f"  ❌ .pptx 파일이 없어 건너뜁니다.")
        return None

    ppt_data = PptExtractor().extract_structured_data(pptx_path)
    if not ppt_data.get("slides"):
        print(f"  ❌ PPT에서 텍스트를 추출하지 못했습니다.")
        return None

    ppt_text = "\n".join(f"Slide {s['slide_number']}: {s['content']}" for s in ppt_data["slides"])

    script_result = FullScriptGenerator().generate_full_script(
        ppt_text=ppt_text,
        presentation_time=PRESENTATION_TIME,
        style=STYLE,
    )

    if not script_result or not script_result.get("slides"):
        print(f"  ❌ 대본 생성 실패: {script_result}")
        return None

    save_generated_script(
        "full", json.dumps(script_result, ensure_ascii=False, indent=2),
        stem=project_name, extension="json", project_dir=project_dir,
    )

    script_text = "\n".join(f"Slide {s['slide_number']}: {s['script']}" for s in script_result["slides"])
    plain_path = save_generated_script(
        "full", script_text, stem=f"{project_name}_plain", extension="txt", project_dir=project_dir,
    )
    print(f"  💾 초안 저장: {plain_path}")
    return script_text


def process_project(project_dir):
    project_name = os.path.basename(os.path.normpath(project_dir))
    print(f"\n{'=' * 60}\n📁 {project_name}\n{'=' * 60}")

    existing_draft = _find_existing_plain_draft(project_dir)
    if existing_draft:
        print(f"  ℹ️ 기존 초안 재사용: {existing_draft}")
        with open(existing_draft, "r", encoding="utf-8") as f:
            script_text = f.read()
    else:
        script_text = _generate_draft(project_dir, project_name)

    if not script_text:
        return

    refined_text = ScriptRefiner().refine_script(script_text, style=STYLE)
    if not refined_text:
        print(f"  ❌ 고도화 실패, 초안만 남습니다.")
        return

    refined_path = save_generated_script(
        "full", refined_text, stem=f"{project_name}_refined", extension="txt", project_dir=project_dir,
    )
    print(f"  ✨ 고도화 저장: {refined_path}")


def main():
    projects_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "projects")
    target_names = sys.argv[1:]

    if target_names:
        project_dirs = [os.path.join(projects_root, name) for name in target_names]
    else:
        project_dirs = sorted(
            os.path.join(projects_root, name)
            for name in os.listdir(projects_root)
            if os.path.isdir(os.path.join(projects_root, name))
        )

    for project_dir in project_dirs:
        process_project(project_dir)

    print("\n🎉 배치 작업 완료")


if __name__ == "__main__":
    main()
