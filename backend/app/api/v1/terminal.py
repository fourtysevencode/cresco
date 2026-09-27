"""Endpoints called by the ESP32 NFC readers. Every request must be HMAC-signed (see app.core.deps.get_terminal)."""

from fastapi import APIRouter

from app.core.deps import SessionDep, TerminalDep
from app.core.timeutil import now
from app.schemas.wallet import ChargeIn, ChargeOut, HeartbeatOut, RedeemIn, RedeemOut
from app.services import ledger, rewards

router = APIRouter(prefix="/terminal", tags=["terminal (ESP32)"])


@router.post("/heartbeat", response_model=HeartbeatOut)
async def heartbeat(ctx: TerminalDep):
    return HeartbeatOut(terminal=ctx.terminal.name, merchant=ctx.merchant.name, server_time=int(now().timestamp()))


@router.post("/charge", response_model=ChargeOut)
async def charge(body: ChargeIn, ctx: TerminalDep, session: SessionDep):
    """Student taps their card to pay. Always 200: `status` says approved/declined, `reason` says why.
    Retry with the same `idempotency_key` after a timeout — the student is charged at most once."""
    result = await ledger.charge(
        session, ctx.terminal, ctx.merchant, body.card_token, body.tag_uid, body.amount_paise, body.idempotency_key
    )
    return ChargeOut(status=result.status, reason=result.reason, name=result.first_name, balance_paise=result.balance_paise)


@router.post("/rewards/redeem", response_model=RedeemOut)
async def redeem(body: RedeemIn, ctx: TerminalDep, session: SessionDep):
    """Student taps to claim a leaderboard prize (e.g. free ice cream at the canteen)."""
    result = await rewards.redeem_at_terminal(session, ctx.terminal, ctx.merchant, body.card_token, body.idempotency_key)
    reward = result.reward
    return RedeemOut(
        status=result.status,
        reason=result.reason,
        name=result.first_name,
        reward=reward.title if reward else None,
        value_paise=reward.value_paise if reward else None,
    )
