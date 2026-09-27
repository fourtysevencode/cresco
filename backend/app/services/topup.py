"""Parent wallet top-ups — MOCK payments for the demo.

A single call credits the wallet instantly; no payment gateway is involved. To go live, replace
`mock_topup` with a gateway flow (create order → gateway webhook → `ledger.credit_topup`), which the
ledger already supports idempotently.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import LedgerEntry, User
from app.services import ledger


class TopupError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


async def mock_topup(
    session: AsyncSession, parent: User, student_id: uuid.UUID, amount_paise: int, idempotency_key: str | None
) -> LedgerEntry:
    s = get_settings()
    if not s.min_topup_paise <= amount_paise <= s.max_topup_paise:
        raise TopupError("amount_out_of_range")
    # A double-tapped "Add money" button with the same key credits only once.
    key = f"topup:{parent.id}:{idempotency_key or uuid.uuid4()}"
    entry = await ledger.credit_topup(session, student_id, amount_paise, key, description=f"Top-up by {parent.first_name}")
    await session.commit()
    return entry
