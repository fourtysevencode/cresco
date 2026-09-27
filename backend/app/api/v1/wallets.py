"""Wallet views, mock top-ups and parent controls (spend limit, lost card)."""

from datetime import datetime
import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, ParentUser, SessionDep, ensure_can_view_student
from app.core.timeutil import local_day_start
from app.models import LedgerEntry, NfcCard, ParentStudent, User, Wallet
from app.schemas.admin import CardOut
from app.schemas.wallet import SpendLimitIn, TopupIn, TransactionOut, WalletOut
from app.services import topup

router = APIRouter(tags=["wallet"])


async def _wallet_out(session: AsyncSession, student: User) -> WalletOut:
    wallet = (await session.execute(select(Wallet).where(Wallet.student_id == student.id))).scalar_one()
    card = await _active_card(session, student.id)
    spent = await session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount_paise), 0)).where(
            LedgerEntry.wallet_id == wallet.id, LedgerEntry.type == "purchase", LedgerEntry.created_at >= local_day_start()
        )
    )
    return WalletOut(
        student_id=student.id,
        student_name=student.name,
        balance_paise=wallet.balance_paise,
        card_status=card.status if card else None,
        daily_limit_paise=card.daily_limit_paise if card else None,
        spent_today_paise=-spent,
    )


async def _active_card(session: AsyncSession, student_id: uuid.UUID) -> NfcCard | None:
    return (
        await session.execute(
            select(NfcCard)
            .where(NfcCard.student_id == student_id, NfcCard.status != "replaced")
            .order_by(NfcCard.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _parent_child(session: AsyncSession, parent: User, student_id: uuid.UUID) -> User:
    if await session.get(ParentStudent, (parent.id, student_id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "student_not_found")
    student = await session.get(User, student_id)
    assert student is not None
    return student


@router.get("/parents/me/children", response_model=list[WalletOut])
async def my_children(parent: ParentUser, session: SessionDep):
    children = (
        await session.execute(
            select(User).join(ParentStudent, ParentStudent.student_id == User.id).where(ParentStudent.parent_id == parent.id).order_by(User.name)
        )
    ).scalars()
    return [await _wallet_out(session, child) for child in children]


@router.get("/wallets/{student_id}", response_model=WalletOut)
async def get_wallet(student_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    student = await ensure_can_view_student(session, user, student_id)
    return await _wallet_out(session, student)


@router.get("/wallets/{student_id}/transactions", response_model=list[TransactionOut])
async def list_transactions(
    student_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = Query(default=None, description="Pagination cursor: created_at of the last row seen"),
):
    await ensure_can_view_student(session, user, student_id)
    stmt = (
        select(LedgerEntry)
        .join(Wallet, Wallet.id == LedgerEntry.wallet_id)
        .where(Wallet.student_id == student_id)
        .order_by(LedgerEntry.created_at.desc())
        .limit(limit)
    )
    if before:
        stmt = stmt.where(LedgerEntry.created_at < before)
    return (await session.execute(stmt)).scalars().all()


@router.post("/wallets/{student_id}/topups", response_model=WalletOut, status_code=201)
async def add_money(student_id: uuid.UUID, body: TopupIn, parent: ParentUser, session: SessionDep):
    """Mock payment: the money is added instantly (the "Add money" button). No real payment is taken."""
    student = await _parent_child(session, parent, student_id)
    try:
        await topup.mock_topup(session, parent, student_id, body.amount_paise, body.idempotency_key)
    except topup.TopupError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, e.code)
    return await _wallet_out(session, student)


@router.patch("/students/{student_id}/spend-limit", response_model=CardOut)
async def set_spend_limit(student_id: uuid.UUID, body: SpendLimitIn, parent: ParentUser, session: SessionDep):
    await _parent_child(session, parent, student_id)
    card = await _active_card(session, student_id)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "card_not_found")
    card.daily_limit_paise = body.daily_limit_paise
    await session.commit()
    return card


@router.post("/students/{student_id}/card/block", response_model=CardOut)
async def block_card(student_id: uuid.UUID, parent: ParentUser, session: SessionDep):
    """Lost card: stop it working immediately. The school issues a new one."""
    await _parent_child(session, parent, student_id)
    card = await _active_card(session, student_id)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "card_not_found")
    card.status = "lost"
    await session.commit()
    return card
