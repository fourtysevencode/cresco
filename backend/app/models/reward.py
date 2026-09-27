from datetime import datetime
import uuid

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, created_at_col, uuid_pk

REWARD_TYPES = ("free_icecream", "book_discount", "olympiad_fee")


class RewardConfig(Base):
    """Which prize each leaderboard rank wins, per school."""

    __tablename__ = "reward_configs"
    __table_args__ = (CheckConstraint(f"type IN {REWARD_TYPES}", name="type"),)

    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schools.id"), primary_key=True)
    rank: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    value_paise: Mapped[int | None] = mapped_column(BigInteger)
    merchant_kind: Mapped[str] = mapped_column(String(20))


class Reward(Base):
    __tablename__ = "rewards"
    __table_args__ = (
        CheckConstraint(f"type IN {REWARD_TYPES}", name="type"),
        CheckConstraint("status IN ('issued', 'redeemed', 'expired')", name="status"),
        UniqueConstraint("school_id", "week", "rank", name="uq_rewards_school_week_rank"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schools.id"))
    week: Mapped[str] = mapped_column(String(8))
    rank: Mapped[int] = mapped_column(SmallInteger)
    points: Mapped[int]
    type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    value_paise: Mapped[int | None] = mapped_column(BigInteger)
    merchant_kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(10), default="issued")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redeemed_terminal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("terminals.id"))
    redeem_idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = created_at_col()
