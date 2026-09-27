from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import now
from app.models import Merchant, NfcCard, Reward, Terminal, User


class RedeemResult:
    def __init__(self, status: str, reason: str | None = None, first_name: str | None = None, reward: Reward | None = None):
        self.status = status
        self.reason = reason
        self.first_name = first_name
        self.reward = reward


async def redeem_at_terminal(
    session: AsyncSession, terminal: Terminal, merchant: Merchant, card_token: str, idempotency_key: str
) -> RedeemResult:
    """Redeem the student's oldest valid prize that this kind of shop can honour. Commits."""
    key = f"{terminal.id}:{idempotency_key}"
    replay = (await session.execute(select(Reward).where(Reward.redeem_idempotency_key == key))).scalar_one_or_none()
    card = (await session.execute(select(NfcCard).where(NfcCard.card_token == card_token))).scalar_one_or_none()
    if card is None:
        return RedeemResult("declined", "unknown_card")
    student = await session.get(User, card.student_id)
    assert student is not None
    if replay:
        if replay.student_id != student.id:
            return RedeemResult("declined", "idempotency_conflict")
        return RedeemResult("approved", None, student.first_name, replay)
    if card.status != "active":
        return RedeemResult("declined", "card_blocked", student.first_name)
    if student.school_id != merchant.school_id:
        return RedeemResult("declined", "wrong_school", student.first_name)

    stmt = (
        select(Reward)
        .where(
            Reward.student_id == student.id,
            Reward.status == "issued",
            Reward.merchant_kind == merchant.kind,
            Reward.expires_at > now(),
        )
        .order_by(Reward.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    reward = (await session.execute(stmt)).scalar_one_or_none()
    if reward is None:
        return RedeemResult("declined", "no_reward", student.first_name)
    reward.status = "redeemed"
    reward.redeemed_at = now()
    reward.redeemed_terminal_id = terminal.id
    reward.redeem_idempotency_key = key
    await session.commit()
    return RedeemResult("approved", None, student.first_name, reward)
