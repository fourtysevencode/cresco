"""Weekly leaderboard and prize issuing."""

from dataclasses import dataclass
from datetime import timedelta
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.timeutil import now
from app.models import PointsEntry, Reward, RewardConfig, School, Student, User

DEFAULT_REWARDS = [
    # rank, type, title, value_paise, merchant_kind
    (1, "olympiad_fee", "Free Olympiad registration", None, "bookstore"),
    (2, "book_discount", "₹200 off at the bookstore", 20_000, "bookstore"),
    (3, "free_icecream", "Free ice cream at the canteen", None, "canteen"),
]


@dataclass
class Row:
    rank: int
    student_id: uuid.UUID
    name: str
    grade: int
    points: int


async def standings(session: AsyncSession, school_id: uuid.UUID, week: str, limit: int) -> list[Row]:
    """Ranked by points; ties go to whoever reached their total first."""
    total = func.sum(PointsEntry.points).label("total")
    reached_at = func.max(PointsEntry.created_at).label("reached_at")
    stmt = (
        select(User.id, User.name, Student.grade, total)
        .join(PointsEntry, PointsEntry.student_id == User.id)
        .join(Student, Student.user_id == User.id)
        .where(PointsEntry.school_id == school_id, PointsEntry.iso_week == week)
        .group_by(User.id, User.name, Student.grade)
        .having(func.sum(PointsEntry.points) > 0)
        .order_by(total.desc(), reached_at.asc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [Row(i + 1, r.id, r.name, r.grade, int(r.total)) for i, r in enumerate(rows)]


async def reward_config(session: AsyncSession, school_id: uuid.UUID) -> list[RewardConfig]:
    configs = (
        await session.execute(select(RewardConfig).where(RewardConfig.school_id == school_id).order_by(RewardConfig.rank))
    ).scalars().all()
    if configs:
        return list(configs)
    return [
        RewardConfig(school_id=school_id, rank=r, type=t, title=title, value_paise=v, merchant_kind=k)
        for r, t, title, v, k in DEFAULT_REWARDS
    ]


async def close_week(session: AsyncSession, week: str, school_id: uuid.UUID | None = None) -> list[Reward]:
    """Issue prizes to the top students of `week`, for one school or all. Idempotent: a school whose
    week has already been closed is skipped. Commits."""
    issued: list[Reward] = []
    expires_at = now() + timedelta(days=get_settings().reward_expiry_days)
    school_ids = [school_id] if school_id else list((await session.execute(select(School.id))).scalars())
    for school_id in school_ids:
        already = await session.scalar(select(func.count()).select_from(Reward).where(Reward.school_id == school_id, Reward.week == week))
        if already:
            continue
        configs = await reward_config(session, school_id)
        top = await standings(session, school_id, week, limit=max((c.rank for c in configs), default=0))
        by_rank = {c.rank: c for c in configs}
        for row in top:
            cfg = by_rank.get(row.rank)
            if cfg is None:
                continue
            reward = Reward(
                student_id=row.student_id,
                school_id=school_id,
                week=week,
                rank=row.rank,
                points=row.points,
                type=cfg.type,
                title=cfg.title,
                value_paise=cfg.value_paise,
                merchant_kind=cfg.merchant_kind,
                expires_at=expires_at,
            )
            session.add(reward)
            issued.append(reward)
    await session.commit()
    return issued
