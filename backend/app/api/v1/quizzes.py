import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.v1.lessons import quiz_out
from app.core.deps import SessionDep, StudentUser
from app.models import Quiz
from app.schemas.learning import AttemptIn, AttemptOut, QuestionResult, QuizOut
from app.services import points

router = APIRouter(prefix="/quizzes", tags=["learning"])


async def _own_quiz(session, student, quiz_id: uuid.UUID) -> Quiz:
    quiz = await session.get(Quiz, quiz_id)
    if quiz is None or quiz.student_id != student.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "quiz_not_found")
    return quiz


@router.get("/{quiz_id}", response_model=QuizOut)
async def get_quiz(quiz_id: uuid.UUID, student: StudentUser, session: SessionDep):
    return quiz_out(await _own_quiz(session, student, quiz_id))


@router.post("/{quiz_id}/attempts", response_model=AttemptOut, status_code=201)
async def submit_attempt(quiz_id: uuid.UUID, body: AttemptIn, student: StudentUser, session: SessionDep):
    """Submit answers (0-based option index per question). Graded on the server; only the first
    attempt earns leaderboard points."""
    quiz = await _own_quiz(session, student, quiz_id)
    if len(body.answers) != len(quiz.questions):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "wrong_number_of_answers")
    if any(not 0 <= a < len(q["options"]) for a, q in zip(body.answers, quiz.questions)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_option")
    attempt, grade = await points.grade_attempt(session, student, quiz, body.answers)
    await session.commit()
    return AttemptOut(
        attempt_id=attempt.id,
        correct_count=attempt.correct_count,
        total=attempt.total,
        points_awarded=grade.points,
        no_points_reason=grade.reason,
        results=[
            QuestionResult(your_answer=a, correct_index=q["correct_index"], correct=ok, explanation=q["explanation"])
            for a, q, ok in zip(body.answers, quiz.questions, grade.correct)
        ],
    )
