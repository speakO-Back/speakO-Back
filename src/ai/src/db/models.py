from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Text, JSON, DateTime, Float, ForeignKey
from sqlalchemy.orm import relationship

from db.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    filename = Column(String, nullable=True)
    topic = Column(String, nullable=True)
    keywords = Column(JSON, nullable=True)  # list[str]
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    slides = relationship("Slide", back_populates="project", cascade="all, delete-orphan", order_by="Slide.slide_number")
    difficult_words = relationship("DifficultWord", back_populates="project", cascade="all, delete-orphan")
    evaluations = relationship("PronunciationEvaluation", back_populates="project", cascade="all, delete-orphan")


class Slide(Base):
    __tablename__ = "slides"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    slide_number = Column(Integer, nullable=False)
    source_content = Column(Text, nullable=True)  # PPT에서 추출한 원본 텍스트
    script = Column(Text, nullable=True)  # 생성된 대본 (부분 재생성 시 갱신됨)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    project = relationship("Project", back_populates="slides")


class DifficultWord(Base):
    __tablename__ = "difficult_words"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    word = Column(String, nullable=False)
    phoneme = Column(JSON, nullable=True)  # G2pConverter 결과
    category = Column(String, nullable=True)  # "장단음" | "연음" | "표기-발음불일치" | None(철자=발음)
    # 단어 목록 화면에 띄우는 설명(예: "경음화: 받침 뒤에 오는 예사소리가 된소리로 바뀌어 발음됩니다").
    # 조회할 때마다 다시 만들면 표준국어대사전을 또 때려야 하므로 스냅샷과 함께 저장한다.
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    project = relationship("Project", back_populates="difficult_words")


class ScriptJob(Base):
    """대본 생성 같은 비동기 작업의 진행 상태.

    왜 DB에 두나: 예전엔 프로세스 메모리(dict)에 담았는데, 그러면 (1) 워커를 2개 이상 띄우면
    접수한 워커와 폴링받는 워커가 달라 404가 나고 (2) 재시작하면 진행 중이던 작업이 증발했다.
    프론트가 1~2초마다 폴링하는 구조라 둘 다 그대로 사용자에게 드러난다.
    """
    __tablename__ = "script_jobs"

    id = Column(String, primary_key=True)  # uuid4().hex (접수번호)
    status = Column(String, nullable=False, default="processing")  # processing | completed | failed
    # 피그마 로딩 화면이 그리는 4단계 중 현재 단계(1~4). job_store.STEP_LABELS 참고.
    # 업로드·추출은 이 작업이 생기기 전에 끝나므로 3(대본 작성)에서 시작한다.
    step = Column(Integer, nullable=True, default=3)
    data = Column(JSON, nullable=True)   # 완료 시 결과
    error = Column(Text, nullable=True)  # 실패 시 사유
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class PronunciationEvaluation(Base):
    __tablename__ = "pronunciation_evaluations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    # 슬라이드별로 나눠 녹음한 경우 그 슬라이드 번호. 대본 전체를 한 번에 녹음했으면 None.
    # (코칭 내역에서 "3번 슬라이드 87점"처럼 구분하려면 이게 남아 있어야 한다)
    slide_number = Column(Integer, nullable=True)
    accuracy_score = Column(Float, nullable=True)
    fluency_score = Column(Float, nullable=True)
    completeness_score = Column(Float, nullable=True)
    pronunciation_score = Column(Float, nullable=True)
    words_detail = Column(JSON, nullable=True)
    # 평가 기준으로 쓴 원본 대본과, Azure가 실제로 인식한 텍스트. 결과 화면에서 둘을 나란히 놓고
    # 어디를 다르게 읽었는지 비교한다(피그마 Feedback Page: 원본 텍스트 ↔ 인식 텍스트).
    reference_text = Column(Text, nullable=True)
    recognized_text = Column(Text, nullable=True)
    # AI 코칭 피드백(총평/잘한 점/개선할 점/연습 팁). 평가 직후엔 비어 있고, 피드백 생성 API를 부르면 채워진다.
    feedback = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    project = relationship("Project", back_populates="evaluations")
