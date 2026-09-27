import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import CurrentUser, SessionDep, StudentUser
from app.core.timeutil import iso_week, now, parse_iso_week, previous_iso_week
from app.models import Merchant, ParentStudent, PointsEntry, Reward, User
from app.schemas.learning import LeaderboardOut, LeaderboardRow, PointsOut, RewardOut, WinnerOut, WinnersOut
from app.services import leaderboard

router = APIRouter(tags=["leaderboard"])


async def _school_for(session: AsyncSession, user: User, school_id: uuid.UUID | None) -> uuid.UUID:
    """Students/admins see their own school; parents a school one of their children attends; staff their shop's school."""
    if user.role in ("student", "admin"):
        allowed = {user.school_id}
    elif user.role == "parent":
        allowed = set(
            (
                await session.execute(
                    select(User.school_id).join(ParentStudent, ParentStudent.student_id == User.id).where(ParentStudent.parent_id == user.id)
                )
            ).scalars()
        )
    else:
        allowed = {await session.scalar(select(Merchant.school_id).where(Merchant.id == user.merchant_id))}
    allowed.discard(None)
    if school_id is None and len(allowed) == 1:
        return next(iter(allowed))
    if school_id is None or school_id not in allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "school_not_found")
    return school_id


def _week(week: str | None, default: str) -> str:
    try:
        return parse_iso_week(week) if week else default
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_week")


@router.get("/leaderboard/weekly", response_model=LeaderboardOut)
async def weekly(
    user: CurrentUser,
    session: SessionDep,
    week: str | None = Query(default=None, examples=["2026-W39"], description="Defaults to the current week"),
    school_id: uuid.UUID | None = None,
):
    school = await _school_for(session, user, school_id)
    target = _week(week, iso_week())
    rows = await leaderboard.standings(session, school, target, limit=100_000 if user.role == "student" else get_settings().leaderboard_size)
    me = next((r for r in rows if r.student_id == user.id), None)
    top = rows[: get_settings().leaderboard_size]
    return LeaderboardOut(
        week=target,
        school_id=school,
        rows=[LeaderboardRow(**r.__dict__) for r in top],
        me=LeaderboardRow(**me.__dict__) if me else None,
    )


@router.get("/leaderboard/weekly/winners", response_model=WinnersOut)
async def winners(
    user: CurrentUser,
    session: SessionDep,
    week: str | None = Query(default=None, description="Defaults to last week"),
    school_id: uuid.UUID | None = None,
):
    school = await _school_for(session, user, school_id)
    target = _week(week, previous_iso_week(iso_week()))
    rows = await session.execute(
        select(Reward, User.name)
        .join(User, User.id == Reward.student_id)
        .where(Reward.school_id == school, Reward.week == target)
        .order_by(Reward.rank)
    )
    return WinnersOut(
        week=target,
        winners=[WinnerOut(rank=r.rank, student_id=r.student_id, name=name, points=r.points, reward=r.title) for r, name in rows],
    )


@router.get("/students/me/points", response_model=PointsOut)
async def my_points(student: StudentUser, session: SessionDep):
    week = iso_week()
    total = func.coalesce(func.sum(PointsEntry.points), 0)
    this_week = await session.scalar(select(total).where(PointsEntry.student_id == student.id, PointsEntry.iso_week == week))
    all_time = await session.scalar(select(total).where(PointsEntry.student_id == student.id))
    rows = await leaderboard.standings(session, student.school_id, week, limit=100_000)
    rank = next((r.rank for r in rows if r.student_id == student.id), None)
    return PointsOut(week=week, this_week=this_week, all_time=all_time, rank_this_week=rank)


@router.get("/students/me/rewards", response_model=list[RewardOut])
async def my_rewards(student: StudentUser, session: SessionDep):
    rewards = (await session.execute(select(Reward).where(Reward.student_id == student.id).order_by(Reward.created_at.desc()))).scalars()
    current = now()
    out = []
    for r in rewards:
        item = RewardOut.model_validate(r, from_attributes=True)
        if r.status == "issued" and r.expires_at <= current:
            item.status = "expired"
        out.append(item)
    return out
