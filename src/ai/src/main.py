import sys

# Windows 콘솔의 기본 코드페이지(cp949)는 이모지를 인코딩하지 못해
# print() 호출 시 서버가 부팅도 되기 전에 죽는다. UTF-8로 강제 전환.
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from fastapi import FastAPI, APIRouter, Request, UploadFile, File, Form, Header, HTTPException, Depends, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Literal, Optional
from concurrent.futures import ThreadPoolExecutor
import uvicorn
import os
import re
import uuid
import hmac
from urllib.parse import quote

# 1. 분리해둔 AI 클라이언트 모듈들 임포트
from clova.full_generation.generator import FullScriptGenerator
from clova.partial_generation.generator import PartialScriptGenerator
from clova.feedback.generator import (
    PronunciationFeedbackGenerator,
    collect_weak_words,
    collect_strong_words,
    normalize_practice_tips,
)
from etri.etri_client import EtriLanguageAnalyzer
from nlp.kiwi_analyzer import KiwiAnalyzer
from g2p.g2p_client import G2pConverter
from azure_speech.azure_client import PronunciationEvaluator
from tts.clova_voice_client import ClovaVoiceClient
from tts import tts_cache
from tts.voices import VOICES, SPEED_MIN, SPEED_MAX, speaker_code
from utils.ppt_extractor import PptExtractor
from utils import pdf_extractor
from utils import docx_extractor
from utils import audio_converter
from utils.text_heuristics import extract_frequent_terms
from utils.stdict_client import StdictClient
from utils.hangul_phonology import has_liaison_pattern
from utils import phonology_rules
from utils import score_grade
from utils import docx_builder
from utils.body_limit import MaxBodySizeMiddleware
from utils.rate_limit import RateLimitMiddleware
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


@app.exception_handler(RequestValidationError)
async def _log_validation_errors(request: Request, exc: RequestValidationError):
    """422를 서버 콘솔에도 남긴다 (응답은 FastAPI 기본 그대로).

    왜: 연동 테스트에서 스프링이 422를 받고도 응답 본문을 안 읽고 재시도만 반복하면,
    우리 쪽에선 "422가 났다"는 사실만 보이고 **무엇이 틀렸는지**는 안 보인다(FastAPI는
    검증 실패를 콘솔에 안 찍는다). 실측(2026-08-12): 같은 422가 3연발로 오는데 원인을
    상대방 화면 캡처 없이는 알 수 없었다. 서버 로그만 보고 원인을 짚을 수 있게 남긴다.
    """
    errors = [
        {"field": ".".join(str(part) for part in e.get("loc", [])), "msg": e.get("msg")}
        for e in exc.errors()
    ]
    body_preview = str(exc.body)[:300] if exc.body is not None else "(없음)"
    print(f"⚠️ 422 검증 실패: {request.method} {request.url.path}")
    print(f"   틀린 필드: {errors}")
    print(f"   받은 본문: {body_preview}")
    return await request_validation_exception_handler(request, exc)

# 3. CORS(교차 출처 리소스 공유) 설정
# 배포된 프론트엔드(Vercel)에서 브라우저가 직접 이 API를 부르면, 허용 목록에 없는 출처는
# 브라우저가 응답을 통째로 차단한다. localhost만 하드코딩해두면 배포 환경에서 전부 막히므로
# 환경변수로 열어둔다. (쉼표로 여러 개, 예: CORS_ALLOW_ORIGINS=https://speakofront.vercel.app,https://speako.app)
DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost:3000",   # CRA/Next 기본
    "http://localhost:5173",   # Vite 기본
    "https://speakofront.vercel.app",  # 배포된 프론트엔드
]


def _parse_origins(raw: str):
    """쉼표로 구분된 출처 목록을 파싱한다. 값이 없으면 기본 목록을 쓴다."""
    parsed = [origin.strip().rstrip("/") for origin in (raw or "").split(",") if origin.strip()]
    return parsed or list(DEFAULT_ALLOWED_ORIGINS)


origins = _parse_origins(os.getenv("CORS_ALLOW_ORIGINS", ""))

# 업로드 파일 제한 (DoS 방지)
MAX_PPT_SIZE_BYTES = 20 * 1024 * 1024   # 20MB
MAX_AUDIO_SIZE_BYTES = 20 * 1024 * 1024  # 20MB
# 녹음 **길이** 상한. 크기 상한과 별개로 필요하다.
#
# 왜 크기만으로는 안 되나: 같은 20MB라도 비트레이트에 따라 재생 길이가 3배 넘게 벌어진다
# (실측: 124kbps m4a는 21분, 브라우저 webm/opus 40kbps는 67분). 그런데 Azure 발음 평가는
# **오디오 길이에 비례**해서 시간을 먹으므로(실측: 5분 녹음 → 148초, 실시간의 약 0.48배),
# 크기로만 막으면 처리 시간이 사실상 무제한이 된다.
#
# 이 값을 넘는 파일은 **Azure를 부르기 전에** 거절한다 — 안 그러면 돈은 다 쓰고 뒷부분이
# 잘린 결과를 돌려주게 된다. 15분이면 평가에 약 7분 걸린다(15×60×0.48 ≈ 435초).
MAX_AUDIO_DURATION_SECONDS = 15 * 60
# 요청 본문 전체의 상한. 가장 큰 허용 파일(PPT 20MB)에 multipart 경계·필드 오버헤드를 더한 값.
# 파일 단위 제한(_save_upload_with_limit)은 본문이 이미 전부 파싱된 뒤에야 도므로 이것만으론
# 부족하다 — 그 전에 ASGI 레벨에서 끊는 용도다. (utils/body_limit.py 주석 참고)
MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_MB", "25")) * 1024 * 1024
# 구버전 `.ppt`(2007년 이전 OLE 바이너리)는 **지원하지 않는다**(사용자 결정, 2026-08-09).
# python-pptx가 OOXML만 읽어서 변환기(LibreOffice)를 서버에 얹어야 하는데, 시연까지 남은
# 기간에 배포 이미지를 키울 만한 가치가 없다고 판단했다. 대신 올렸을 때 무엇을 하면 되는지
# 알려준다(_save_upload_with_limit의 415 안내 참고).
ALLOWED_PPT_EXTENSIONS = {".pptx", ".pdf"}
ALLOWED_COACHING_EXTENSIONS = {".docx", ".txt", ".pdf"}
# 브라우저 MediaRecorder는 기본적으로 webm/opus(Chrome·Firefox·Edge)나 mp4/m4a(Safari)로 녹음한다.
# ffmpeg가 이 포맷들을 전부 16kHz mono WAV로 변환하므로(convert_to_wav), 프론트가 녹음한 걸 그대로 받는다.
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".webm"}
UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1MB

# 텍스트 입력 길이 상한 (비용·DoS 방지)
# 파일 크기 제한과 같은 이유다. 아래 값들은 대부분 그대로 HCX 프롬프트에 실려 나가므로,
# 상한이 없으면 호출 한 번으로 유료 토큰을 무제한 태울 수 있다. 파일 업로드만 막아두고
# 본문 텍스트를 열어두면 제한을 우회하는 셈이라 같이 막는다.
# 값은 "정상 사용자가 절대 넘지 않을 선"으로 잡았다 — 20분 발표 대본이 한국어 약 6,000자다.
MAX_SCRIPT_TEXT_LEN = 50_000        # 붙여넣기/파일로 받는 대본 전문
MAX_SLIDE_SCRIPT_LEN = 20_000       # 슬라이드 한 장 분량
MAX_OUTLINE_LEN = 5_000             # 목차/가이드라인
MAX_TOPIC_LEN = 200                 # 발표 주제 (한 줄)
MAX_AUDIENCE_LEN = 100              # 발표 대상 (예: "교수님", "면접관")
MAX_EXTRA_REQUIREMENT_LEN = 1_000   # 추가 요구사항 자유 텍스트
MAX_PROJECT_NAME_LEN = 200
# TTS 합성 대상 텍스트. Clova Voice는 **글자 수만큼 과금**되므로 상한이 곧 호출 1회당 비용 상한이다.
# 이 엔드포인트는 단어 목록의 스피커 버튼용이라 실제로는 2~5자면 충분하다.
MAX_TTS_TEXT_LEN = 100
# 발표 시간은 생성할 대본 분량을 좌우한다 = 토큰 비용에 직결된다.
MAX_PRESENTATION_MINUTES = 180
# 대본 생성은 슬라이드 한 장당 HCX 호출 1회다. 길이 상한은 장당 분량만 막지 개수는 못 막으므로,
# 500페이지 PDF가 500회 호출로 이어지지 않도록 개수 자체에 상한을 둔다.
MAX_SLIDES_PER_PROJECT = int(os.getenv("MAX_SLIDES_PER_PROJECT", "100"))

# 미들웨어는 **나중에 추가한 것이 바깥쪽**에 감싸인다(Starlette). CORS를 마지막에 붙여야
# 본문 크기 초과(413) 응답에도 CORS 헤더가 붙어서 브라우저가 에러 내용을 읽을 수 있다.
# 순서를 바꾸면 배포 프론트에서 413이 그냥 "네트워크 오류"로 보인다.
app.add_middleware(MaxBodySizeMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)
app.add_middleware(
    RateLimitMiddleware,
    enabled=os.getenv("RATE_LIMIT_ENABLED", "1") not in ("0", "false", "False"),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    # Vercel 프리뷰 배포는 커밋마다 도메인이 바뀌므로(<프로젝트>-<해시>-<팀>.vercel.app) 정규식으로 함께 허용한다.
    allow_origin_regex=os.getenv("CORS_ALLOW_ORIGIN_REGEX", r"https://.*\.vercel\.app"),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


def _save_upload_with_limit(upload_file: UploadFile, dest_path: str, max_bytes: int, allowed_extensions: set,
                            hint: str = ""):
    """확장자를 검사하고, 크기 제한을 지키며 업로드 파일을 디스크에 저장합니다.

    hint: 상한을 넘었을 때 "그래서 뭘 하면 되는지"를 덧붙인다. 크기만 알려주면 사용자는
          파일을 줄일 방법을 스스로 찾아야 한다.
    """
    ext = os.path.splitext(upload_file.filename or "")[1].lower()
    if ext not in allowed_extensions:
        # 구버전 .ppt는 흔한 실수라 따로 안내한다. 목록만 보여주면 사용자는 왜 자기 PPT가
        # 거절됐는지 모른다(확장자가 .ppt인 걸 인식 못 하는 경우가 많다).
        if ext == ".ppt":
            raise HTTPException(
                status_code=415,
                detail=(
                    "구버전 .ppt 형식은 지원하지 않습니다. "
                    "PowerPoint에서 '다른 이름으로 저장 → PowerPoint 프레젠테이션(.pptx)'으로 "
                    "바꿔서 올려주세요."
                ),
            )
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
                    detail=f"파일 크기가 너무 큽니다. (최대 {max_bytes // (1024 * 1024)}MB){hint}"
                )
            buffer.write(chunk)

# 4. 각 AI 모듈 객체 생성
full_generator = FullScriptGenerator()
partial_generator = PartialScriptGenerator()
etri_analyzer = EtriLanguageAnalyzer()
kiwi_analyzer = KiwiAnalyzer()
g2p_converter = G2pConverter()
azure_evaluator = PronunciationEvaluator()
feedback_generator = PronunciationFeedbackGenerator()
ppt_extractor = PptExtractor()
stdict_client = StdictClient()
tts_client = ClovaVoiceClient()

# 5. DB 테이블 생성 (없으면 생성, 있으면 그대로 둠)
init_db()

# 작업 상태는 DB에 있지만 작업을 돌리는 스레드풀은 프로세스 안에 있다. 서버가 죽으면 그때
# 'processing'이던 작업은 아무도 이어받지 않으므로, 그대로 두면 프론트가 영원히 폴링한다.
# 부팅 시 한 번 정리한다.
_stale_jobs = job_store.fail_stale_jobs()
if _stale_jobs:
    print(f"🧹 재시작 전에 중단된 대본 생성 작업 {_stale_jobs}건을 실패로 정리했습니다.")


def _fallback_difficult_words(script_text: str, top_n: int = 6) -> list:
    """ETRI 키가 없거나 호출이 실패했을 때, 실제 대본 내용에서 빈도 기반으로 발음 주의 단어 후보를 뽑는다."""
    cleaned = re.sub(r"Slide \d+:", "", script_text)
    return extract_frequent_terms([cleaned], top_n=top_n, min_length=2)


DIFFICULT_WORD_CATEGORIES = ("장단음", "연음", "표기-발음불일치")
# 한 번의 /api/analysis/words 요청이 외부 사전 API를 무제한으로 때리지 않도록 상한.
# (단어마다 표준국어대사전 조회가 들어가므로, 대본이 아주 길어도 앞쪽 N개까지만 분류한다)
MAX_DIFFICULT_WORDS = 40


def _classify_word_category(word: str, is_different: bool, long_vowel_positions=()):
    """
    발음 주의 단어를 장단음/연음/표기-발음불일치 3가지로 분류한다.
    - 장단음: 표준국어대사전에 등록된 발음에 장음 표시(ː)가 있는 경우.
              모음 길이는 한글 철자에는 드러나지 않으므로(예: 밤/밤ː는 표기가 같음),
              G2P의 철자≠발음(is_different) 여부와 **무관하게** 독립적으로 판정한다.
    - 연음: (철자≠발음이면서) 받침+무초성 음절 구조가 있어 받침이 다음 음절로 넘어가는 경우.
    - 표기-발음불일치: 위 둘에 해당하지 않는 철자≠발음 (비음화/경음화 등).
    철자=발음이고 장단음도 아니면 분류하지 않는다(None).

    long_vowel_positions는 호출부가 이미 조회해둔 결과를 넘기기 위한 것이다(같은 단어로
    표준국어대사전을 두 번 때리지 않으려고). 안 넘기면 여기서 직접 조회한다.
    """
    if long_vowel_positions or stdict_client.has_long_vowel(word):
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
# 어투 허용값. 공식은 "formal"/"casual"(스프링 연동 합의, 2026-08-12).
# 한국어 값은 기존 문서·프론트 호환용 — clova/styles.py의 STYLE_ALIASES가 매핑한다.
StyleValue = Literal["formal", "casual", "격식체", "편안한 말투"]


class FullScriptRequest(BaseModel):
    project_id: int = Field(..., ge=1)
    presentation_time: int = Field(..., ge=1, le=MAX_PRESENTATION_MINUTES)
    style: StyleValue
    extra_requirement: Optional[str] = Field("", max_length=MAX_EXTRA_REQUIREMENT_LEN)
    audience: Optional[str] = Field("", max_length=MAX_AUDIENCE_LEN)  # 발표 대상/청중 (피그마 '대상' 필드, 예: 교수님/면접관). 선택 입력.
    topic: Optional[str] = Field("", max_length=MAX_TOPIC_LEN)  # 발표 주제. 비우면 프로젝트에 저장된 주제(생성 시 입력)를 사용한다.

class PartialScriptRequest(BaseModel):
    project_id: int = Field(..., ge=1)
    target_slide: int = Field(..., ge=1)
    style: StyleValue
    extra_requirement: Optional[str] = Field("", max_length=MAX_EXTRA_REQUIREMENT_LEN)
    audience: Optional[str] = Field("", max_length=MAX_AUDIENCE_LEN)  # 발표 대상/청중. 선택 입력.

class AnalysisRequest(BaseModel):
    project_id: int = Field(..., ge=1)

class ProjectUpdateRequest(BaseModel):
    """프로젝트명 수정(피그마 AI Script Edit Page ⑬). 이 이름이 docx 다운로드 파일명이 된다."""
    name: str = Field(..., min_length=1, max_length=MAX_PROJECT_NAME_LEN)

class TtsWordRequest(BaseModel):
    """단어 목록 화면의 스피커 버튼 → 이 단어를 소리로 들려준다.

    `pronunciation`을 주면 그걸 그대로 합성하고, 없으면 서버가 발음을 찾아낸다
    (저장된 발음기호 → 즉석 G2P 순). 철자 그대로 들려주고 싶으면 `pronunciation`에
    철자를 넣으면 된다 — 정책이 바뀌어도 클라이언트가 선택할 수 있게 열어둔 자리다.
    """
    word: str = Field(..., min_length=1, max_length=MAX_TTS_TEXT_LEN)  # 철자 (화면에 보이는 단어)
    project_id: Optional[int] = Field(None, ge=1)  # 있으면 이 프로젝트에 저장된 발음기호를 먼저 찾는다
    pronunciation: Optional[str] = Field(None, max_length=MAX_TTS_TEXT_LEN)  # 직접 지정할 발음. '[여칼]' 형태도 허용
    # 목소리·속도 설정(2026-08-12 제품 확정: 남 동현/대성, 여 혜리/고은, 속도 -5~+5).
    # 목록은 GET /api/tts/voices로 받는다. 미지정이면 기존 기본 화자 그대로 — 설정 UI가
    # 아직 없는 배포 프론트가 이 API를 그대로 써도 동작이 바뀌지 않게 한다.
    voice: Optional[Literal["동현", "대성", "혜리", "고은"]] = None
    speed: int = Field(0, ge=SPEED_MIN, le=SPEED_MAX)  # 음수=빠르게, 양수=느리게, 0=보통

class SlideUpdateRequest(BaseModel):
    """결과 화면(피그마 05)에서 사용자가 직접 고친 대본을 저장할 때 쓴다. PPT O는 슬라이드별,
    PPT X는 1번 슬라이드(전체 대본 한 덩어리)를 이 API로 저장한다."""
    script: str = Field(..., max_length=MAX_SLIDE_SCRIPT_LEN)  # 사용자가 편집한 대본 본문 (빈 문자열 허용 — 내용을 비우는 것도 편집이다)
    source_content: Optional[str] = Field(None, max_length=MAX_SCRIPT_TEXT_LEN)  # 원문도 함께 고칠 일이 있으면 선택적으로 갱신

class SlideCreateRequest(BaseModel):
    """슬라이드 추가(피그마 05-1 '슬라이드 추가/삭제 가능'). position이 있으면 그 자리에 끼워넣고
    뒤 슬라이드 번호는 하나씩 밀린다. 없으면 맨 뒤에 붙인다."""
    position: Optional[int] = Field(None, ge=1)  # 1-based. 이 번호 자리에 삽입. None이면 맨 끝.
    script: Optional[str] = Field("", max_length=MAX_SLIDE_SCRIPT_LEN)
    source_content: Optional[str] = Field("", max_length=MAX_SCRIPT_TEXT_LEN)


def _attach_error_spans(words_detail, reference_text: str, recognized_text: str) -> None:
    """단어마다 원본/인식 텍스트 안에서의 위치(문자 오프셋)를 붙인다. 리스트를 제자리에서 수정한다.

    왜 필요한가: 피그마 Feedback Page는 틀린 부분을 **원본과 인식 양쪽에** 강조한다.
    `error_type`만으로는 같은 단어가 여러 번 나올 때 어느 것을 칠할지 정할 수 없다.

    Azure는 단어를 발화 순서대로 돌려주므로, 각 텍스트를 커서로 훑으며 앞에서부터 맞춰간다.
    - Omission(빠뜨림): 원본에만 있다 → recognized_span은 None
    - Insertion(덧붙임): 실제로 말한 것뿐이다 → reference_span은 None
    찾지 못하면(문장부호·정규화 차이 등) 억지로 맞추지 않고 None으로 둔다 — 엉뚱한 곳을
    칠하느니 칠하지 않는 편이 낫다.
    """
    if not words_detail:
        return

    reference_text = reference_text or ""
    recognized_text = recognized_text or ""
    ref_cursor = 0
    rec_cursor = 0

    for entry in words_detail:
        word = (entry.get("word") or "").strip()
        error_type = entry.get("error_type")
        entry["reference_span"] = None
        entry["recognized_span"] = None
        if not word:
            continue

        if error_type != "Insertion":
            found = reference_text.find(word, ref_cursor)
            if found != -1:
                entry["reference_span"] = [found, found + len(word)]
                ref_cursor = found + len(word)

        if error_type != "Omission":
            found = recognized_text.find(word, rec_cursor)
            if found != -1:
                entry["recognized_span"] = [found, found + len(word)]
                rec_cursor = found + len(word)


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


def _extract_coaching_text(temp_file_path: str, ext: str) -> str:
    """완성된 대본 파일(DOCX/TXT/PDF) → 전체 텍스트. 동기 블로킹(디스크 + 파싱)이라 스레드풀에서 부른다."""
    if ext == ".pdf":
        return pdf_extractor.extract_full_text(temp_file_path)
    if ext == ".docx":
        return docx_extractor.extract_full_text(temp_file_path)
    with open(temp_file_path, "rb") as f:
        return f.read().decode("utf-8", errors="ignore")


def _extract_slide_data(temp_file_path: str, ext: str, topic_hint: str, outline_hint: str) -> dict:
    """슬라이드 파일(PPT/PPTX/PDF) → 구조화 데이터. 동기 블로킹이라 스레드풀에서 부른다.

    PPTX 경로는 이미지 전용 장표를 만나면 **HCX 비전을 유료로 호출**한다(슬라이드당 최대 3장).
    네트워크 왕복이 여러 번 일어나므로 이벤트 루프에서 직접 돌리면 안 된다.
    """
    if ext == ".pdf":
        return pdf_extractor.extract_structured_data(temp_file_path)
    return ppt_extractor.extract_structured_data(temp_file_path, topic_hint=topic_hint, outline_hint=outline_hint)


@api.post("/api/projects")
async def create_project(
    file: UploadFile = File(None),
    project_name: str = Form(None, max_length=MAX_PROJECT_NAME_LEN),
    mode: Literal["script", "coaching"] = Form("script"),
    topic: str = Form(None, max_length=MAX_TOPIC_LEN),
    outline: str = Form(None, max_length=MAX_OUTLINE_LEN),
    script_text: str = Form(None, max_length=MAX_SCRIPT_TEXT_LEN),
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
            await run_in_threadpool(
                _save_upload_with_limit, file, temp_file_path, MAX_PPT_SIZE_BYTES, ALLOWED_COACHING_EXTENSIONS
            )

            ext = os.path.splitext(file.filename or "")[1].lower()
            try:
                text = await run_in_threadpool(_extract_coaching_text, temp_file_path, ext)
            except Exception as e:
                # 손상되었거나 확장자만 바꾼 파일 등 — 라이브러리 예외를 그대로 500으로 내보내지 않고 422로 정직하게 알린다.
                print(f"❌ 코칭용 파일 파싱 실패({ext}): {e}")
                raise HTTPException(status_code=422, detail="파일을 열 수 없습니다. 파일이 손상되지 않았는지 확인해주세요.")
            text = text.strip()

            if not text:
                raise HTTPException(status_code=422, detail="파일에서 텍스트를 추출하지 못했습니다.")

            # script_text로 직접 붙여넣는 경로엔 상한이 걸려 있으므로, 파일로 우회해서 같은 내용을
            # 무제한으로 넣을 수 있으면 안 된다. 20MB짜리 txt는 수백만 자가 되고, 그게 대본으로
            # 저장되면 이후 생성·분석 호출마다 통째로 HCX 프롬프트에 실린다.
            # 잘라내지 않고 거절한다 — 조용히 자르면 사용자가 대본 뒷부분이 사라진 걸 모른다.
            if len(text) > MAX_SCRIPT_TEXT_LEN:
                raise HTTPException(
                    status_code=413,
                    detail=f"대본이 너무 깁니다. (최대 {MAX_SCRIPT_TEXT_LEN:,}자, 현재 {len(text):,}자)"
                )

            project = _create_project_from_script(db, project_name or os.path.splitext(file.filename or "project")[0], text)
            return {"success": True, "project_id": project.id, "data": {"metadata": {"topic": None, "keywords": []}, "slides": [{"slide_number": 1, "content": text}]}}
        finally:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)

    if file is not None:
        temp_file_path = _safe_temp_path(file.filename)
        try:
            await run_in_threadpool(
                _save_upload_with_limit, file, temp_file_path, MAX_PPT_SIZE_BYTES, ALLOWED_PPT_EXTENSIONS
            )

            ext = os.path.splitext(file.filename or "")[1].lower()
            try:
                result = await run_in_threadpool(
                    _extract_slide_data, temp_file_path, ext, topic or "", outline or ""
                )
            except Exception as e:
                print(f"❌ 슬라이드 파일 파싱 실패({ext}): {e}")
                raise HTTPException(status_code=422, detail="파일을 열 수 없습니다. 파일이 손상되지 않았는지 확인해주세요.")

            if not result["slides"]:
                raise HTTPException(status_code=422, detail="파일에서 텍스트를 추출하지 못했습니다.")

            # 대본 생성은 슬라이드 한 장당 HCX 호출 1회다. 길이 상한은 장당 분량만 막으므로,
            # 500페이지 PDF를 올리면 20MB 안쪽이어도 500회 호출이 나간다. 개수도 막는다.
            if len(result["slides"]) > MAX_SLIDES_PER_PROJECT:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"슬라이드가 너무 많습니다. "
                        f"(최대 {MAX_SLIDES_PER_PROJECT}장, 현재 {len(result['slides'])}장)"
                    ),
                )

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
        # 연동 테스트 진단용: 상대가 404 응답 본문을 안 읽고 재시도만 반복할 때,
        # **무슨 ID를 보냈는지** 우리 로그만 보고 알 수 있어야 한다 (2026-08-12 실측 상황).
        print(f"⚠️ 404 대본 생성: project_id={request.project_id} 는 이 서버에 없습니다 "
              f"(POST /api/projects 응답의 project_id를 써야 함)")
        raise HTTPException(status_code=404, detail=(
            f"project_id={request.project_id} 프로젝트를 찾을 수 없습니다. "
            "POST /api/projects(자료 업로드) 응답으로 받은 project_id를 사용하세요."))
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

    # 피그마 AI Set Page (Loading)이 진행을 4단계로 그린다. status만으로는 "처리중"밖에
    # 못 그리므로 현재 단계도 함께 준다(job_store.STEP_LABELS).
    response = {
        "success": True, "job_id": job_id, "status": job["status"],
        "step": job["step"],
        "total_steps": job_store.TOTAL_STEPS,
        "step_label": job_store.step_label(job["step"]),
    }
    if job["status"] == "completed":
        # 완료 시 응답은 기존 동기 방식과 동일한 모양({project_id, data})을 그대로 담는다.
        response["project_id"] = job["data"]["project_id"]
        response["data"] = job["data"]["data"]
    elif job["status"] == "failed":
        response["error"] = job["error"]
    return response

@api.post("/api/script/partial")
async def create_partial_script(request: PartialScriptRequest, db: Session = Depends(get_db)):
    """[대본 부분 재생성 API] project_id + target_slide로 대상을 지정하고, style은 "formal"/"casual" 중 하나
    (구버전 "격식체"/"편안한 말투"도 허용), extra_requirement는 선택 입력(공백 가능).
    기존 대본 전문을 다시 보낼 필요 없이 DB에 저장된 걸 그대로 쓴다."""
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
        # 이 슬라이드의 원문(PPT에서 추출한 내용). 없으면 모델은 이 장이 무슨 내용인지 거의 모른 채
        # 앞뒤 대본만 보고 지어내게 된다.
        target_slide.source_content or "",
    )

    if not result or "script" not in result:
        raise HTTPException(status_code=502, detail="대본 부분 재생성에 실패했습니다.")

    target_slide.script = result["script"]
    db.commit()

    return {"success": True, "project_id": project.id, "data": result}

def _analyze_difficult_words(script_text: str):
    """대본 → (발음 주의 단어 목록, 카테고리별 집계).

    전부 동기 블로킹이다 — 형태소 분석은 CPU를 잡고, ETRI와 표준국어대사전은 HTTP를 친다.
    그래서 `async def` 핸들러에서 직접 부르지 말고 반드시 `run_in_threadpool`로 감쌀 것.
    """
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

    # G2P 모듈로 발음 기호 획득
    final_result = g2p_converter.convert_words(extracted_words)

    # 장단음/연음/표기-발음불일치 카테고리 분류 (하이라이트용) + 단어 목록에 띄울 설명 문구
    summary = {category: 0 for category in DIFFICULT_WORD_CATEGORIES}
    words_payload = []
    for item in final_result:
        word = item["word"]
        long_vowels = stdict_client.long_vowel_positions(word)
        category = _classify_word_category(word, item.get("is_different", False), long_vowels)
        # 분류가 None이라는 건 `_classify_word_category` 정의상 **철자=발음이고 장단음도 아니다**,
        # 즉 발음상 주의할 게 없다는 뜻이다. 그런 단어를 '발음 주의 단어' 목록에 넣으면
        # 피그마 화면에서 뱃지도 설명도 빈 줄이 된다(실측: 제로 녹음 대본에서 40개 중 25개가
        # 여기 해당 — '생각', '노력', '타인'처럼 어려울 게 없는 단어들이었다).
        # 형태소 분석기는 "명사인가"만 보므로 이 걸러내기는 여기서 해야 한다.
        if not category:
            continue
        summary[category] += 1

        # 장단음으로 분류해놓고 어디를 길게 읽는지 안 알려주면 그 카테고리가 무의미하다.
        # 피그마 단어 목록도 `구성 › [구ː성]`처럼 위치를 보여준다.
        phoneme = phonology_rules.apply_length_marks(item["phoneme"], long_vowels)
        words_payload.append({
            **item,
            "phoneme": phoneme,
            "category": category,
            "description": phonology_rules.describe(word, phoneme, category, long_vowels),
        })

    return words_payload, summary


@api.post("/api/analysis/words")
async def extract_and_convert_words(request: AnalysisRequest, db: Session = Depends(get_db)):
    """[발음 주의 단어 추출 및 G2P 변환 API] project_id의 생성된 대본을 분석 대상으로 삼는다."""
    project = db.get(models.Project, request.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

    script_text = _compiled_script_text(project)
    if not script_text:
        raise HTTPException(status_code=422, detail="먼저 /api/script/full로 전체 대본을 생성해주세요.")

    # 형태소 분석(CPU) + ETRI/표준국어대사전 HTTP가 전부 동기 호출이라 스레드풀로 넘긴다.
    # 단어 40개면 사전 조회만 40번이라, 루프에서 직접 돌리면 그동안 다른 요청이 전부 멈춘다.
    words_payload, summary = await run_in_threadpool(_analyze_difficult_words, script_text)

    # 이 프로젝트의 기존 단어 목록을 최신 결과로 교체 (현재 대본 기준 스냅샷)
    db.query(models.DifficultWord).filter(models.DifficultWord.project_id == project.id).delete()
    for item in words_payload:
        db.add(models.DifficultWord(
            project_id=project.id, word=item["word"], phoneme=item["phoneme"],
            category=item["category"], description=item.get("description"),
        ))
    db.commit()

    return {"success": True, "project_id": project.id, "data": {"words": words_payload, "summary": summary}}


# 합성에 쓸 화자. 'ndain'은 신뢰감 있는 톤의 한국어 여성 아나운서 음색.
TTS_SPEAKER = os.getenv("CLOVA_VOICE_SPEAKER", "ndain")


def _clean_tts_text(text: str) -> str:
    """발음기호를 Clova Voice에 그대로 넣을 수 있는 평문으로 다듬는다.

    - 대괄호를 벗긴다: '[여칼]' → '여칼' (G2P 결과는 항상 대괄호로 감싸여 있다)
    - 장음 기호(ː)를 뺀다. Clova Voice 입력은 평문이라 ː를 표현할 방법이 없고, 남겨두면
      모르는 문자가 그대로 발음에 섞인다. 대신 장단음 정보는 소리에서 빠지는데, 장단음 단어는
      대개 철자=발음이라(밤/밤ː) 일반 읽기와 같아진다 — 틀린 소리가 나가진 않는다.
    """
    cleaned = phonology_rules.strip_brackets(text or "")
    cleaned = cleaned.replace(phonology_rules.LENGTH_MARK, "")
    return " ".join(cleaned.split())[:MAX_TTS_TEXT_LEN]


def _stored_pronunciation(db: Session, project_id: int, word: str) -> str:
    """/api/analysis/words가 이미 저장해 둔 발음기호를 꺼낸다. 없으면 빈 문자열."""
    row = (
        db.query(models.DifficultWord)
        .filter(models.DifficultWord.project_id == project_id, models.DifficultWord.word == word)
        .first()
    )
    return _clean_tts_text(row.phoneme) if row and row.phoneme else ""


@api.get("/api/tts/voices")
async def list_tts_voices():
    """[목소리 목록 API] 발음 듣기 설정 화면이 그릴 선택지(이름·성별)와 속도 범위.

    프론트가 하드코딩하는 대신 여기서 받으면, 화자 구성이 바뀌어도 프론트 수정이 없다.
    `name` 값을 그대로 POST /api/tts/word의 `voice`로 보내면 된다.
    """
    return {
        "success": True,
        "voices": [
            {"name": name, "gender": info["gender"]}
            for name, info in VOICES.items()
        ],
        "speed": {"min": SPEED_MIN, "max": SPEED_MAX, "default": 0},
    }


@api.post("/api/tts/word")
async def synthesize_word_pronunciation(request: TtsWordRequest, db: Session = Depends(get_db)):
    """[발음 듣기 API] 단어의 **표준 발음**을 합성해 MP3 바이트를 그대로 돌려준다.

    ## 왜 철자가 아니라 발음을 보내는가 (2026-08-09 실측)
    Clova Voice는 한국어 음운 규칙을 **일부만** 적용한다. 네 단어를 철자 그대로 넣어 들어본 결과:

        역할 → [여칼] ✅ 격음화 적용      권리 → [궐리] ✅ 유음화 적용
        각자 → [각자] ❌ 경음화 미적용    책임 → [책임] ❌ 연음 미적용

    철자를 보내면 '각자'·'책임'처럼 **틀린 발음을 정답이라고 들려주게 된다** — 발음 주의 단어
    기능이 정확히 거기서 무너진다. 반대로 발음 문자열('각짜'/'채김'/'궐리'/'여칼')은 넷 다 넣은
    대로 소리가 났고, 이미 규칙이 걸린 '권리'·'역할'에 넣어도 규칙이 이중 적용되지 않았다.
    그래서 **기본은 발음**이고, 철자로 듣고 싶으면 `pronunciation`에 철자를 넣으면 된다.
    """
    word = request.word.strip()
    if not word:
        raise HTTPException(status_code=422, detail="합성할 단어가 비어 있습니다.")

    # 발음 결정: 요청에 명시된 값 → 저장된 발음기호 → 즉석 G2P → (전부 실패 시) 철자
    text = _clean_tts_text(request.pronunciation) if request.pronunciation else ""
    if not text and request.project_id:
        text = _stored_pronunciation(db, request.project_id, word)
    if not text:
        # G2P는 형태소 분석까지 도는 CPU 작업이라 루프에서 직접 돌리면 서버 전체가 그동안 멈춘다.
        converted = await run_in_threadpool(g2p_converter.convert_words, [word])
        text = _clean_tts_text(converted[0]["phoneme"]) if converted else ""
    if not text:
        text = word

    # 목소리 결정: 요청에 voice가 있으면 카탈로그의 화자, 없으면 기존 기본 화자.
    speaker = speaker_code(request.voice) if request.voice else TTS_SPEAKER

    # 캐시가 먼저다. Clova Voice는 글자 수만큼 과금되는데, 발음 주의 단어는 프로젝트마다 겹치고
    # 사용자가 스피커 버튼을 여러 번 누르면 똑같은 요청이 그대로 또 나간다.
    audio = tts_cache.get(text, speaker, request.speed)
    cache_status = "hit"

    if audio is None:
        cache_status = "miss"
        # requests.post는 완전 블로킹이다 (tests/test_blocking_offload.py가 이 계약을 지킨다).
        audio = await run_in_threadpool(tts_client.synthesize_bytes, text, speaker, request.speed)
        if audio is None:
            raise HTTPException(status_code=502, detail="발음 음성 합성에 실패했습니다.")
        tts_cache.put(text, speaker, audio, request.speed)

    # 헤더는 latin-1만 담을 수 있어서 한글을 그대로 넣으면 응답 자체가 깨진다. 퍼센트 인코딩한다.
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(word)}.mp3",
            "X-TTS-Cache": cache_status,       # 과금 발생 여부(miss = 실제 합성 호출)
            "X-TTS-Text": quote(text),         # 실제로 합성한 문자열 — 디버깅/검증용
        },
    )


@api.post("/api/evaluation/audio")
async def evaluate_pronunciation(
    project_id: int = Form(...),
    reference_text: str = Form(None),
    slide_number: int = Form(None),
    audio_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """[사용자 음성 발음 평가 API] 오디오 파일과 project_id를 받아 점수를 매기고 히스토리로 저장합니다.

    평가 기준이 되는 대본은 다음 순서로 정해집니다.
      1) reference_text를 직접 주면 그것
      2) slide_number를 주면 그 슬라이드의 대본만 (슬라이드별 부분 녹음)
      3) 둘 다 없으면 프로젝트의 대본 전체
    slide_number는 결과에도 함께 저장돼서, 코칭 내역에서 "3번 슬라이드 87점"처럼 구분됩니다."""

    # 임시 파일로 오디오 저장 (Azure SDK가 물리적인 파일 경로를 요구함)
    temp_file_path = _safe_temp_path(audio_file.filename)
    wav_file_path = None
    try:
        # 업로드된 파일을 로컬 디스크에 임시 복사 (크기/타입 제한 적용)
        await run_in_threadpool(
            _save_upload_with_limit, audio_file, temp_file_path, MAX_AUDIO_SIZE_BYTES, ALLOWED_AUDIO_EXTENSIONS,
            " 슬라이드별로 나눠 녹음하시면 한 번에 올리는 분량이 줄어듭니다.",
        )

        # 길이 상한은 크기 상한과 별개다(MAX_AUDIO_DURATION_SECONDS 주석 참고).
        # **변환·Azure 호출 전에** 잰다 — 나중에 재면 그 비용이 이미 다 나간 뒤가 된다.
        # ffprobe는 헤더만 읽지만 subprocess라 블로킹이므로 스레드풀로 넘긴다.
        audio_seconds = await run_in_threadpool(audio_converter.probe_duration_seconds, temp_file_path)
        if audio_seconds is not None and audio_seconds > MAX_AUDIO_DURATION_SECONDS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"녹음이 너무 깁니다. ({audio_seconds / 60:.1f}분 / 최대 "
                    f"{MAX_AUDIO_DURATION_SECONDS // 60}분) "
                    "슬라이드별로 나눠 녹음하시면 한 번에 올리는 분량이 줄어듭니다."
                ),
            )

        project = db.get(models.Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

        # 슬라이드별 부분 녹음: slide_number를 주면 그 슬라이드 대본만 기준으로 채점한다.
        # (전체 대본을 기준으로 잡으면 한 장만 읽었을 때 완성도가 바닥으로 나온다)
        if reference_text:
            text_to_evaluate = reference_text
        elif slide_number is not None:
            slide = next((s for s in project.slides if s.slide_number == slide_number), None)
            if not slide:
                raise HTTPException(status_code=404, detail="해당 번호의 슬라이드를 찾을 수 없습니다.")
            if not (slide.script or "").strip():
                raise HTTPException(status_code=422, detail="이 슬라이드에는 아직 생성된 대본이 없습니다.")
            text_to_evaluate = slide.script
        else:
            text_to_evaluate = _compiled_script_text(project)

        if not text_to_evaluate:
            raise HTTPException(status_code=422, detail="reference_text가 없고, 이 프로젝트에 생성된 대본도 없습니다.")

        # Azure Pronunciation Assessment는 16kHz mono PCM WAV를 요구하므로,
        # WAV가 아니면(MP3/M4A) ffmpeg로 변환한 뒤 그 결과 파일을 평가에 사용한다.
        audio_path_for_evaluation = temp_file_path
        if os.path.splitext(audio_file.filename or "")[1].lower() != ".wav":
            wav_file_path = _safe_temp_path("converted.wav")
            # ffmpeg는 subprocess.run으로 완료까지 블로킹한다. 10MB 녹음이면 초 단위가 걸리므로
            # 루프에서 직접 돌리면 그동안 서버 전체가 멈춘 것처럼 보인다.
            converted = await run_in_threadpool(audio_converter.convert_to_wav, temp_file_path, wav_file_path)
            if not converted:
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
        # 틀린 부분을 원본·인식 양쪽에서 강조하려면(피그마 Feedback Page) 단어가 각 텍스트의
        # 어디에 있는지 알아야 한다. error_type만으로는 같은 단어가 여러 번 나올 때 못 고른다.
        _attach_error_spans(result.get("words_detail"), text_to_evaluate, result.get("recognized_text"))
        scores = result.get("overall_scores", {})
        evaluation = models.PronunciationEvaluation(
            project_id=project.id,
            accuracy_score=scores.get("accuracy"),
            fluency_score=scores.get("fluency"),
            completeness_score=scores.get("completeness"),
            pronunciation_score=scores.get("pronunciation_score"),
            words_detail=result.get("words_detail"),
            # 결과 화면에서 "원본 텍스트 ↔ 인식 텍스트"를 나란히 보여주려면 둘 다 남겨야 한다.
            reference_text=text_to_evaluate,
            recognized_text=result.get("recognized_text"),
            # 슬라이드별로 녹음했으면 몇 번 장이었는지 남긴다(전체 녹음이면 None).
            slide_number=slide_number,
        )
        db.add(evaluation)
        db.commit()
        db.refresh(evaluation)

        return {
            "success": True, "project_id": project.id, "evaluation_id": evaluation.id,
            "slide_number": evaluation.slide_number,
            # words_detail의 reference_span은 **이 문자열 기준**의 오프셋이다. 같이 안 주면
            # 프론트가 어느 텍스트에 대고 칠해야 할지 알 수 없다(대본을 이어붙이며 "Slide N:"
            # 접두어가 붙으므로 프론트가 가진 원본과 인덱스가 다르다).
            "reference_text": text_to_evaluate,
            # 피그마 Feedback Page ㊶는 점수와 함께 등급(A~F)을 그린다. 프론트가 각자 변환하면
            # 화면마다 기준이 달라질 수 있어서 서버가 한 곳에서 정한다(utils/score_grade.py).
            "grades": score_grade.grades_for(result.get("overall_scores")),
            **result,
        }
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

@api.post("/api/evaluation/{evaluation_id}/feedback")
async def create_evaluation_feedback(evaluation_id: int, db: Session = Depends(get_db)):
    """[AI 발음 피드백 API] 점수만으로는 무엇을 고쳐야 할지 알 수 없으므로, 점수와 실제로 틀린 단어를
    근거로 코칭 피드백(총평/잘한 점/개선할 점/연습 팁)을 생성해 저장하고 반환합니다.
    이미 생성된 피드백이 있으면 다시 만들지 않고 그대로 돌려줍니다(불필요한 HCX 비용 방지)."""
    evaluation = db.get(models.PronunciationEvaluation, evaluation_id)
    if not evaluation:
        raise HTTPException(status_code=404, detail="평가 결과를 찾을 수 없습니다.")

    if evaluation.feedback:
        # 연습 팁 형식을 바꾸기 전에 캐시된 피드백이 있다(문자열 리스트). 그대로 내려주면
        # 지난 평가 화면만 깨지므로 읽을 때 새 형식으로 맞춰준다.
        cached = dict(evaluation.feedback)
        cached["practice_tips"] = normalize_practice_tips(cached.get("practice_tips"))
        return {"success": True, "evaluation_id": evaluation.id, "data": cached, "cached": True}

    overall_scores = {
        "accuracy": evaluation.accuracy_score,
        "fluency": evaluation.fluency_score,
        "completeness": evaluation.completeness_score,
        "pronunciation_score": evaluation.pronunciation_score,
    }
    weak_words = collect_weak_words(evaluation.words_detail)
    # 칭찬에도 근거가 필요하다 — 안 주면 모델이 대본에서 아무 단어나 골라 "잘 발음했다"고 지어낸다.
    strong_words = collect_strong_words(evaluation.words_detail)
    script_excerpt = _compiled_script_text(evaluation.project) if evaluation.project else ""

    feedback = await run_in_threadpool(
        feedback_generator.generate_feedback, overall_scores, weak_words, script_excerpt, strong_words
    )
    if not feedback:
        raise HTTPException(status_code=502, detail="발음 피드백 생성에 실패했습니다.")

    # 어떤 단어를 근거로 지적했는지 프론트가 함께 보여줄 수 있도록 같이 저장한다.
    feedback["weak_words"] = weak_words
    evaluation.feedback = feedback
    db.commit()
    db.refresh(evaluation)

    return {"success": True, "evaluation_id": evaluation.id, "data": evaluation.feedback, "cached": False}

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
                "slide_number": e.slide_number,  # 슬라이드별 녹음이면 그 번호, 전체 녹음이면 null
                "accuracy_score": e.accuracy_score,
                "fluency_score": e.fluency_score,
                "completeness_score": e.completeness_score,
                "pronunciation_score": e.pronunciation_score,
                "grades": _evaluation_grades(e),  # A~F (피그마 Feedback Page)
                "feedback": e.feedback,  # 아직 생성 안 했으면 null
                "reference_text": e.reference_text,   # 원본 대본
                "recognized_text": e.recognized_text,  # Azure가 실제로 인식한 텍스트
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
            "difficult_words": [
                {"word": w.word, "phoneme": w.phoneme, "category": w.category, "description": w.description}
                for w in project.difficult_words
            ],
            "evaluations": [
                {
                    "id": e.id,
                    "slide_number": e.slide_number,  # 슬라이드별 녹음이면 그 번호, 전체 녹음이면 null
                    "accuracy_score": e.accuracy_score,
                    "fluency_score": e.fluency_score,
                    "completeness_score": e.completeness_score,
                    "pronunciation_score": e.pronunciation_score,
                    "grades": _evaluation_grades(e),  # A~F (피그마 Feedback Page)
                    "feedback": e.feedback,  # 아직 생성 안 했으면 null
                    "reference_text": e.reference_text,   # 원본 대본
                    "recognized_text": e.recognized_text,  # Azure가 실제로 인식한 텍스트
                    "words_detail": e.words_detail,        # 단어별 점수(하이라이팅용)
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


@api.put("/api/projects/{project_id}")
async def update_project(project_id: int, request: ProjectUpdateRequest, db: Session = Depends(get_db)):
    """[프로젝트명 수정 API] 피그마 AI Script Edit Page ⑬ "사용자가 직접 입력하여 수정".

    지금까지는 업로드한 파일명으로 이름이 정해지고 그 뒤로 바꿀 방법이 없었다. 이 이름이
    docx 다운로드 파일명으로도 쓰이므로(㉒ "13의 제목명.docx로 저장") 수정이 가능해야 한다.
    """
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="프로젝트명이 비어 있습니다.")

    project.name = name
    db.commit()
    db.refresh(project)
    return {"success": True, "project_id": project.id, "data": {"name": project.name}}


def _docx_response(content: bytes, filename: str) -> Response:
    """다운로드 응답. 파일명이 한글이라 latin-1 헤더에 그대로 못 넣는다 — 퍼센트 인코딩한다."""
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def _project_slides_for_docx(project: "models.Project"):
    """(슬라이드 번호, 대본) 목록. 대본이 없는 장은 뺀다."""
    return [
        (slide.slide_number, slide.script)
        for slide in sorted(project.slides, key=lambda s: s.slide_number)
        if (slide.script or "").strip()
    ]


@api.get("/api/projects/{project_id}/script.docx")
async def download_script_docx(project_id: int, db: Session = Depends(get_db)):
    """[대본 다운로드] 피그마 AI Script Edit Page ㉒ "13의 제목명.docx로 저장"."""
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

    slides = _project_slides_for_docx(project)
    if not slides:
        raise HTTPException(status_code=422, detail="아직 생성된 대본이 없습니다.")

    content = await run_in_threadpool(docx_builder.build_script_docx, project.name, slides)
    return _docx_response(content, f"{project.name}.docx")


@api.get("/api/projects/{project_id}/highlight.docx")
async def download_highlighted_script_docx(project_id: int, db: Session = Depends(get_db)):
    """[하이라이팅 대본 다운로드] 피그마 Coach View Page ㉙ "하이라이팅_대본.docx로 저장".

    발음 주의 단어에 카테고리 색을 칠한다(장단음 F7358E / 연음 0072F2 / 표기-발음불일치 F79322 —
    피그마 ㉜에 적힌 값 그대로). 색만 칠하면 무슨 뜻인지 알 수 없으므로 범례도 함께 넣는다.
    """
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

    slides = _project_slides_for_docx(project)
    if not slides:
        raise HTTPException(status_code=422, detail="아직 생성된 대본이 없습니다.")

    words = [
        {"word": w.word, "phoneme": w.phoneme, "category": w.category, "description": w.description}
        for w in project.difficult_words
    ]
    content = await run_in_threadpool(
        docx_builder.build_highlighted_script_docx, project.name, slides, words
    )
    return _docx_response(content, f"하이라이팅_{project.name}.docx")


def _evaluation_grades(evaluation) -> dict:
    """저장된 평가 행 → 등급 dict(피그마 Feedback Page ㊶).

    이력 조회 응답은 스키마가 flat이라(accuracy_score …) 키 이름이 평가 직후 응답과 다르다.
    등급 기준은 한 곳(utils/score_grade.py)에만 두려고 여기서 이름만 맞춰 넘긴다.
    """
    return score_grade.grades_for({
        "accuracy": evaluation.accuracy_score,
        "fluency": evaluation.fluency_score,
        "completeness": evaluation.completeness_score,
        "pronunciation_score": evaluation.pronunciation_score,
    })


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