"""
projects/ 밑을 재귀적으로 훑어 모든 .pptx에서 대본을 생성/고도화해 각 PPT 옆에 저장한다.
- 저장 위치: <pptx와 같은 폴더>/<파일명>_대본.txt  (팀원이 바로 찾아 공유하기 쉽게)
- 이미 _대본.txt가 있으면 건너뜀(토큰 절약, idempotent)
- 이미지 전용 슬라이드(텍스트 0개)는 실패로 기록
- .pdf는 대본 생성 대상에서 제외하고 "참고 자료"로만 목록에 남김
"""
import os
import sys
import glob
import json

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from clova.full_generation.generator import FullScriptGenerator, ScriptRefiner
from utils.ppt_extractor import PptExtractor

load_dotenv()

PROJECTS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "projects")
STYLE = "격식체"


def _presentation_time(slide_count):
    # 슬라이드 수에 대략 비례(3~15분). 자연스러움 판단이 목적이라 정확할 필요는 없음.
    return max(3, min(15, round(slide_count * 0.5)))


def _extract_slides_cached(pptx_path, ppt_extractor):
    """
    PPT 텍스트 추출 결과를 PPT 옆에 캐시한다.
    이미지 전용 슬라이드는 추출에 HCX 비전 호출(유료)이 슬라이드당 몇 번씩 들어가므로,
    대본 생성을 다시 돌릴 때마다 같은 이미지를 또 읽히면 비용이 그대로 두 배가 된다.
    """
    cache_path = os.path.join(
        os.path.dirname(pptx_path), f".{os.path.splitext(os.path.basename(pptx_path))[0]}_추출캐시.json"
    )

    if os.path.exists(cache_path) and os.path.getmtime(cache_path) >= os.path.getmtime(pptx_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f).get("slides", [])
        except Exception:
            pass  # 캐시가 깨졌으면 그냥 새로 추출한다

    ppt_data = ppt_extractor.extract_structured_data(pptx_path)
    slides = ppt_data.get("slides", [])
    if slides:
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"slides": slides}, f, ensure_ascii=False)
        except Exception as e:
            print(f"  ⚠️ 추출 캐시 저장 실패(무시하고 진행): {e}")
    return slides


def process_pptx(pptx_path, ppt_extractor, generator, refiner):
    folder = os.path.dirname(pptx_path)
    stem = os.path.splitext(os.path.basename(pptx_path))[0]
    out_path = os.path.join(folder, f"{stem}_대본.txt")
    rel = os.path.relpath(pptx_path, PROJECTS_ROOT)

    if os.path.exists(out_path):
        return ("skip", rel, out_path, "이미 대본 있음")

    slides = _extract_slides_cached(pptx_path, ppt_extractor)
    if not slides:
        return ("fail", rel, None, "텍스트 추출 0개(이미지 전용 슬라이드로 추정)")

    ppt_text = "\n".join(f"Slide {s['slide_number']}: {s['content']}" for s in slides)
    result = generator.generate_full_script(ppt_text, _presentation_time(len(slides)), STYLE)
    if not result or not result.get("slides"):
        return ("fail", rel, None, "HCX 대본 생성 실패")

    draft = "\n\n".join(f"Slide {s['slide_number']}: {s['script']}" for s in result["slides"])
    refined = refiner.refine_script(draft, style=STYLE) or draft  # 고도화 실패 시 초안이라도 저장

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(refined)

    # 입력 장수가 아니라 "실제로 대본이 만들어진 장수"를 보고해야 한다.
    # (예전엔 입력 장수를 찍어서, 19장 중 1장만 생성된 것을 "19슬라이드 성공"으로 오인했다)
    generated = len(result["slides"])
    note = f"{generated}/{len(slides)}슬라이드"
    if generated < len(slides):
        note += " ⚠️일부 누락"
    return ("ok", rel, out_path, note)


def main():
    ppt_extractor = PptExtractor()
    generator = FullScriptGenerator()
    refiner = ScriptRefiner()

    pptx_files = sorted(glob.glob(os.path.join(PROJECTS_ROOT, "**", "*.pptx"), recursive=True))
    pdf_files = sorted(glob.glob(os.path.join(PROJECTS_ROOT, "**", "*.pdf"), recursive=True))

    print(f"발견: pptx {len(pptx_files)}개, pdf {len(pdf_files)}개\n")

    results = []
    for pptx in pptx_files:
        print(f"▶ 처리: {os.path.relpath(pptx, PROJECTS_ROOT)}")
        results.append(process_pptx(pptx, ppt_extractor, generator, refiner))

    print("\n" + "=" * 70)
    print("결과 요약")
    print("=" * 70)
    for status, rel, out, note in results:
        mark = {"ok": "✅ 생성", "skip": "⏭️ 스킵", "fail": "❌ 실패"}[status]
        loc = os.path.relpath(out, PROJECTS_ROOT) if out else "-"
        print(f"{mark} | {rel}\n        └ {note} | 저장: {loc}")

    if pdf_files:
        print("\n[참고] 대본 생성 대상이 아닌 PDF 파일 (수동 확인 필요):")
        for pdf in pdf_files:
            print(f"  - {os.path.relpath(pdf, PROJECTS_ROOT)}")


if __name__ == "__main__":
    main()
