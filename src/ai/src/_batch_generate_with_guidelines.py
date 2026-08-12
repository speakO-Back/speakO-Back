"""
발표자가 직접 적어준 슬라이드별 가이드라인을 근거로 대본을 생성한다.

## _batch_extract_team.py와 뭐가 다른가
그쪽은 PPT에서 추출한 텍스트만 근거로 쓴다. 그런데 실제 팀원 PPT는 대부분 **디자인
이미지 한 장**이라, 비전 추출 결과가 "이미지에는 글자가 없습니다. 검은색 배경에…" 같은
**거절 문구**로 채워져 있다. 그걸 그대로 프롬프트에 넣으면 모델이 그 문장을 설명하는
엉뚱한 대본을 쓴다.

그래서 여기서는
  1) 발표자가 적어준 가이드라인을 **주된 근거**로 삼고,
  2) 추출 텍스트에서 거절 문구를 걷어낸 뒤 남은 실제 글자만 **보조 근거**로 붙인다.

입력: projects/_가이드라인.json  (개인 자료라 projects/는 gitignore)
출력: <pptx와 같은 폴더>/<파일명>_대본_가이드라인반영.txt
"""
import json
import os
import re
import sys
import time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

from clova.full_generation.generator import FullScriptGenerator, ScriptRefiner
from utils.ppt_extractor import PptExtractor

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS_ROOT = os.path.join(BASE_DIR, "projects")
GUIDELINE_PATH = os.path.join(PROJECTS_ROOT, "_가이드라인.json")
STATE_PATH = os.path.join(BASE_DIR, ".usage_state.json")
STYLE = "격식체"
# 발표 사이 대기. HCX 분당 호출 한도에 걸리지 않게 한다(실측: 동시 4개로 연달아 던지면 429).
COOLDOWN_SECONDS = int(os.getenv("BATCH_COOLDOWN_SECONDS", "30"))

# HCX 비전이 "글자가 없다"고 답한 문장들. 내용이 아니라 잡음이므로 근거에서 걷어낸다.
_VISION_REFUSAL = re.compile(
    r"(이미지에(는| 텍스트가| 글자가)[^.]*?(없|빈 문자열)[^.]*\.?"
    r"|이미지에서 확인되는 텍스트는 없습니다\.?"
    r"|전체가 검은색 배경[^.]*\.?"
    r"|검은색 배경에 흰색[^.]*\.?"
    r"|따라서 빈 문자열[^.]*\.?"
    r"|빈 문자열)"
)


def _clean_extracted(text: str) -> str:
    """비전 거절 문구를 걷어내고 실제로 읽힌 글자만 남긴다."""
    cleaned = _VISION_REFUSAL.sub(" ", text or "")
    cleaned = re.sub(r"[\s​]+", " ", cleaned).strip(" -·|")
    return cleaned


def _load_slides(pptx_path, extractor):
    """추출 캐시가 있으면 그대로 쓴다. 비전 호출은 유료라 두 번 내면 안 된다."""
    cache = os.path.join(
        os.path.dirname(pptx_path),
        f".{os.path.splitext(os.path.basename(pptx_path))[0]}_추출캐시.json",
    )
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f).get("slides", [])
    return extractor.extract_structured_data(pptx_path).get("slides", [])


def _usage():
    if not os.path.exists(STATE_PATH):
        return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f).get("hcx", {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})


def _build_ppt_text(guide_slides, extracted, swaps):
    """슬라이드 번호 → '가이드라인 + (있으면) 추출 텍스트' 형태의 프롬프트 입력을 만든다."""
    by_number = {int(s["slide_number"]): (s.get("content") or "") for s in extracted}

    for left, right in swaps or []:
        by_number[left], by_number[right] = by_number.get(right, ""), by_number.get(left, "")

    lines = []
    for number in sorted(int(n) for n in guide_slides):
        guide = guide_slides[str(number)]
        extracted_text = _clean_extracted(by_number.get(number, ""))
        # 가이드라인을 앞에 둔다 — 이게 발표자가 실제로 말하려는 내용이다.
        content = f"[발표자 가이드] {guide}"
        if extracted_text:
            content += f" / [슬라이드에서 읽힌 글자] {extracted_text}"
        lines.append(f"Slide {number}: {content}")
    return "\n".join(lines), len(guide_slides)


def process(entry, extractor, generator, refiner):
    pptx_path = os.path.join(PROJECTS_ROOT, entry["pptx"].replace("/", os.sep))
    name = os.path.splitext(os.path.basename(pptx_path))[0]
    if not os.path.exists(pptx_path):
        return ("fail", name, None, "pptx 없음")

    out_path = os.path.join(os.path.dirname(pptx_path), f"{name}_대본_가이드라인반영.txt")

    # 이어받기: 이미 완성된 대본이 있으면 건너뛴다. HCX가 429로 막히면 중간부터 다시
    # 돌려야 하는데, 매번 처음부터 돌리면 성공한 발표의 토큰까지 다시 쓰게 된다.
    # (누락 슬라이드가 있는 파일은 완성으로 치지 않는다)
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            if "생성 누락 슬라이드" not in f.read(400):
                return ("skip", name, out_path, "이미 완성된 대본 있음")

    extracted = _load_slides(pptx_path, extractor)
    ppt_text, slide_count = _build_ppt_text(
        entry["슬라이드"], extracted, entry.get("슬라이드순서교체")
    )

    presentation_time = max(3, min(20, round(slide_count * 0.6)))
    topic = entry.get("요약", "")
    audience = entry.get("대상", "")
    extra = entry.get("비고", "")

    print(f"   가이드 {slide_count}장 / 추출 {len(extracted)}장 / 발표시간 {presentation_time}분 / 대상 {audience or '미지정'}")

    result = generator.generate_full_script(
        ppt_text, presentation_time, STYLE, extra_requirement=extra, audience=audience, topic=topic
    )
    if not result or not result.get("slides"):
        return ("fail", name, None, "대본 생성 실패")

    generated = {int(s["slide_number"]) for s in result["slides"]}
    missing = sorted(set(int(n) for n in entry["슬라이드"]) - generated)
    # 원문이 제목 한 줄뿐이라 근거 없이 쓴 장. 발표자가 직접 채워야 하므로 눈에 띄게 알려준다.
    thin = sorted(int(n) for n in result.get("thin_source_slide_numbers", []))

    draft = "\n\n".join(
        f"Slide {s['slide_number']}: {s['script']}"
        for s in sorted(result["slides"], key=lambda x: int(x["slide_number"]))
    )
    refined = refiner.refine_script(draft, style=STYLE) or draft

    header = [f"# {name}", f"- 대상: {audience or '미지정'}", f"- 요약: {topic}"]
    if entry.get("팀명"):
        header.insert(1, f"- 팀명: {entry['팀명']}")
    if missing:
        header.append(f"- ⚠️ 생성 누락 슬라이드: {missing}")
    if thin:
        header.append(
            f"- ✍️ 직접 채워야 하는 슬라이드: {thin} "
            "(원문이 제목 한 줄뿐이라 근거가 없습니다. 일반적인 안내문만 써뒀으니 내용을 직접 넣으세요)"
        )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(header) + "\n\n" + refined + "\n")

    note = f"{len(generated)}/{slide_count}장"
    if missing:
        note += f" ⚠️누락 {missing}"
    if thin:
        note += f" ✍️직접채움 {thin}"
    return ("ok", name, out_path, note)


def main():
    with open(GUIDELINE_PATH, encoding="utf-8") as f:
        guidelines = json.load(f)

    extractor = PptExtractor()
    generator = FullScriptGenerator()
    refiner = ScriptRefiner()

    before = _usage()
    results = []
    for index, entry in enumerate(guidelines["발표"]):
        print(f"\n▶ {entry['pptx']}")
        result = process(entry, extractor, generator, refiner)
        results.append(result)
        # HCX는 분당 호출 수 제한이 있다(실측: 동시 4개로 연달아 던지면 429).
        # 발표 사이에 숨을 돌려서 다음 발표가 통째로 실패하는 것을 막는다.
        if result[0] != "skip" and index < len(guidelines["발표"]) - 1:
            print(f"   … 다음 발표까지 {COOLDOWN_SECONDS}초 대기 (HCX 분당 한도 회피)")
            time.sleep(COOLDOWN_SECONDS)
    after = _usage()

    print("\n" + "=" * 70)
    print("결과")
    print("=" * 70)
    for status, name, out, note in results:
        mark = {"ok": "✅", "fail": "❌"}[status]
        loc = os.path.relpath(out, PROJECTS_ROOT) if out else "-"
        print(f"{mark} {name}\n   └ {note} | {loc}")

    prompt = after["prompt_tokens"] - before["prompt_tokens"]
    completion = after["completion_tokens"] - before["completion_tokens"]
    calls = after["calls"] - before["calls"]
    cost = prompt / 1_000_000 * 1250 + completion / 1_000_000 * 5000
    print("\n" + "=" * 70)
    print(f"HCX 호출 {calls}회 | prompt {prompt:,} + completion {completion:,} = {prompt + completion:,} tokens")
    print(f"비용: {cost:,.1f}원 (VAT 별도) / {cost * 1.1:,.1f}원 (VAT 포함)")
    print("=" * 70)


if __name__ == "__main__":
    main()
