"""Canteen / bookstore counter: set what the next tap on a reader does, watch the result, see sales
and refund them."""

from datetime import timedelta
from typing import Annotated
import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import SessionDep, StaffUser, require_role
from app.core.timeutil import local_day_start, now
from app.models import LedgerEntry, Merchant, PendingAction, TagScan, Terminal, User, Wallet
from app.schemas.admin import TerminalOut
from app.schemas.wallet import MerchantTransactionOut, PendingIn, PendingOut, RefundOut, ScanOut, TerminalStatusOut
from app.services import ledger

router = APIRouter(prefix="/merchant", tags=["merchant"])

# Counter staff operate their own shop's readers; admins can operate any reader in their school.
CashierUser = Annotated[User, require_role("merchant_staff", "admin")]


def _operable(user: User):
    if user.role == "admin":
        return Merchant.school_id == user.school_id
    return Merchant.id == user.merchant_id


async def _terminal(session: AsyncSession, user: User, terminal_id: uuid.UUID) -> Terminal:
    terminal = (
        await session.execute(
            select(Terminal).join(Merchant, Merchant.id == Terminal.merchant_id).where(Terminal.id == terminal_id, _operable(user))
        )
    ).scalar_one_or_none()
    if terminal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "terminal_not_found")
    return terminal


@router.get("/terminals", response_model=list[TerminalOut])
async def my_terminals(user: CashierUser, session: SessionDep):
    rows = await session.execute(
        select(Terminal, Merchant).join(Merchant, Merchant.id == Terminal.merchant_id).where(_operable(user)).order_by(Merchant.name, Terminal.name)
    )
    return [
        TerminalOut(
            id=t.id, name=t.name, merchant_id=m.id, merchant_name=m.name, merchant_kind=m.kind, active=t.active, last_seen_at=t.last_seen_at
        )
        for t, m in rows
    ]


@router.post("/terminals/{terminal_id}/pending", response_model=PendingOut, status_code=201)
async def set_pending(terminal_id: uuid.UUID, body: PendingIn, user: CashierUser, session: SessionDep):
    """Set what the next card tap on this reader does: `charge` (with `amount_paise`) or `redeem` a
    prize. Replaces anything already pending; lapses after a couple of minutes if nobody taps."""
    terminal = await _terminal(session, user, terminal_id)
    await session.execute(
        update(PendingAction)
        .where(PendingAction.terminal_id == terminal.id, PendingAction.status == "pending")
        .values(status="cancelled")
    )
    action = PendingAction(
        terminal_id=terminal.id,
        kind=body.kind,
        amount_paise=body.amount_paise if body.kind == "charge" else None,
        created_by=user.id,
        expires_at=now() + timedelta(seconds=get_settings().pending_action_seconds),
    )
    session.add(action)
    try:
        await session.commit()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "busy_try_again")
    return action


@router.delete("/terminals/{terminal_id}/pending", status_code=204)
async def cancel_pending(terminal_id: uuid.UUID, user: CashierUser, session: SessionDep):
    terminal = await _terminal(session, user, terminal_id)
    await session.execute(
        update(PendingAction)
        .where(PendingAction.terminal_id == terminal.id, PendingAction.status == "pending")
        .values(status="cancelled")
    )
    await session.commit()


@router.get("/terminals/{terminal_id}/status", response_model=TerminalStatusOut)
async def terminal_status(terminal_id: uuid.UUID, user: CashierUser, session: SessionDep):
    """Poll this (about once a second) while waiting for a tap: what's pending, and the last tap's result."""
    terminal = await _terminal(session, user, terminal_id)
    pending = (
        await session.execute(
            select(PendingAction).where(
                PendingAction.terminal_id == terminal.id, PendingAction.status == "pending", PendingAction.expires_at > now()
            )
        )
    ).scalar_one_or_none()
    last = (
        await session.execute(select(TagScan).where(TagScan.terminal_id == terminal.id).order_by(TagScan.created_at.desc()).limit(1))
    ).scalar_one_or_none()
    return TerminalStatusOut(
        terminal_id=terminal.id,
        terminal_name=terminal.name,
        last_seen_at=terminal.last_seen_at,
        pending=PendingOut.model_validate(pending) if pending else None,
        last_scan=ScanOut(**last.response) if last else None,
        last_scan_at=last.created_at if last else None,
        last_scan_uid=last.tag_uid if last else None,
    )


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
