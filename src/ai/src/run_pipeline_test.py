import os
import sys
import json
from dotenv import load_dotenv

# Windows 콘솔의 기본 코드페이지(cp949)는 이모지를 인코딩하지 못해
# print() 호출 시 스크립트가 죽는다. UTF-8로 강제 전환.
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# 프로젝트 최상위 경로를 파이썬 경로에 추가하여 임포트 에러를 방지합니다.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 1. 작성해둔 모든 AI 클라이언트 모듈 임포트
from clova.full_generation.generator import FullScriptGenerator
from etri.etri_client import EtriLanguageAnalyzer
from g2p.g2p_client import G2pConverter
from tts.clova_voice_client import ClovaVoiceClient
from utils.ppt_extractor import PptExtractor
from utils.script_storage import save_generated_script, find_file_with_ext

# Azure 모듈은 이전 단계에서 누락되었을 수 있으므로 예외 처리하여 안전하게 임포트합니다.
try:
    from azure_speech.azure_client import PronunciationEvaluator
    azure_available = True
except ImportError:
    azure_available = False

# .env 파일 로드
load_dotenv()

def _resolve_project_dir():
    """
    작업할 프로젝트 폴더(projects/<이름>/)를 결정한다.
    PPT, 생성된 대본, 녹음 파일을 전부 같은 프로젝트 폴더 안에 두므로,
    어떤 녹음/대본이 어떤 PPT에 해당하는지는 "같은 폴더에 있다"는 것으로 알 수 있다.

    1. `python run_pipeline_test.py projects/내프로젝트`처럼 인자로 폴더를 넘기면 그걸 사용
    2. 인자가 없으면 projects/ 밑에 있는 폴더 중 하나를 자동으로 사용
       (여러 개면 첫 번째 것을 쓰고 다른 것도 있다고 안내)
    3. projects/ 폴더 자체가 없거나 비어있으면 안내만 하고, STEP 0에서 건너뜀
    """
    projects_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "projects")

    if len(sys.argv) > 1:
        arg_path = sys.argv[1]
        return arg_path if os.path.isdir(arg_path) else os.path.dirname(os.path.abspath(arg_path))

    if os.path.isdir(projects_root):
        candidates = sorted(
            name for name in os.listdir(projects_root)
            if os.path.isdir(os.path.join(projects_root, name))
        )
        if len(candidates) > 1:
            print(f"⚠️ projects/ 밑에 프로젝트가 여러 개({', '.join(candidates)})라 첫 번째 것을 사용합니다.")
            print("⚠️ 다른 프로젝트를 테스트하려면: python run_pipeline_test.py projects/폴더이름")
        if candidates:
            return os.path.join(projects_root, candidates[0])

    return projects_root

def run_integrated_pipeline():
    print("=" * 60)
    print("🚀 SpeaKO AI 전체 파이프라인 통합 테스트를 시작합니다!")
    print("=" * 60)

    # ==========================================
    # [모듈 초기화]
    # ==========================================
    print("\n⏳ 각 AI 모듈을 초기화하고 있습니다...")
    ppt_extractor = PptExtractor()
    clova_gen = FullScriptGenerator()
    etri_analyzer = EtriLanguageAnalyzer()
    g2p_converter = G2pConverter()
    tts_client = ClovaVoiceClient()
    azure_evaluator = PronunciationEvaluator() if azure_available else None
    print("✅ 모든 모듈 초기화 완료!\n")

    # ==========================================
    # [STEP 0] PPT 구조화 추출
    # ==========================================
    print("-" * 60)
    print("🎯 [STEP 0] PPT 구조화 추출 테스트 (python-pptx)")
    project_dir = _resolve_project_dir()
    project_name = os.path.basename(os.path.normpath(project_dir))
    pptx_path = find_file_with_ext(project_dir, (".pptx",))
    ppt_data = None

    if pptx_path:
        print(f"📁 프로젝트: {project_name} ({project_dir})")
        print(f"📄 사용할 PPT 파일: {pptx_path}")
        ppt_data = ppt_extractor.extract_structured_data(pptx_path)
        print("✨ [추출 성공] 데이터:")
        print(json.dumps(ppt_data, indent=2, ensure_ascii=False))
    else:
        print(f"⚠️ 프로젝트 폴더({project_dir})에 .pptx 파일이 없어 이 단계는 건너뜁니다.")
        print("⚠️ speako-ai-server/projects/<프로젝트이름>/ 폴더를 만들고 그 안에 .pptx를 넣어주세요.")

    # ==========================================
    # [STEP 1] 대본 생성 (HyperCLOVA X)
    # ==========================================
    print("-" * 60)
    print("🎯 [STEP 1] 대본 생성 테스트 (HyperCLOVA X)")

    # STEP 0에서 실제 PPT를 추출했다면 그 내용을 그대로 사용하고,
    # PPT가 없을 때만 데모용 문장으로 대체한다.
    if ppt_data and ppt_data.get("slides"):
        ppt_text = "\n".join(f"Slide {s['slide_number']}: {s['content']}" for s in ppt_data["slides"])
    else:
        ppt_text = "메타버스와 인프라 구축의 특징을 살펴봅시다."

    script_result = clova_gen.generate_full_script(
        ppt_text=ppt_text,
        presentation_time=1,
        style="신뢰감을 주는 아나운서 스타일"
    )

    # 생성된 대본이 없거나(API 키 없음) 실패 시, 다음 파이프라인 진행을 위해 가짜 대본 주입
    if not script_result:
        print("⚠️ 대본 생성 결과가 없어(안전 모드), 임시 텍스트로 다음 단계를 진행합니다.")
        script_text = "메타버스와 인프라 구축의 특징을 살펴봅시다."
    else:
        print("✨ [생성 성공] 데이터:")
        print(json.dumps(script_result, indent=2, ensure_ascii=False))

        saved_path = save_generated_script(
            "full", json.dumps(script_result, ensure_ascii=False, indent=2),
            stem=project_name, extension="json", project_dir=project_dir
        )
        print(f"💾 생성된 전체 대본 저장: {saved_path}")

        # 실제 생성된 대본을 "Slide N: 내용" 형태의 평문으로도 만들어 둔다.
        # 부분 재생성(target_slide) 시 원본 대본 텍스트 안에서 몇 번 슬라이드가 어디인지
        # 별도 매핑 없이 텍스트 자체에서 바로 찾을 수 있게 하기 위함.
        if script_result.get("slides"):
            script_text = "\n".join(f"Slide {slide['slide_number']}: {slide['script']}" for slide in script_result["slides"])
        else:
            script_text = script_result.get("raw_toon", "메타버스와 인프라 구축의 특징을 살펴봅시다.")

        saved_txt_path = save_generated_script("full", script_text, stem=f"{project_name}_plain", extension="txt", project_dir=project_dir)
        print(f"💾 평문(Slide N:) 대본 저장: {saved_txt_path}")

    # ==========================================
    # [STEP 2] 어려운 단어 추출 (ETRI)
    # ==========================================
    print("\n" + "-" * 60)
    print("🎯 [STEP 2] 발음 주의 단어 추출 (ETRI 언어 분석)")
    
    extracted_words = etri_analyzer.extract_difficult_words(script_text)
    
    # ETRI 결과가 없으면(키 오류 등) 강제로 임시 단어 리스트 주입
    if not extracted_words:
        print("⚠️ ETRI 분석 결과가 없어, 안전 모드(Fallback) 단어를 사용합니다.")
        extracted_words = ["메타버스", "인프라", "특징", "구축"]
    else:
        print(f"✨ [추출 성공] 단어 리스트: {extracted_words}")

    # ==========================================
    # [STEP 3] 발음 기호 변환 (G2P)
    # ==========================================
    print("\n" + "-" * 60)
    print("🎯 [STEP 3] 시각적 발음 코칭 변환 (G2P)")
    
    phoneme_data = g2p_converter.convert_words(extracted_words)
    print("✨ [변환 성공] 데이터:")
    print(json.dumps(phoneme_data, indent=2, ensure_ascii=False))

    # ==========================================
    # [STEP 4] 단어 음성 합성 (Clova Voice)
    # ==========================================
    print("\n" + "-" * 60)
    print("🎯 [STEP 4] 단어 음성 합성 테스트 (Clova Voice)")
    
    if phoneme_data:
        target_word = phoneme_data[-1]['word'] # 예시로 리스트의 마지막 단어 선택 (예: '특징')
        test_audio_filename = f"test_pronunciation_{target_word}.mp3"
        
        audio_path = tts_client.synthesize_word(target_word, test_audio_filename)
        if audio_path:
            print(f"✨ [합성 성공] 오디오 파일이 저장되었습니다: {os.path.abspath(audio_path)}")
        else:
            print("❌ 음성 합성에 실패했습니다.")
    else:
        print("⚠️ 합성할 단어가 없습니다.")

    # ==========================================
    # [STEP 5] 사용자 음성 평가 (Azure Speech)
    # ==========================================
    print("\n" + "-" * 60)
    print("🎯 [STEP 5] 사용자 발음 채점 및 평가 (Azure Speech)")
    
    if azure_evaluator:
        recordings_dir = os.path.join(project_dir, "recordings")
        wav_path = find_file_with_ext(recordings_dir, (".wav",))

        if wav_path:
            print(f"🎙️ 사용할 녹음 파일: {wav_path}")
            eval_result = azure_evaluator.evaluate_audio(wav_path, script_text)
            print("✨ [평가 성공] 분석 데이터:")
            print(json.dumps(eval_result, indent=2, ensure_ascii=False))
        else:
            print(f"⚠️ 녹음 파일이 없어 이 단계는 건너뜁니다.")
            print(f"⚠️ ({recordings_dir} 밑에 .wav 파일을 넣어주세요.)")
    else:
        print("⚠️ Azure 평가 모듈이 존재하지 않거나 로드에 실패했습니다.")

    print("\n" + "=" * 60)
    print("🎉 SpeaKO AI 전체 파이프라인 통합 테스트가 완료되었습니다!")
    print("=" * 60)

if __name__ == "__main__":
    run_integrated_pipeline()