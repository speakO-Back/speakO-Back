import os
import sys
import re
import glob
import random

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
from clova.partial_generation.generator import PartialScriptGenerator, STYLE_INSTRUCTIONS
from utils.script_storage import save_generated_script

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv()

PROJECTS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "projects")


def _find_latest_refined(project_dir):
    matches = sorted(glob.glob(os.path.join(project_dir, "scripts", "full", "*_refined_*.txt")))
    return matches[-1] if matches else None


def _detect_dominant_style(text):
    formal = len(re.findall(r"(습니다|드립니다|하겠습니다)", text))
    casual = len(re.findall(r"(해요|네요|을게요|ㄹ게요|거든요)", text))
    return "편안한 말투" if casual >= formal else "격식체"


def _opposite(style):
    return "편안한 말투" if style == "격식체" else "격식체"


def run(project_name=None, target_slide=None, style=None, extra_requirement=""):
    if project_name is None:
        candidates = [
            name for name in os.listdir(PROJECTS_ROOT)
            if os.path.isdir(os.path.join(PROJECTS_ROOT, name)) and _find_latest_refined(os.path.join(PROJECTS_ROOT, name))
        ]
        project_name = random.choice(candidates)

    project_dir = os.path.join(PROJECTS_ROOT, project_name)
    refined_path = _find_latest_refined(project_dir)
    if not refined_path:
        print(f"❌ {project_name}에 고도화된 대본이 없습니다.")
        return

    with open(refined_path, "r", encoding="utf-8") as f:
        full_script = f.read()

    slide_numbers = [int(n) for n in re.findall(r"Slide (\d+):", full_script)]
    if not slide_numbers:
        print("❌ 슬라이드 번호를 찾지 못했습니다.")
        return

    if target_slide is None:
        target_slide = random.choice(slide_numbers)

    current_style = _detect_dominant_style(full_script)
    if style is None:
        style = _opposite(current_style)

    original_line_match = re.search(rf"Slide {target_slide}:.*?(?=\nSlide \d+:|\Z)", full_script, re.DOTALL)
    original_line = original_line_match.group(0).strip() if original_line_match else "(찾을 수 없음)"

    print(f"📁 프로젝트: {project_name}")
    print(f"🎯 대상 슬라이드: {target_slide}")
    print(f"🎨 현재 추정 어투: {current_style} → 요청 어투: {style}")
    if extra_requirement.strip():
        print(f"📝 추가 요구사항: {extra_requirement.strip()}")
    print(f"\n--- 기존 슬라이드 {target_slide} 대본 ---\n{original_line}\n")

    result = PartialScriptGenerator().generate_partial_script(
        original_script=full_script,
        target_slide=target_slide,
        style=style,
        extra_requirement=extra_requirement,
    )

    if not result or "script" not in result:
        print(f"❌ 부분 재생성 실패: {result}")
        return

    print(f"--- 재생성 결과 (요청 어투: {style}) ---\n{result['script']}\n")

    saved_path = save_generated_script(
        "partial", result["script"],
        stem=f"{project_name}_slide{target_slide}_{style}",
        extension="txt",
        project_dir=project_dir,
    )
    print(f"💾 저장: {saved_path}")


if __name__ == "__main__":
    run()
