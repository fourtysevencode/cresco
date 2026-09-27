"""Canteen / bookstore staff: sales history and refunds."""

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.core.deps import SessionDep, StaffUser
from app.core.timeutil import local_day_start
from app.models import LedgerEntry, User, Wallet
from app.schemas.wallet import MerchantTransactionOut, RefundOut
from app.services import ledger

router = APIRouter(prefix="/merchant", tags=["merchant"])


@router.get("/transactions", response_model=list[MerchantTransactionOut])
async def transactions(staff: StaffUser, session: SessionDep, today_only: bool = True, limit: int = Query(default=100, ge=1, le=500)):
    stmt = (
        select(LedgerEntry, User.name)
        .join(Wallet, Wallet.id == LedgerEntry.wallet_id)
        .join(User, User.id == Wallet.student_id)
        .where(LedgerEntry.merchant_id == staff.merchant_id)
        .order_by(LedgerEntry.created_at.desc())
        .limit(limit)
    )
    if today_only:
        stmt = stmt.where(LedgerEntry.created_at >= local_day_start())
    rows = await session.execute(stmt)
    return [
        MerchantTransactionOut(
            id=e.id,
            type=e.type,
            amount_paise=e.amount_paise,
            balance_after_paise=e.balance_after_paise,
            description=e.description,
            created_at=e.created_at,
            student_name=name,
        )
        for e, name in rows
    ]


@router.post("/transactions/{entry_id}/refund", response_model=RefundOut, status_code=201)
async def refund(entry_id: uuid.UUID, staff: StaffUser, session: SessionDep):
    purchase = await session.get(LedgerEntry, entry_id)
    if purchase is None or purchase.merchant_id != staff.merchant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "transaction_not_found")
    try:
        entry = await ledger.refund(session, purchase)
    except ledger.LedgerError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, e.code)
    await session.commit()
    return RefundOut(refund_id=entry.id, amount_paise=entry.amount_paise, balance_after_paise=entry.balance_after_paise)
