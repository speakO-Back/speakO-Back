import os
import sys
import json
from dotenv import load_dotenv

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from clova.full_generation.generator import FullScriptGenerator
from utils.ppt_extractor import PptExtractor
from utils.script_storage import save_generated_script, find_file_with_ext

load_dotenv()

def run(project_dir, presentation_time=8, style="신뢰감을 주는 발표자 스타일"):
    project_name = os.path.basename(os.path.normpath(project_dir))
    pptx_path = find_file_with_ext(project_dir, (".pptx",))
    if not pptx_path:
        print(f"❌ {project_dir}에 .pptx 파일이 없습니다.")
        return

    print(f"📁 프로젝트: {project_name}")
    print(f"📄 PPT: {pptx_path}")

    ppt_data = PptExtractor().extract_structured_data(pptx_path)
    if not ppt_data.get("slides"):
        print("❌ PPT에서 텍스트를 추출하지 못했습니다.")
        return

    ppt_text = "\n".join(f"Slide {s['slide_number']}: {s['content']}" for s in ppt_data["slides"])

    script_result = FullScriptGenerator().generate_full_script(
        ppt_text=ppt_text,
        presentation_time=presentation_time,
        style=style,
    )

    if not script_result or not script_result.get("slides"):
        print("❌ 대본 생성 실패 (API 키 미설정 또는 응답 오류)")
        print(script_result)
        return

    saved_json = save_generated_script(
        "full", json.dumps(script_result, ensure_ascii=False, indent=2),
        stem=project_name, extension="json", project_dir=project_dir,
    )
    print(f"💾 JSON 저장: {saved_json}")

    script_text = "\n".join(f"Slide {s['slide_number']}: {s['script']}" for s in script_result["slides"])
    saved_txt = save_generated_script(
        "full", script_text, stem=f"{project_name}_plain", extension="txt", project_dir=project_dir,
    )
    print(f"💾 평문 저장: {saved_txt}")
    print("\n--- 생성된 대본 미리보기 ---")
    print(script_text[:1500])

if __name__ == "__main__":
    run(sys.argv[1])
