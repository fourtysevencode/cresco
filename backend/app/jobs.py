"""Background jobs, run in-process by APScheduler."""

from datetime import timedelta
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.timeutil import iso_week, now
from app.models import TerminalNonce
from app.services import leaderboard

log = logging.getLogger("cresco.jobs")


async def close_week(week: str) -> int:
    """Issue prizes for `week` in every school. Idempotent. Returns the number issued."""
    async with get_sessionmaker()() as session:
        issued = await leaderboard.close_week(session, week)
    log.info("closed %s: %d rewards issued", week, len(issued))
    return len(issued)


async def close_current_week() -> None:
    """Sunday 23:59: issue prizes for the week that is ending."""
    await close_week(iso_week())


async def prune_nonces() -> None:
    """Drop terminal nonces too old to be replayed (the timestamp check rejects them anyway)."""
    cutoff = now() - timedelta(seconds=get_settings().terminal_clock_skew_seconds * 2)
    async with get_sessionmaker()() as session:
        await session.execute(delete(TerminalNonce).where(TerminalNonce.created_at < cutoff))
        await session.commit()


def build_scheduler() -> AsyncIOScheduler:
    tz = get_settings().tz
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(close_current_week, "cron", day_of_week="sun", hour=23, minute=59, id="close_week")
    scheduler.add_job(prune_nonces, "interval", minutes=10, id="prune_nonces")
    return scheduler
