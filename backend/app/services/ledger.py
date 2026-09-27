"""The only module that changes wallet balances.

Every change locks the wallet row (SELECT ... FOR UPDATE), appends a LedgerEntry and updates the
cached balance in the same transaction, so concurrent taps can never overdraw a wallet. Terminal
requests carry an idempotency key, so a reader retrying after a WiFi drop is never charged twice.
"""

from dataclasses import dataclass
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import local_day_start, now
from app.models import LedgerEntry, Merchant, NfcCard, Terminal, User, Wallet


class LedgerError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class ChargeResult:
    status: str  # "approved" | "declined"
    reason: str | None = None
    first_name: str | None = None
    balance_paise: int | None = None
    entry_id: uuid.UUID | None = None


async def _lock_wallet(session: AsyncSession, **where) -> Wallet | None:
    stmt = select(Wallet).filter_by(**where).with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _append(session: AsyncSession, wallet: Wallet, amount_paise: int, type_: str, **fields) -> LedgerEntry:
    if wallet.balance_paise + amount_paise < 0:
        raise LedgerError("insufficient_balance")
    wallet.balance_paise += amount_paise
    wallet.updated_at = now()
    entry = LedgerEntry(
        wallet_id=wallet.id,
        amount_paise=amount_paise,
        balance_after_paise=wallet.balance_paise,
        type=type_,
        **fields,
    )
    session.add(entry)
    await session.flush()
    return entry


async def charge(
    session: AsyncSession,
    terminal: Terminal,
    merchant: Merchant,
    card_token: str,
    tag_uid: str | None,
    amount_paise: int,
    idempotency_key: str,
) -> ChargeResult:
    """Debit a student's wallet for a purchase at a terminal. Commits the session."""
    key = f"{terminal.id}:{idempotency_key}"
    replay = await _replayed_charge(session, key, card_token, amount_paise)
    if replay:
        return replay

    card = (await session.execute(select(NfcCard).where(NfcCard.card_token == card_token))).scalar_one_or_none()
    if card is None:
        return ChargeResult("declined", "unknown_card")
    student = await session.get(User, card.student_id)
    assert student is not None
    first_name = student.first_name  # read now: objects expire on rollback

    def declined(reason: str) -> ChargeResult:
        return ChargeResult("declined", reason, first_name)

    if card.status != "active":
        return declined("card_blocked")
    normalized_uid = tag_uid.upper().replace(":", "") if tag_uid else None
    if card.tag_uid and normalized_uid != card.tag_uid:
        return declined("tag_mismatch")
    if student.school_id != merchant.school_id:
        return declined("wrong_school")

    wallet = await _lock_wallet(session, student_id=student.id)
    assert wallet is not None
    # A concurrent retry with the same key may have committed while we waited for the lock.
    replay = await _replayed_charge(session, key, card_token, amount_paise)
    if replay:
        await session.rollback()
        return replay
    spent_today = -(
        await session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount_paise), 0)).where(
                LedgerEntry.wallet_id == wallet.id,
                LedgerEntry.type == "purchase",
                LedgerEntry.created_at >= local_day_start(),
            )
        )
    )
    if spent_today + amount_paise > card.daily_limit_paise:
        await session.rollback()
        return declined("daily_limit")
    if wallet.balance_paise < amount_paise:
        balance = wallet.balance_paise
        await session.rollback()
        return ChargeResult("declined", "insufficient_balance", first_name, balance)

    if card.tag_uid is None and normalized_uid:
        card.tag_uid = normalized_uid  # bind the tag's hardware UID on first use
    try:
        entry = await _append(
            session,
            wallet,
            -amount_paise,
            "purchase",
            description=merchant.name,
            card_id=card.id,
            terminal_id=terminal.id,
            merchant_id=merchant.id,
            idempotency_key=key,
        )
        await session.commit()
    except IntegrityError:
        # Lost a race with a concurrent retry carrying the same idempotency key.
        await session.rollback()
        replay = await _replayed_charge(session, key, card_token, amount_paise)
        if replay:
            return replay
        raise
    return ChargeResult("approved", None, first_name, entry.balance_after_paise, entry.id)


async def _replayed_charge(session: AsyncSession, key: str, card_token: str, amount_paise: int) -> ChargeResult | None:
    entry = (await session.execute(select(LedgerEntry).where(LedgerEntry.idempotency_key == key))).scalar_one_or_none()
    if entry is None:
        return None
    card = await session.get(NfcCard, entry.card_id)
    if card is None or card.card_token != card_token or -entry.amount_paise != amount_paise:
        return ChargeResult("declined", "idempotency_conflict")
    student = await session.get(User, card.student_id)
    return ChargeResult("approved", None, student.first_name if student else None, entry.balance_after_paise, entry.id)


async def credit_topup(
    session: AsyncSession, student_id: uuid.UUID, amount_paise: int, idempotency_key: str, description: str = "Top-up"
) -> LedgerEntry:
    """Credit a top-up. Replaying the same idempotency key returns the original entry. Caller commits."""
    existing = (
        await session.execute(select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key))
    ).scalar_one_or_none()
    if existing:
        return existing
    wallet = await _lock_wallet(session, student_id=student_id)
    if wallet is None:
        raise LedgerError("wallet_not_found")
    return await _append(
        session, wallet, amount_paise, "topup", description=description, idempotency_key=idempotency_key
    )


async def refund(session: AsyncSession, purchase: LedgerEntry) -> LedgerEntry:
    """Refund a purchase in full. Caller commits; a second refund of the same purchase fails."""
    if purchase.type != "purchase":
        raise LedgerError("not_a_purchase")
    wallet = await _lock_wallet(session, id=purchase.wallet_id)  # also serialises concurrent refunds
    assert wallet is not None
    already = await session.scalar(
        select(LedgerEntry.id).where(LedgerEntry.related_entry_id == purchase.id, LedgerEntry.type == "refund")
    )
    if already:
        raise LedgerError("already_refunded")
    return await _append(
        session,
        wallet,
        -purchase.amount_paise,
        "refund",
        description=f"Refund: {purchase.description or ''}".strip(),
        merchant_id=purchase.merchant_id,
        related_entry_id=purchase.id,
    )
