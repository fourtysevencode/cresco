"""Scheduled jobs for serverless hosting, where there is no in-process scheduler. Vercel Cron calls
this once a day with `Authorization: Bearer <CRON_SECRET>`."""

import hmac
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from app import jobs
from app.core.config import get_settings
from app.core.timeutil import iso_week, previous_iso_week

router = APIRouter(prefix="/cron", tags=["cron"], include_in_schema=False)


@router.get("/daily")
async def daily(authorization: Annotated[str | None, Header()] = None):
    secret = get_settings().cron_secret
    if not secret or not hmac.compare_digest(authorization or "", f"Bearer {secret}"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthorized")
    await jobs.prune_nonces()
    # Close last week. Idempotent, so running daily is harmless and it happens on Monday even though
    # Hobby-plan cron timing is only accurate to the hour.
    week = previous_iso_week(iso_week())
    issued = await jobs.close_week(week)
    return {"closed_week": week, "rewards_issued": issued}
