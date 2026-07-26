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
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    project = relationship("Project", back_populates="difficult_words")


class PronunciationEvaluation(Base):
    __tablename__ = "pronunciation_evaluations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    accuracy_score = Column(Float, nullable=True)
    fluency_score = Column(Float, nullable=True)
    completeness_score = Column(Float, nullable=True)
    pronunciation_score = Column(Float, nullable=True)
    words_detail = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    project = relationship("Project", back_populates="evaluations")
