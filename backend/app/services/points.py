"""Quiz scoring rules.

Anti-gaming: only a student's first attempt at a quiz scores, and only their first N quizzes per day
score (they can keep practising, it just doesn't count for the leaderboard).
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.timeutil import iso_week, local_day_start
from app.models import PointsEntry, Quiz, QuizAttempt, User


@dataclass
class Grade:
    correct: list[bool]
    points: int
    reason: str | None  # why no points were awarded, if none were


async def grade_attempt(session: AsyncSession, student: User, quiz: Quiz, answers: list[int]) -> tuple[QuizAttempt, Grade]:
    """Grade answers, record the attempt and any points. Caller commits."""
    s = get_settings()
    # Serialise grading per student so concurrent submissions can't both score as a "first attempt".
    await session.execute(select(User.id).where(User.id == student.id).with_for_update())
    correct = [a == q["correct_index"] for a, q in zip(answers, quiz.questions, strict=True)]
    n_correct = sum(correct)

    first_attempt = not await session.scalar(
        select(func.count()).select_from(QuizAttempt).where(QuizAttempt.quiz_id == quiz.id, QuizAttempt.student_id == student.id)
    )
    scoring_today = await session.scalar(
        select(func.count())
        .select_from(PointsEntry)
        .where(
            PointsEntry.student_id == student.id,
            PointsEntry.source == "quiz",
            PointsEntry.created_at >= local_day_start(),
        )
    )

    points, reason = 0, None
    if not first_attempt:
        reason = "retry_not_scored"
    elif scoring_today >= s.max_scoring_quizzes_per_day:
        reason = "daily_quiz_limit"
    else:
        points = n_correct * s.points_per_correct
        if n_correct == len(quiz.questions):
            points += s.full_marks_bonus

    attempt = QuizAttempt(
        quiz_id=quiz.id,
        student_id=student.id,
        answers=answers,
        correct_count=n_correct,
        total=len(quiz.questions),
        points_awarded=points,
    )
    session.add(attempt)
    await session.flush()
    if first_attempt and reason is None:
        # Recorded even at 0 points, so it counts towards the daily scoring limit.
        session.add(
            PointsEntry(
                student_id=student.id,
                school_id=student.school_id,
                points=points,
                source="quiz",
                source_id=attempt.id,
                iso_week=iso_week(),
            )
        )
    return attempt, Grade(correct, points, reason)
