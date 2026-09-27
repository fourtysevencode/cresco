from datetime import datetime
import uuid

from sqlalchemy import ForeignKey, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, created_at_col, uuid_pk


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    language: Mapped[str] = mapped_column(String(5))
    grade: Mapped[int | None] = mapped_column(SmallInteger)
    # Hash of the uploaded pages + language; the same photos are never sent to the AI twice.
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    image_keys: Mapped[list[str]] = mapped_column(JSONB)
    title: Mapped[str] = mapped_column(String(300))
    subject: Mapped[str] = mapped_column(String(100))
    extracted_text: Mapped[str] = mapped_column(Text)
    # {"summary": str, "sections": [{"heading", "body"}], "key_terms": [{"english", "native", "meaning"}]}
    explanation: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_col()


class LessonMessage(Base):
    """Follow-up Q&A with the tutor about a lesson."""

    __tablename__ = "lesson_messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    lesson_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()


class TtsCache(Base):
    __tablename__ = "tts_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    storage_key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = created_at_col()


class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[uuid.UUID] = uuid_pk()
    lesson_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    # [{"question", "options": [...], "correct_index", "explanation"}] — answers never leave the server
    # until the quiz has been attempted.
    questions: Mapped[list[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_col()


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    quiz_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    answers: Mapped[list[int]] = mapped_column(JSONB)
    correct_count: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    points_awarded: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at_col()


class PointsEntry(Base):
    __tablename__ = "points_entries"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schools.id"))
    points: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20))  # "quiz"
    source_id: Mapped[uuid.UUID | None]
    iso_week: Mapped[str] = mapped_column(String(8), index=True)  # "2026-W39"
    created_at: Mapped[datetime] = created_at_col()
