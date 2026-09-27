"""What happens when a card is tapped on a reader.

The reader only knows the card's serial number (UID). The server decides what the tap means:

- the cashier has set a pending **charge** on this reader  → debit the student's wallet
- the cashier has set a pending **redeem** on this reader  → hand out the student's prize
- nothing pending                                          → just identify the student
- the UID isn't registered                                 → "unknown" (the dashboard offers to register it)

Declined taps leave the pending action open, so the next student (or the same one after a top-up)
can tap again. Every scan is logged with the response sent, and a retried scan (same `scan_id`)
gets that stored response back instead of being processed twice.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import now
from app.models import Merchant, NfcCard, PendingAction, TagScan, Terminal, User, Wallet
from app.services import ledger, rewards


async def handle_scan(session: AsyncSession, terminal: Terminal, merchant: Merchant, tag_uid: str, scan_id: str) -> dict:
    terminal_id = terminal.id  # read ids now: rollbacks below expire loaded objects
    stored = await _stored_response(session, terminal_id, scan_id)
    if stored is not None:
        return stored

    card = (
        await session.execute(
            select(NfcCard).where(NfcCard.tag_uid == tag_uid).order_by(NfcCard.status != "active", NfcCard.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    student = await session.get(User, card.student_id) if card else None
    student_id = student.id if student else None
    pending = (
        await session.execute(
            select(PendingAction)
            .where(PendingAction.terminal_id == terminal_id, PendingAction.status == "pending", PendingAction.expires_at > now())
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    pending_id = pending.id if pending else None
    scan_row_id = uuid.uuid4()
    response: dict = {"result": "unknown", "reason": None, "name": None, "balance_paise": None, "amount_paise": None, "reward": None}

    if card is None or student is None:
        response["reason"] = "unregistered_card"
        await session.rollback()
    elif card.status != "active":
        response.update(result="declined", reason="card_blocked", name=student.first_name)
        await session.rollback()
    elif pending and pending.kind == "charge":
        assert pending.amount_paise is not None
        amount = pending.amount_paise
        # Marked complete up front: ledger.charge commits this with the debit on approval and rolls it
        # back on decline, leaving the action open for another tap.
        pending.status, pending.completed_scan_id = "completed", scan_row_id
        result = await ledger.charge(session, terminal, merchant, card, student, amount, scan_id)
        response.update(result=result.status, reason=result.reason, name=result.first_name, balance_paise=result.balance_paise, amount_paise=amount)
    elif pending and pending.kind == "redeem":
        first_name = student.first_name
        redeemed = await rewards.redeem(session, terminal, merchant, student, scan_id)
        if redeemed.status == "approved":
            assert redeemed.reward is not None
            pending.status, pending.completed_scan_id = "completed", scan_row_id
            response.update(result="approved", name=first_name, reward=redeemed.reward.title)
            await session.commit()
        else:
            response.update(result="declined", reason=redeemed.reason, name=first_name)
            await session.rollback()
    else:
        balance = await session.scalar(select(Wallet.balance_paise).where(Wallet.student_id == student.id))
        response.update(result="identified", name=student.first_name, balance_paise=balance)
        await session.rollback()

    session.add(
        TagScan(
            id=scan_row_id,
            terminal_id=terminal_id,
            scan_id=scan_id,
            tag_uid=tag_uid,
            student_id=student_id,
            pending_action_id=pending_id if response["result"] in ("approved", "declined") else None,
            result=response["result"],
            reason=response["reason"],
            response=response,
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        # The same scan was retried concurrently; return whatever the other request recorded.
        await session.rollback()
        stored = await _stored_response(session, terminal_id, scan_id)
        if stored is not None:
            return stored
        raise
    return response


async def _stored_response(session: AsyncSession, terminal_id: uuid.UUID, scan_id: str) -> dict | None:
    return await session.scalar(select(TagScan.response).where(TagScan.terminal_id == terminal_id, TagScan.scan_id == scan_id))
