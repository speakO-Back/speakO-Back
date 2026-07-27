import sys

# Windows 콘솔의 기본 코드페이지(cp949)는 이모지를 인코딩하지 못해
# print() 호출 시 서버가 부팅도 되기 전에 죽는다. UTF-8로 강제 전환.
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from fastapi import FastAPI, APIRouter, UploadFile, File, Form, Header, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Literal, Optional
from concurrent.futures import ThreadPoolExecutor
import uvicorn
import os
import re
import uuid
import hmac

# 1. 분리해둔 AI 클라이언트 모듈들 임포트
from clova.full_generation.generator import FullScriptGenerator
from clova.partial_generation.generator import PartialScriptGenerator
from etri.etri_client import EtriLanguageAnalyzer
from nlp.kiwi_analyzer import KiwiAnalyzer
from g2p.g2p_client import G2pConverter
from azure_speech.azure_client import PronunciationEvaluator
from utils.ppt_extractor import PptExtractor
from utils import pdf_extractor
from utils import docx_extractor
from utils import audio_converter
from utils.text_heuristics import extract_frequent_terms
from utils.stdict_client import StdictClient
from utils.hangul_phonology import has_liaison_pattern
from db.database import get_db, init_db, SessionLocal
from db import models
from utils import job_store

# 대본 생성은 20~30초 걸리는 무거운 작업이라, 요청을 붙잡고 기다리면 타임아웃에 끊길 수 있다.
# 그래서 접수번호(job_id)를 즉시 돌려주고 실제 생성은 백그라운드 스레드에서 돌린다.
# - job_executor: 백그라운드 작업을 돌리는 스레드풀(동시 실행 개수 상한).
# - job_session_factory: 백그라운드 작업은 요청 수명과 분리되므로 자체 DB 세션을 열어야 한다.
#   (요청에서 받은 세션은 응답과 함께 닫힌다.) 테스트에서 인메모리 DB로 갈아끼울 수 있게 모듈 변수로 둔다.
job_executor = ThreadPoolExecutor(
    max_workers=max(1, int(os.getenv("SCRIPT_JOB_CONCURRENCY", "4"))),
    thread_name_prefix="script-job",
)
job_session_factory = SessionLocal

# 2. FastAPI 앱 인스턴스 생성
app = FastAPI(
    title="SpeaKO AI Server",
    description="SpeaKO 프로젝트의 대본 생성 및 발음 분석을 담당하는 AI 마이크로서비스입니다.",
    version="1.0.0"
)

# 3. CORS(교차 출처 리소스 공유) 설정
origins = [
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 업로드 파일 제한 (DoS 방지)
MAX_PPT_SIZE_BYTES = 20 * 1024 * 1024   # 20MB
MAX_AUDIO_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
ALLOWED_PPT_EXTENSIONS = {".pptx", ".pdf"}
ALLOWED_COACHING_EXTENSIONS = {".docx", ".txt", ".pdf"}
# 브라우저 MediaRecorder는 기본적으로 webm/opus(Chrome·Firefox·Edge)나 mp4/m4a(Safari)로 녹음한다.
# ffmpeg가 이 포맷들을 전부 16kHz mono WAV로 변환하므로(convert_to_wav), 프론트가 녹음한 걸 그대로 받는다.
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".webm"}
UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1MB

# /api/* 호출 인증 (X-API-Key 헤더). 값이 비어있거나 플레이스홀더면 로컬 개발 편의를 위해 인증을 건너뛴다.
# 배포 전에는 반드시 실제 값으로 채워야 한다 — 안 그러면 누구나 /api/*를 호출해 외부 API 비용을 유발할 수 있다.
SPEAKO_API_KEY = os.getenv("SPEAKO_API_KEY")
_AUTH_ENABLED = bool(SPEAKO_API_KEY) and "여기에_" not in SPEAKO_API_KEY

if not _AUTH_ENABLED:
    print("⚠️ [경고] SPEAKO_API_KEY가 설정되지 않아 /api/* 엔드포인트가 인증 없이 열려 있습니다. (로컬 개발 전용 상태)")


def verify_api_key(x_api_key: str = Header(None)):
    if not _AUTH_ENABLED:
        return
    # 타이밍 사이드채널을 피하려고 상수 시간 비교를 쓴다. (헤더가 없으면 즉시 거부)
    if not x_api_key or not hmac.compare_digest(x_api_key, SPEAKO_API_KEY):
        raise HTTPException(status_code=401, detail="유효하지 않거나 누락된 API 키입니다. (X-API-Key 헤더 필요)")


def _safe_temp_path(original_filename: str) -> str:
    """원본 파일명을 그대로 경로에 쓰지 않고 확장자만 추출해 새 임의 이름을 만든다 (경로 조작 방지)."""
    ext = os.path.splitext(original_filename or "")[1].lower()
    return f"temp_{uuid.uuid4().hex}{ext}"


def _save_upload_with_limit(upload_file: UploadFile, dest_path: str, max_bytes: int, allowed_extensions: set):
    """확장자를 검사하고, 크기 제한을 지키며 업로드 파일을 디스크에 저장합니다."""
    ext = os.path.splitext(upload_file.filename or "")[1].lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=415,
            detail=f"허용되지 않는 파일 형식입니다. ({', '.join(sorted(allowed_extensions))}만 허용)"
        )

    total_bytes = 0
    with open(dest_path, "wb") as buffer:
        while True:
            chunk = upload_file.file.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"파일 크기가 너무 큽니다. (최대 {max_bytes // (1024 * 1024)}MB)"
                )
            buffer.write(chunk)

# 4. 각 AI 모듈 객체 생성
full_generator = FullScriptGenerator()
partial_generator = PartialScriptGenerator()
etri_analyzer = EtriLanguageAnalyzer()
kiwi_analyzer = KiwiAnalyzer()
g2p_converter = G2pConverter()
azure_evaluator = PronunciationEvaluator()
ppt_extractor = PptExtractor()
stdict_client = StdictClient()

# 5. DB 테이블 생성 (없으면 생성, 있으면 그대로 둠)
init_db()


def _fallback_difficult_words(script_text: str, top_n: int = 6) -> list:
    """ETRI 키가 없거나 호출이 실패했을 때, 실제 대본 내용에서 빈도 기반으로 발음 주의 단어 후보를 뽑는다."""
    cleaned = re.sub(r"Slide \d+:", "", script_text)
    return extract_frequent_terms([cleaned], top_n=top_n, min_length=2)


DIFFICULT_WORD_CATEGORIES = ("장단음", "연음", "표기-발음불일치")
# 한 번의 /api/analysis/words 요청이 외부 사전 API를 무제한으로 때리지 않도록 상한.
# (단어마다 표준국어대사전 조회가 들어가므로, 대본이 아주 길어도 앞쪽 N개까지만 분류한다)
MAX_DIFFICULT_WORDS = 40


def _classify_word_category(word: str, is_different: bool):
    """
    발음 주의 단어를 장단음/연음/표기-발음불일치 3가지로 분류한다.
    - 장단음: 표준국어대사전에 등록된 발음에 장음 표시(ː)가 있는 경우.
              모음 길이는 한글 철자에는 드러나지 않으므로(예: 밤/밤ː는 표기가 같음),
              G2P의 철자≠발음(is_different) 여부와 **무관하게** 독립적으로 판정한다.
    - 연음: (철자≠발음이면서) 받침+무초성 음절 구조가 있어 받침이 다음 음절로 넘어가는 경우.
    - 표기-발음불일치: 위 둘에 해당하지 않는 철자≠발음 (비음화/경음화 등).
    철자=발음이고 장단음도 아니면 분류하지 않는다(None).
    """
    if stdict_client.has_long_vowel(word):
        return "장단음"
    if not is_different:
        return None
    if has_liaison_pattern(word):
        return "연음"
    return "표기-발음불일치"


def _compiled_script_text(project: "models.Project", only_scripted: bool = True) -> str:
    """프로젝트의 슬라이드들을 "Slide N: 내용" 평문으로 이어붙인다."""
    slides = [s for s in project.slides if s.script] if only_scripted else project.slides
    return "\n".join(f"Slide {s.slide_number}: {s.script}" for s in slides)


# ==========================================
# 📦 프론트엔드와 통신할 데이터 모델 (JSON 바디 정의)
# ==========================================
class FullScriptRequest(BaseModel):
    project_id: int
    presentation_time: int
    style: Literal["격식체", "편안한 말투"]
    extra_requirement: Optional[str] = ""
    audience: Optional[str] = ""  # 발표 대상/청중 (피그마 '대상' 필드, 예: 교수님/면접관). 선택 입력.
    topic: Optional[str] = ""  # 발표 주제. 비우면 프로젝트에 저장된 주제(생성 시 입력)를 사용한다.

class PartialScriptRequest(BaseModel):
    project_id: int
    target_slide: int
    style: Literal["격식체", "편안한 말투"]
    extra_requirement: Optional[str] = ""
    audience: Optional[str] = ""  # 발표 대상/청중. 선택 입력.

class AnalysisRequest(BaseModel):
    project_id: int

class SlideUpdateRequest(BaseModel):
    """결과 화면(피그마 05)에서 사용자가 직접 고친 대본을 저장할 때 쓴다. PPT O는 슬라이드별,
    PPT X는 1번 슬라이드(전체 대본 한 덩어리)를 이 API로 저장한다."""
    script: str  # 사용자가 편집한 대본 본문 (빈 문자열 허용 — 내용을 비우는 것도 편집이다)
    source_content: Optional[str] = None  # 원문도 함께 고칠 일이 있으면 선택적으로 갱신

class SlideCreateRequest(BaseModel):
    """슬라이드 추가(피그마 05-1 '슬라이드 추가/삭제 가능'). position이 있으면 그 자리에 끼워넣고
    뒤 슬라이드 번호는 하나씩 밀린다. 없으면 맨 뒤에 붙인다."""
    position: Optional[int] = None  # 1-based. 이 번호 자리에 삽입. None이면 맨 끝.
    script: Optional[str] = ""
    source_content: Optional[str] = ""


def _round_scores_in_place(result: dict):
    """
    발음 평가 점수를 소수 1자리(0~100)로 정리한다. 프론트는 이 숫자를 그대로 표시만 하므로
    표시 형태를 백엔드에서 확정하는데, 0~5점 같은 거친 척도로 뭉개지 말고 소수점까지
    자세히(예: 87.4) 내려줘서 미세한 발음 차이가 드러나게 한다.
    전체 점수(overall_scores)와 단어별 정확도(words_detail)를 모두 처리.
    """
    scores = result.get("overall_scores")
    if isinstance(scores, dict):
        for key, value in list(scores.items()):
            if isinstance(value, (int, float)):
                scores[key] = round(value, 1)
    for word in result.get("words_detail") or []:
        if isinstance(word, dict) and isinstance(word.get("accuracy_score"), (int, float)):
            word["accuracy_score"] = round(word["accuracy_score"], 1)


# ==========================================
# 🚀 API 엔드포인트(라우터) 정의
# ==========================================
# "/"는 헬스체크용이라 인증 없이 열어두고, /api/* 전부는 verify_api_key를 거치도록 라우터를 분리한다.
api = APIRouter(dependencies=[Depends(verify_api_key)])


@app.get("/")
async def root():
    return {"message": "SpeaKO AI 서버가 정상적으로 실행 중입니다!"}

def _create_project_from_script(db: Session, name: str, script: str):
    project = models.Project(name=name, filename=None, topic=None, keywords=[])
    project.slides = [models.Slide(slide_number=1, source_content=script, script=script)]
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@api.post("/api/projects")
async def create_project(
    file: UploadFile = File(None),
    project_name: str = Form(None),
    mode: Literal["script", "coaching"] = Form("script"),
    topic: str = Form(None),
    outline: str = Form(None),
    script_text: str = Form(None),
    db: Session = Depends(get_db),
):
    """[프로젝트 생성 API] 입력 방식에 따라 새 프로젝트를 만듭니다.
    1. file(PPTX/PDF, mode="script" 기본값) — 슬라이드별 텍스트를 추출해서 프로젝트를 만듭니다. topic/outline은
       이미지 슬라이드 텍스트 인식(HCX 비전) 정확도를 높이는 선택적 힌트로 쓰입니다. ("AI 대본 생성" 플로우)
    2. file 없이 topic + outline — PPT 없이 주제/가이드라인 텍스트만으로 프로젝트를 만듭니다.
       이후 /api/script/full 호출 시 이 내용을 바탕으로 대본을 생성합니다.
    3. script_text 또는 file(DOCX/TXT/PDF, mode="coaching") — 이미 완성된 발표 대본을 그대로 붙여넣거나
       파일로 올려서, 생성 단계 없이 바로 발음 코칭/평가로 넘어갑니다. ("발표 발음 코칭" 플로우)
    이후 대본 생성/재생성/단어 분석/발음 평가는 여기서 받은 project_id를 기준으로 이어집니다."""

    if script_text and script_text.strip():
        project = _create_project_from_script(db, project_name or "직접 입력한 대본", script_text.strip())
        return {"success": True, "project_id": project.id, "data": {"metadata": {"topic": None, "keywords": []}, "slides": [{"slide_number": 1, "content": script_text.strip()}]}}

    if file is not None and mode == "coaching":
        temp_file_path = _safe_temp_path(file.filename)
        try:
            _save_upload_with_limit(file, temp_file_path, MAX_PPT_SIZE_BYTES, ALLOWED_COACHING_EXTENSIONS)

            ext = os.path.splitext(file.filename or "")[1].lower()
            try:
                if ext == ".pdf":
                    text = pdf_extractor.extract_full_text(temp_file_path)
                elif ext == ".docx":
                    text = docx_extractor.extract_full_text(temp_file_path)
                else:
                    with open(temp_file_path, "rb") as f:
                        text = f.read().decode("utf-8", errors="ignore")
            except Exception as e:
                # 손상되었거나 확장자만 바꾼 파일 등 — 라이브러리 예외를 그대로 500으로 내보내지 않고 422로 정직하게 알린다.
                print(f"❌ 코칭용 파일 파싱 실패({ext}): {e}")
                raise HTTPException(status_code=422, detail="파일을 열 수 없습니다. 파일이 손상되지 않았는지 확인해주세요.")
            text = text.strip()

            if not text:
                raise HTTPException(status_code=422, detail="파일에서 텍스트를 추출하지 못했습니다.")

            project = _create_project_from_script(db, project_name or os.path.splitext(file.filename or "project")[0], text)
            return {"success": True, "project_id": project.id, "data": {"metadata": {"topic": None, "keywords": []}, "slides": [{"slide_number": 1, "content": text}]}}
        finally:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)

    if file is not None:
        temp_file_path = _safe_temp_path(file.filename)
        try:
            _save_upload_with_limit(file, temp_file_path, MAX_PPT_SIZE_BYTES, ALLOWED_PPT_EXTENSIONS)

            ext = os.path.splitext(file.filename or "")[1].lower()
            try:
                if ext == ".pdf":
                    result = pdf_extractor.extract_structured_data(temp_file_path)
                else:
                    result = ppt_extractor.extract_structured_data(temp_file_path, topic_hint=topic or "", outline_hint=outline or "")
            except Exception as e:
                print(f"❌ 슬라이드 파일 파싱 실패({ext}): {e}")
                raise HTTPException(status_code=422, detail="파일을 열 수 없습니다. 파일이 손상되지 않았는지 확인해주세요.")

            if not result["slides"]:
                raise HTTPException(status_code=422, detail="파일에서 텍스트를 추출하지 못했습니다.")

            # 발표 주제는 피그마에서 유일한 필수 입력이다. 사용자가 직접 적어 보낸 topic이 있으면
            # 그것을 최우선으로 저장한다(예전엔 자동 감지 topic으로 덮어써서 사용자 입력이 사라졌다).
            project = models.Project(
                name=project_name or os.path.splitext(file.filename or "project")[0],
                filename=file.filename,
                topic=(topic.strip() if topic and topic.strip() else result["metadata"]["topic"]),
                keywords=result["metadata"]["keywords"],
            )
            project.slides = [
                models.Slide(slide_number=slide["slide_number"], source_content=slide["content"])
                for slide in result["slides"]
            ]
            db.add(project)
            db.commit()
            db.refresh(project)

            return {"success": True, "project_id": project.id, "data": result}
        finally:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)

    if topic and topic.strip() and outline and outline.strip():
        brief = f"발표 주제: {topic.strip()}\n목차/가이드라인: {outline.strip()}"
        project = models.Project(
            name=project_name or topic.strip()[:50],
            filename=None,
            topic=topic.strip(),
            keywords=[],
        )
        project.slides = [models.Slide(slide_number=1, source_content=brief)]
        db.add(project)
        db.commit()
        db.refresh(project)
        return {"success": True, "project_id": project.id, "data": {"metadata": {"topic": topic.strip(), "keywords": []}, "slides": [{"slide_number": 1, "content": brief}]}}

    raise HTTPException(
        status_code=422,
        detail=(
            "file(PPTX/PDF)을 업로드하거나, topic과 outline을 함께 입력하거나, "
            "script_text를 입력하거나, mode=coaching과 함께 file(DOCX/TXT/PDF)을 업로드해주세요."
        ),
    )

def _run_full_script_job(job_id, project_id, ppt_text, presentation_time, style, extra_requirement, audience, topic):
    """[백그라운드] 실제 대본 생성 + DB 저장. 요청과 분리된 자체 세션을 열고, 끝나면 job_store에 결과를 기록한다.
    예외가 나도 프로세스가 죽지 않도록 여기서 모두 잡아 '실패'로 표시한다."""
    db = job_session_factory()
    try:
        result = full_generator.generate_full_script(
            ppt_text, presentation_time, style, extra_requirement, audience, topic
        )
        if not result or not result.get("slides"):
            job_store.fail_job(job_id, "대본 생성에 실패했습니다.")
            return

        project = db.get(models.Project, project_id)
        if not project:
            job_store.fail_job(job_id, "프로젝트를 찾을 수 없습니다.")
            return

        # PPT 기반 프로젝트는 보통 원본 슬라이드 수와 생성된 슬라이드 수가 같지만,
        # topic/outline만으로 만든 프로젝트(원본 슬라이드 1개)는 모델이 알아서 여러 슬라이드로 쪼개 생성하기도 한다.
        # 이 경우 기존에 없는 슬라이드 번호는 새로 만들어서(upsert) 생성 결과가 유실되지 않게 한다.
        # 새로 만든 슬라이드에는 원본 브리프(주제/목차)를 source_content로 복사해, 재생성 시 근거가 남게 한다.
        base_source = next((s.source_content for s in project.slides if s.source_content), "")
        slides_by_number = {s.slide_number: s for s in project.slides}
        for item in result["slides"]:
            try:
                slide_number = int(item["slide_number"])
            except (KeyError, TypeError, ValueError):
                continue
            slide = slides_by_number.get(slide_number)
            if slide:
                slide.script = item["script"]
            else:
                new_slide = models.Slide(
                    project_id=project.id, slide_number=slide_number,
                    source_content=base_source, script=item["script"],
                )
                db.add(new_slide)
                slides_by_number[slide_number] = new_slide
        db.commit()

        job_store.complete_job(job_id, {"project_id": project_id, "data": result})
    except Exception as e:
        print(f"❌ 대본 생성 작업({job_id}) 처리 중 오류: {e}")
        job_store.fail_job(job_id, "대본 생성 중 서버 오류가 발생했습니다.")
    finally:
        db.close()


@api.post("/api/script/full", status_code=status.HTTP_202_ACCEPTED)
async def create_full_script(request: FullScriptRequest, db: Session = Depends(get_db)):
    """[대본 전체 생성 시작 API] 생성은 20~30초 걸리는 무거운 작업이라 요청을 붙잡지 않는다.
    접수번호(job_id)를 즉시 돌려주고 실제 생성은 백그라운드에서 진행하며, 프론트는
    GET /api/script/jobs/{job_id}로 상태를 물어본다(완료되면 결과 포함)."""
    project = db.get(models.Project, request.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    if not project.slides:
        raise HTTPException(status_code=422, detail="이 프로젝트에 추출된 슬라이드가 없습니다.")

    # 생성에 필요한 입력은 지금(요청 세션이 살아있을 때) 값으로 다 뽑아둔다.
    # 백그라운드 작업은 이 세션을 쓰지 않고 자체 세션을 새로 연다.
    # source_content가 None인 슬라이드(이전 라운드 upsert로 생긴 것)는 "Slide 2: None"이 되지 않게 빈 문자열로 방어.
    ppt_text = "\n".join(f"Slide {s.slide_number}: {s.source_content or ''}" for s in project.slides)
    # 발표 주제: 요청으로 넘어온 값이 있으면 우선, 없으면 프로젝트 생성 때 저장한 주제를 쓴다.
    topic = (request.topic or "").strip() or (project.topic or "")
    project_id = project.id

    # 여기까지 요청 세션은 읽기만 했다. 백그라운드 작업이 자체 세션으로 DB에 쓰기 전에
    # 이 읽기 트랜잭션을 명시적으로 닫아 읽기 락을 놓는다(운영 이점 + 테스트의 공유 커넥션 충돌 방지).
    db.rollback()

    job_id = job_store.create_job()
    job_executor.submit(
        _run_full_script_job,
        job_id, project_id, ppt_text, request.presentation_time,
        request.style, request.extra_requirement, request.audience, topic,
    )
    return {"success": True, "job_id": job_id, "status": "processing"}


@api.get("/api/script/jobs/{job_id}")
async def get_script_job(job_id: str):
    """[대본 생성 상태 조회 API] 프론트가 스피너를 돌리며 1~2초마다 폴링한다.
    status: processing(처리중) / completed(완료, data 포함) / failed(실패, error 포함)."""
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="해당 작업을 찾을 수 없습니다.")

    response = {"success": True, "job_id": job_id, "status": job["status"]}
    if job["status"] == "completed":
        # 완료 시 응답은 기존 동기 방식과 동일한 모양({project_id, data})을 그대로 담는다.
        response["project_id"] = job["data"]["project_id"]
        response["data"] = job["data"]["data"]
    elif job["status"] == "failed":
        response["error"] = job["error"]
    return response

@api.post("/api/script/partial")
async def create_partial_script(request: PartialScriptRequest, db: Session = Depends(get_db)):
    """[대본 부분 재생성 API] project_id + target_slide로 대상을 지정하고, style은 "격식체"/"편안한 말투" 중 하나,
    extra_requirement는 선택 입력(공백 가능). 기존 대본 전문을 다시 보낼 필요 없이 DB에 저장된 걸 그대로 쓴다."""
    project = db.get(models.Project, request.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

    target_slide = next((s for s in project.slides if s.slide_number == request.target_slide), None)
    if not target_slide:
        raise HTTPException(status_code=404, detail=f"슬라이드 {request.target_slide}를 찾을 수 없습니다.")

    original_script = _compiled_script_text(project)
    if not original_script:
        raise HTTPException(status_code=422, detail="먼저 /api/script/full로 전체 대본을 생성해주세요.")

    result = await run_in_threadpool(
        partial_generator.generate_partial_script,
        original_script,
        request.target_slide,
        request.style,
        request.extra_requirement,
        request.audience,
    )

    if not result or "script" not in result:
        raise HTTPException(status_code=502, detail="대본 부분 재생성에 실패했습니다.")

    target_slide.script = result["script"]
    db.commit()

    return {"success": True, "project_id": project.id, "data": result}

@api.post("/api/analysis/words")
async def extract_and_convert_words(request: AnalysisRequest, db: Session = Depends(get_db)):
    """[발음 주의 단어 추출 및 G2P 변환 API] project_id의 생성된 대본을 분석 대상으로 삼는다."""
    project = db.get(models.Project, request.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

    script_text = _compiled_script_text(project)
    if not script_text:
        raise HTTPException(status_code=422, detail="먼저 /api/script/full로 전체 대본을 생성해주세요.")

    # "Slide N:" 라벨은 대본 내용이 아니므로 분석 전에 떼어낸다 (안 그러면 "Slide"가 외국어로 잡힘).
    analysis_text = re.sub(r"Slide \d+:", " ", script_text)

    # 단어 추출은 3단계 폴백 체인:
    # 1) ETRI WiseNLU (키가 있을 때만 — 없으면 즉시 빈 리스트)
    # 2) Kiwi 로컬 형태소 분석기 (키 불필요, 현재 주력. ETRI 키 발급되면 1)이 우선함)
    # 3) 빈도 기반 휴리스틱 (Kiwi 로드까지 실패한 극단적 상황의 최후 방어)
    extracted_words = etri_analyzer.extract_difficult_words(analysis_text)
    if not extracted_words:
        extracted_words = kiwi_analyzer.extract_difficult_words(analysis_text)
    if not extracted_words:
        print("⚠️ ETRI/Kiwi 모두 결과 없음. 빈도 기반 휴리스틱 폴백을 사용합니다.")
        extracted_words = _fallback_difficult_words(script_text)

    # 단어마다 표준국어대사전 조회가 들어가므로, 상한을 둬서 외부 API 폭주를 막는다.
    if len(extracted_words) > MAX_DIFFICULT_WORDS:
        print(f"⚠️ 발음 주의 단어가 {len(extracted_words)}개라 상한({MAX_DIFFICULT_WORDS}개)까지만 분석합니다.")
        extracted_words = extracted_words[:MAX_DIFFICULT_WORDS]

    # 2. G2P 모듈로 발음 기호 획득
    final_result = g2p_converter.convert_words(extracted_words)

    # 3. 장단음/연음/표기-발음불일치 카테고리 분류 (하이라이트용)
    summary = {category: 0 for category in DIFFICULT_WORD_CATEGORIES}
    words_payload = []
    for item in final_result:
        category = _classify_word_category(item["word"], item.get("is_different", False))
        if category:
            summary[category] += 1
        words_payload.append({**item, "category": category})

    # 4. 이 프로젝트의 기존 단어 목록을 최신 결과로 교체 (현재 대본 기준 스냅샷)
    db.query(models.DifficultWord).filter(models.DifficultWord.project_id == project.id).delete()
    for item in words_payload:
        db.add(models.DifficultWord(project_id=project.id, word=item["word"], phoneme=item["phoneme"], category=item["category"]))
    db.commit()

    return {"success": True, "project_id": project.id, "data": {"words": words_payload, "summary": summary}}

@api.post("/api/evaluation/audio")
async def evaluate_pronunciation(
    project_id: int = Form(...),
    reference_text: str = Form(None),
    audio_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """[사용자 음성 발음 평가 API] 사용자의 오디오 파일(WAV/MP3/M4A)과 project_id를 받아 점수를 매기고,
    평가 결과를 히스토리로 저장합니다. reference_text를 안 주면 DB에 저장된 대본 전체를 기준으로 평가합니다."""

    # 임시 파일로 오디오 저장 (Azure SDK가 물리적인 파일 경로를 요구함)
    temp_file_path = _safe_temp_path(audio_file.filename)
    wav_file_path = None
    try:
        # 업로드된 파일을 로컬 디스크에 임시 복사 (크기/타입 제한 적용)
        _save_upload_with_limit(audio_file, temp_file_path, MAX_AUDIO_SIZE_BYTES, ALLOWED_AUDIO_EXTENSIONS)

        project = db.get(models.Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

        text_to_evaluate = reference_text or _compiled_script_text(project)
        if not text_to_evaluate:
            raise HTTPException(status_code=422, detail="reference_text가 없고, 이 프로젝트에 생성된 대본도 없습니다.")

        # Azure Pronunciation Assessment는 16kHz mono PCM WAV를 요구하므로,
        # WAV가 아니면(MP3/M4A) ffmpeg로 변환한 뒤 그 결과 파일을 평가에 사용한다.
        audio_path_for_evaluation = temp_file_path
        if os.path.splitext(audio_file.filename or "")[1].lower() != ".wav":
            wav_file_path = _safe_temp_path("converted.wav")
            if not audio_converter.convert_to_wav(temp_file_path, wav_file_path):
                raise HTTPException(status_code=502, detail="오디오 파일을 변환하지 못했습니다. 파일이 손상되지 않았는지 확인해주세요.")
            audio_path_for_evaluation = wav_file_path

        # Azure 평가 모듈 호출
        result = await run_in_threadpool(azure_evaluator.evaluate_audio, audio_path_for_evaluation, text_to_evaluate)

        # 다른 엔드포인트(/api/script/*)와 동일하게, 실패는 200이 아닌 502로 알린다.
        if result.get("status") != "success":
            raise HTTPException(status_code=502, detail=result.get("message", "발음 평가에 실패했습니다."))

        # 점수는 백엔드에서 소수 1자리(0~100)로 정리해서 내려준다. 프론트는 이 숫자를 그대로 표시만 한다.
        # (0~5점으로 뭉개지 않고 소수점까지 자세히. Azure는 소수 점수를 주고, evaluate_audio는 overall_scores 키로 반환한다.)
        _round_scores_in_place(result)
        scores = result.get("overall_scores", {})
        evaluation = models.PronunciationEvaluation(
            project_id=project.id,
            accuracy_score=scores.get("accuracy"),
            fluency_score=scores.get("fluency"),
            completeness_score=scores.get("completeness"),
            pronunciation_score=scores.get("pronunciation_score"),
            words_detail=result.get("words_detail"),
        )
        db.add(evaluation)
        db.commit()
        db.refresh(evaluation)

        return {"success": True, "project_id": project.id, "evaluation_id": evaluation.id, **result}
    except HTTPException:
        raise
    except Exception as e:
        # 내부 예외 메시지를 클라이언트에 그대로 노출하지 않는다 (구현 세부 유출 방지). 상세는 서버 로그에만.
        print(f"❌ /api/evaluation/audio 처리 중 예기치 못한 오류: {e}")
        raise HTTPException(status_code=502, detail="발음 평가 처리 중 서버 오류가 발생했습니다.")
    finally:
        # 평가가 완료되었거나 에러가 났더라도, 서버 용량 낭비를 막기 위해 임시 파일 삭제.
        # Windows에서 파일 핸들이 아직 잡혀 있으면 os.remove가 PermissionError를 낼 수 있는데,
        # finally에서 예외가 나면 이미 raise된 HTTPException을 덮어써 500으로 바뀌므로 개별적으로 방어한다.
        for path in (temp_file_path, wav_file_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError as cleanup_err:
                    print(f"⚠️ 임시 파일 삭제 실패({path}): {cleanup_err}")

@api.get("/api/projects")
async def list_projects(db: Session = Depends(get_db)):
    """[프로젝트 히스토리 목록 API]"""
    projects = db.query(models.Project).order_by(models.Project.created_at.desc()).all()
    return {
        "success": True,
        "data": [
            {
                "id": p.id,
                "name": p.name,
                "topic": p.topic,
                "slide_count": len(p.slides),
                "created_at": p.created_at.isoformat(),
            }
            for p in projects
        ],
    }

@api.get("/api/evaluations")
async def list_evaluations(db: Session = Depends(get_db)):
    """[발표 코칭 내역 API] 프로젝트 구분 없이 지난 발음 평가 결과를 최신순으로 나열한다.
    마이페이지 '발표 코칭 내역' 화면이 프로젝트를 하나씩 열어보지 않고 한 번에 보도록."""
    evaluations = (
        db.query(models.PronunciationEvaluation)
        .order_by(models.PronunciationEvaluation.created_at.desc())
        .all()
    )
    return {
        "success": True,
        "data": [
            {
                "id": e.id,
                "project_id": e.project_id,
                "project_name": e.project.name if e.project else None,
                "accuracy_score": e.accuracy_score,
                "fluency_score": e.fluency_score,
                "completeness_score": e.completeness_score,
                "pronunciation_score": e.pronunciation_score,
                "created_at": e.created_at.isoformat(),
            }
            for e in evaluations
        ],
    }

@api.get("/api/projects/{project_id}")
async def get_project(project_id: int, db: Session = Depends(get_db)):
    """[프로젝트 상세 조회 API] 슬라이드별 대본, 발음 주의 단어, 평가 히스토리를 함께 반환합니다."""
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

    return {
        "success": True,
        "data": {
            "id": project.id,
            "name": project.name,
            "filename": project.filename,
            "topic": project.topic,
            "keywords": project.keywords,
            "created_at": project.created_at.isoformat(),
            "slides": [
                {"slide_number": s.slide_number, "source_content": s.source_content, "script": s.script}
                for s in project.slides
            ],
            "difficult_words": [{"word": w.word, "phoneme": w.phoneme, "category": w.category} for w in project.difficult_words],
            "evaluations": [
                {
                    "id": e.id,
                    "accuracy_score": e.accuracy_score,
                    "fluency_score": e.fluency_score,
                    "completeness_score": e.completeness_score,
                    "pronunciation_score": e.pronunciation_score,
                    "created_at": e.created_at.isoformat(),
                }
                for e in project.evaluations
            ],
        },
    }

@api.delete("/api/projects/{project_id}")
async def delete_project(project_id: int, db: Session = Depends(get_db)):
    """[프로젝트(기록) 삭제 API] 마이페이지에서 지난 기록을 지운다. 슬라이드·발음 주의 단어·평가 이력은
    관계 cascade로 함께 삭제된다."""
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

    db.delete(project)
    db.commit()
    return {"success": True, "deleted_project_id": project_id}

def _slides_payload(project: "models.Project") -> list:
    """편집 API들이 공통으로 돌려주는 슬라이드 목록(번호 순)."""
    return [
        {"slide_number": s.slide_number, "source_content": s.source_content, "script": s.script}
        for s in sorted(project.slides, key=lambda s: s.slide_number)
    ]


def _resequence(slides: list) -> None:
    """정렬된 슬라이드 리스트를 받아 slide_number를 1..N으로 다시 매긴다(추가/삭제 후 빈 번호 방지)."""
    for index, slide in enumerate(slides, start=1):
        slide.slide_number = index


@api.put("/api/projects/{project_id}/slides/{slide_number}")
async def update_slide_script(project_id: int, slide_number: int, request: SlideUpdateRequest, db: Session = Depends(get_db)):
    """[대본 편집 저장 API] 결과 화면에서 사용자가 직접 고친 대본을 저장한다(피그마 05 '수동/자동 저장').
    PPT O는 슬라이드별로, PPT X는 1번 슬라이드로 저장한다."""
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

    slide = next((s for s in project.slides if s.slide_number == slide_number), None)
    if not slide:
        raise HTTPException(status_code=404, detail="해당 번호의 슬라이드를 찾을 수 없습니다.")

    slide.script = request.script
    if request.source_content is not None:
        slide.source_content = request.source_content
    db.commit()
    db.refresh(project)

    return {"success": True, "project_id": project.id, "data": {"slides": _slides_payload(project)}}


@api.post("/api/projects/{project_id}/slides")
async def add_slide(project_id: int, request: SlideCreateRequest, db: Session = Depends(get_db)):
    """[슬라이드 추가 API] position 자리에 새 슬라이드를 끼워넣고 뒤 번호를 하나씩 민다(피그마 05-1).
    position이 없으면 맨 끝에 추가한다. 추가 후 전체 번호는 1..N으로 유지된다."""
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

    ordered = sorted(project.slides, key=lambda s: s.slide_number)
    # position은 1-based. 범위를 벗어나면 맨 끝으로 클램프한다(잘못된 값에도 유실 없이 추가).
    if request.position is None or request.position > len(ordered):
        insert_index = len(ordered)
    else:
        insert_index = max(0, request.position - 1)

    new_slide = models.Slide(
        project_id=project.id,
        slide_number=insert_index + 1,  # _resequence가 곧 다시 매기므로 임시값
        source_content=request.source_content or "",
        script=request.script or "",
    )
    ordered.insert(insert_index, new_slide)
    db.add(new_slide)
    _resequence(ordered)
    db.commit()
    db.refresh(project)

    return {"success": True, "project_id": project.id, "added_slide_number": insert_index + 1, "data": {"slides": _slides_payload(project)}}


@api.delete("/api/projects/{project_id}/slides/{slide_number}")
async def delete_slide(project_id: int, slide_number: int, db: Session = Depends(get_db)):
    """[슬라이드 삭제 API] 슬라이드를 지우고 남은 번호를 1..N으로 다시 매긴다(피그마 05-1).
    마지막 한 장은 지울 수 없다(대본이 0장이 되면 생성/평가가 불가능하므로)."""
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

    slide = next((s for s in project.slides if s.slide_number == slide_number), None)
    if not slide:
        raise HTTPException(status_code=404, detail="해당 번호의 슬라이드를 찾을 수 없습니다.")
    if len(project.slides) <= 1:
        raise HTTPException(status_code=422, detail="최소 1개의 슬라이드는 있어야 합니다.")

    deleted_id = slide.id  # flush 이후엔 삭제된 인스턴스 접근이 불안정하므로 미리 잡아둔다
    db.delete(slide)
    db.flush()  # 세션에서 실제로 빠져야 아래 재번호가 올바르게 매겨진다
    remaining = sorted((s for s in project.slides if s.id != deleted_id), key=lambda s: s.slide_number)
    _resequence(remaining)
    db.commit()
    db.refresh(project)

    return {"success": True, "project_id": project.id, "data": {"slides": _slides_payload(project)}}


app.include_router(api)

# 6. 서버 실행 코드
if __name__ == "__main__":
    print("🌟 SpeaKO AI 서버를 시작합니다... (http://localhost:8000)")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)