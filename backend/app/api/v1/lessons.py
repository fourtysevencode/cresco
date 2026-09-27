"""AI tutor: photograph textbook pages → explanation in the student's language → audio, follow-up
questions and quizzes."""

import hashlib
from typing import Annotated
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import CurrentUser, SessionDep, StudentUser, ensure_can_view_student
from app.models import Lesson, LessonMessage, Quiz, Student, User
from app.schemas.learning import AskIn, LessonOut, LessonSummary, MessageOut, QuestionOut, QuizOut
from app.services import tts as tts_service
from app.services.ai_tutor import LessonContext, PageImage, TutorError, get_tutor
from app.services.languages import LanguageCode
from app.services.storage import get_storage

router = APIRouter(tags=["learning"])

_MAGIC = [
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
]
_HISTORY_LIMIT = 20


def _sniff(data: bytes) -> tuple[str, str] | None:
    for magic, media_type, ext in _MAGIC:
        if data.startswith(magic):
            return media_type, ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


def _tutor_http_error(e: TutorError) -> HTTPException:
    code = status.HTTP_503_SERVICE_UNAVAILABLE if e.code in ("ai_busy", "ai_unavailable") else status.HTTP_502_BAD_GATEWAY
    return HTTPException(code, e.code)


def _context(lesson: Lesson) -> LessonContext:
    return LessonContext(
        language=lesson.language,
        grade=lesson.grade,
        title=lesson.title,
        subject=lesson.subject,
        extracted_text=lesson.extracted_text,
        explanation=lesson.explanation,
    )


async def _lesson_out(session: AsyncSession, lesson: Lesson, deduplicated: bool = False) -> LessonOut:
    messages = (
        await session.execute(select(LessonMessage).where(LessonMessage.lesson_id == lesson.id).order_by(LessonMessage.created_at))
    ).scalars()
    return LessonOut(
        id=lesson.id,
        title=lesson.title,
        subject=lesson.subject,
        language=lesson.language,
        grade=lesson.grade,
        created_at=lesson.created_at,
        extracted_text=lesson.extracted_text,
        explanation=lesson.explanation,
        messages=[MessageOut(role=m.role, content=m.content, created_at=m.created_at) for m in messages],
        deduplicated=deduplicated,
    )


async def _viewable_lesson(session: AsyncSession, user: User, lesson_id: uuid.UUID) -> Lesson:
    lesson = await session.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lesson_not_found")
    await ensure_can_view_student(session, user, lesson.student_id)
    return lesson


async def _own_lesson(session: AsyncSession, student: User, lesson_id: uuid.UUID) -> Lesson:
    lesson = await session.get(Lesson, lesson_id)
    if lesson is None or lesson.student_id != student.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lesson_not_found")
    return lesson


@router.post("/lessons", response_model=LessonOut, status_code=201)
async def create_lesson(
    student: StudentUser,
    session: SessionDep,
    response: Response,
    images: Annotated[list[UploadFile], File(description="Photos of textbook pages, in reading order")],
    language: Annotated[LanguageCode | None, Form()] = None,
    subject: Annotated[str | None, Form(max_length=100)] = None,
):
    """Upload photos of textbook pages; the tutor reads them and explains the chapter in `language`
    (defaults to the student's preferred language)."""
    s = get_settings()
    if not 1 <= len(images) <= s.max_lesson_images:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "too_many_images")
    language = language or student.preferred_language or "ta"
    pages: list[tuple[PageImage, str]] = []
    digest = hashlib.sha256(language.encode())
    for upload in images:
        data = await upload.read(s.max_image_bytes + 1)
        if len(data) > s.max_image_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "image_too_large")
        sniffed = _sniff(data)
        if sniffed is None:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "unsupported_image")
        pages.append((PageImage(data, sniffed[0]), sniffed[1]))
        digest.update(hashlib.sha256(data).digest())
    content_hash = digest.hexdigest()

    existing = (
        await session.execute(select(Lesson).where(Lesson.student_id == student.id, Lesson.content_hash == content_hash))
    ).scalar_one_or_none()
    if existing:
        response.status_code = status.HTTP_200_OK
        return await _lesson_out(session, existing, deduplicated=True)

    grade = await session.scalar(select(Student.grade).where(Student.user_id == student.id))
    await session.commit()  # release the DB connection during the (slow) AI call
    try:
        content = await get_tutor().explain([p for p, _ in pages], language, grade, subject)
    except TutorError as e:
        raise _tutor_http_error(e)
    if not content.readable:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {"code": "unreadable_photo", "tip": content.summary})

    storage = get_storage()
    keys = [await storage.save(f"lessons/{student.id}/{content_hash}-{i}.{ext}", p.data) for i, (p, ext) in enumerate(pages)]
    lesson = Lesson(
        student_id=student.id,
        language=language,
        grade=grade,
        content_hash=content_hash,
        image_keys=keys,
        title=content.title[:300],
        subject=content.subject[:100],
        extracted_text=content.extracted_text,
        explanation={
            "summary": content.summary,
            "sections": [sec.model_dump() for sec in content.sections],
            "key_terms": [t.model_dump() for t in content.key_terms],
        },
    )
    session.add(lesson)
    await session.commit()
    return await _lesson_out(session, lesson)


@router.get("/lessons", response_model=list[LessonSummary])
async def list_lessons(
    user: CurrentUser, session: SessionDep, student_id: uuid.UUID | None = Query(default=None, description="For parents/admins")
):
    target = student_id or user.id
    await ensure_can_view_student(session, user, target)
    lessons = (
        await session.execute(select(Lesson).where(Lesson.student_id == target).order_by(Lesson.created_at.desc()).limit(100))
    ).scalars()
    return [LessonSummary(id=l.id, title=l.title, subject=l.subject, language=l.language, created_at=l.created_at) for l in lessons]


@router.get("/lessons/{lesson_id}", response_model=LessonOut)
async def get_lesson(lesson_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    return await _lesson_out(session, await _viewable_lesson(session, user, lesson_id))


async def _speak(session: AsyncSession, text: str, language: str) -> Response:
    provider = tts_service.get_tts()
    if provider is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "tts_not_configured")
    try:
        audio = await tts_service.speak(session, provider, text, language)
    except tts_service.TTSError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "tts_failed")
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/lessons/{lesson_id}/audio", response_class=Response, responses={200: {"content": {"audio/mpeg": {}}}})
async def lesson_audio(
    lesson_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    section: int | None = Query(default=None, ge=0, description="Section index; omit for the summary"),
):
    """The explanation read aloud (MP3) in the lesson's language."""
    lesson = await _viewable_lesson(session, user, lesson_id)
    explanation = lesson.explanation
    if section is None:
        text = explanation["summary"]
    elif section < len(explanation["sections"]):
        sec = explanation["sections"][section]
        text = f"{sec['heading']}.\n{sec['body']}"
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "section_not_found")
    return await _speak(session, text, lesson.language)


@router.post("/lessons/{lesson_id}/messages", response_model=MessageOut, status_code=201)
async def ask_tutor(lesson_id: uuid.UUID, body: AskIn, student: StudentUser, session: SessionDep):
    """Ask a follow-up question about the lesson ("explain that again more simply")."""
    lesson = await _own_lesson(session, student, lesson_id)
    recent = (
        await session.execute(
            select(LessonMessage).where(LessonMessage.lesson_id == lesson.id).order_by(LessonMessage.created_at.desc()).limit(_HISTORY_LIMIT)
        )
    ).scalars().all()
    history = [(m.role, m.content) for m in reversed(recent)]
    await session.commit()
    try:
        answer = await get_tutor().answer(_context(lesson), history, body.question)
    except TutorError as e:
        raise _tutor_http_error(e)
    session.add(LessonMessage(lesson_id=lesson.id, role="user", content=body.question))
    await session.flush()
    reply = LessonMessage(lesson_id=lesson.id, role="assistant", content=answer)
    session.add(reply)
    await session.commit()
    return MessageOut(role=reply.role, content=reply.content, created_at=reply.created_at)


@router.get("/lessons/{lesson_id}/messages/{message_index}/audio", response_class=Response)
async def message_audio(lesson_id: uuid.UUID, message_index: int, user: CurrentUser, session: SessionDep):
    """A tutor reply read aloud. `message_index` is the position in the lesson's `messages` list."""
    lesson = await _viewable_lesson(session, user, lesson_id)
    messages = (
        await session.execute(
            select(LessonMessage).where(LessonMessage.lesson_id == lesson.id).order_by(LessonMessage.created_at).offset(message_index).limit(1)
        )
    ).scalar_one_or_none()
    if message_index < 0 or messages is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "message_not_found")
    return await _speak(session, messages.content, lesson.language)


@router.post("/lessons/{lesson_id}/quiz", response_model=QuizOut, status_code=201)
async def create_quiz(lesson_id: uuid.UUID, student: StudentUser, session: SessionDep):
    """Generate a multiple-choice quiz on the lesson. Answers are only revealed after an attempt."""
    lesson = await _own_lesson(session, student, lesson_id)
    await session.commit()
    try:
        content = await get_tutor().make_quiz(_context(lesson), get_settings().quiz_questions)
    except TutorError as e:
        raise _tutor_http_error(e)
    quiz = Quiz(lesson_id=lesson.id, student_id=student.id, questions=[q.model_dump() for q in content.questions])
    session.add(quiz)
    await session.commit()
    return quiz_out(quiz)


def quiz_out(quiz: Quiz) -> QuizOut:
    return QuizOut(
        id=quiz.id,
        lesson_id=quiz.lesson_id,
        questions=[QuestionOut(question=q["question"], options=q["options"]) for q in quiz.questions],
    )
