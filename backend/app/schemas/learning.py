from datetime import datetime
import uuid

from pydantic import BaseModel, Field

from app.services.languages import LanguageCode


class SectionOut(BaseModel):
    heading: str
    body: str


class KeyTermOut(BaseModel):
    english: str
    native: str
    meaning: str


class ExplanationOut(BaseModel):
    summary: str
    sections: list[SectionOut]
    key_terms: list[KeyTermOut]


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: datetime


class LessonSummary(BaseModel):
    id: uuid.UUID
    title: str
    subject: str
    language: str
    created_at: datetime


class LessonOut(LessonSummary):
    grade: int | None
    extracted_text: str
    explanation: ExplanationOut
    messages: list[MessageOut] = []
    deduplicated: bool = False


class TextLessonIn(BaseModel):
    """Text the student's device already read from the page (OCR)."""

    text: str = Field(min_length=20, max_length=30_000)
    language: LanguageCode | None = None
    subject: str | None = Field(default=None, max_length=100)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class QuestionOut(BaseModel):
    question: str
    options: list[str]


class QuizOut(BaseModel):
    id: uuid.UUID
    lesson_id: uuid.UUID
    questions: list[QuestionOut]


class AttemptIn(BaseModel):
    answers: list[int]


class QuestionResult(BaseModel):
    your_answer: int
    correct_index: int
    correct: bool
    explanation: str


class AttemptOut(BaseModel):
    attempt_id: uuid.UUID
    correct_count: int
    total: int
    points_awarded: int
    no_points_reason: str | None
    results: list[QuestionResult]


class LeaderboardRow(BaseModel):
    rank: int
    student_id: uuid.UUID
    name: str
    grade: int
    points: int


class LeaderboardOut(BaseModel):
    week: str
    school_id: uuid.UUID
    rows: list[LeaderboardRow]
    me: LeaderboardRow | None = None


class WinnerOut(BaseModel):
    rank: int
    student_id: uuid.UUID
    name: str
    points: int
    reward: str


class WinnersOut(BaseModel):
    week: str
    winners: list[WinnerOut]


class PointsOut(BaseModel):
    week: str
    this_week: int
    all_time: int
    rank_this_week: int | None


class RewardOut(BaseModel):
    id: uuid.UUID
    week: str
    rank: int
    type: str
    title: str
    value_paise: int | None
    merchant_kind: str
    status: str
    expires_at: datetime
    redeemed_at: datetime | None
