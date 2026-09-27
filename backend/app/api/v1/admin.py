"""School administration: accounts, NFC cards, shops, readers and prizes. Scoped to the admin's school."""

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import delete, select

from app.core.deps import AdminUser, SessionDep
from app.core.security import terminal_secret
from app.core.timeutil import iso_week, parse_iso_week, previous_iso_week
from app.models import Merchant, NfcCard, ParentStudent, RewardConfig, Student, Terminal, User
from app.schemas.admin import (
    CardIssued,
    CardIssueIn,
    CardOut,
    CardUpdate,
    CloseWeekOut,
    LinkStudentIn,
    MerchantCreate,
    MerchantOut,
    ParentCreate,
    RewardConfigItem,
    StaffCreate,
    StudentCreate,
    StudentOut,
    TerminalCreate,
    TerminalCredentials,
)
from app.schemas.auth import UserOut
from app.services import accounts, leaderboard

router = APIRouter(prefix="/admin", tags=["admin"])


async def _school_student(session, admin: User, student_id: uuid.UUID) -> User:
    student = await session.get(User, student_id)
    if student is None or student.role != "student" or student.school_id != admin.school_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "student_not_found")
    return student


async def _school_merchant(session, admin: User, merchant_id: uuid.UUID) -> Merchant:
    merchant = await session.get(Merchant, merchant_id)
    if merchant is None or merchant.school_id != admin.school_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "merchant_not_found")
    return merchant


@router.post("/students", response_model=StudentOut, status_code=201)
async def create_student(body: StudentCreate, admin: AdminUser, session: SessionDep):
    student = await accounts.create_student(session, admin.school_id, **body.model_dump())
    await session.commit()
    return StudentOut(
        id=student.id, name=student.name, grade=body.grade, school_id=student.school_id, preferred_language=student.preferred_language
    )


@router.get("/students", response_model=list[StudentOut])
async def list_students(admin: AdminUser, session: SessionDep):
    rows = await session.execute(
        select(User, Student.grade).join(Student, Student.user_id == User.id).where(User.school_id == admin.school_id).order_by(User.name)
    )
    return [
        StudentOut(id=u.id, name=u.name, grade=grade, school_id=u.school_id, preferred_language=u.preferred_language)
        for u, grade in rows
    ]


@router.post("/parents", response_model=UserOut, status_code=201)
async def create_parent(body: ParentCreate, admin: AdminUser, session: SessionDep):
    parent = await accounts.create_user(session, role="parent", **body.model_dump())
    await session.commit()
    return parent


@router.post("/parents/{parent_id}/students", status_code=204)
async def link_parent(parent_id: uuid.UUID, body: LinkStudentIn, admin: AdminUser, session: SessionDep):
    parent = await session.get(User, parent_id)
    if parent is None or parent.role != "parent":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "parent_not_found")
    await _school_student(session, admin, body.student_id)
    await session.merge(ParentStudent(parent_id=parent_id, student_id=body.student_id))
    await session.commit()


@router.post("/merchant-staff", response_model=UserOut, status_code=201)
async def create_staff(body: StaffCreate, admin: AdminUser, session: SessionDep):
    await _school_merchant(session, admin, body.merchant_id)
    staff = await accounts.create_user(session, role="merchant_staff", **body.model_dump())
    await session.commit()
    return staff


@router.post("/students/{student_id}/cards", response_model=CardIssued, status_code=201)
async def issue_card(student_id: uuid.UUID, body: CardIssueIn, admin: AdminUser, session: SessionDep):
    """Issue an NFC card. Returns the text to write to the tag; any previous card stops working."""
    student = await _school_student(session, admin, student_id)
    card = await accounts.issue_card(session, student, body.tag_uid, body.daily_limit_paise)
    await session.commit()
    return CardIssued(
        **CardOut.model_validate(card).model_dump(), card_token=card.card_token, ndef_text=accounts.ndef_text(card, student)
    )


@router.patch("/cards/{card_id}", response_model=CardOut)
async def update_card(card_id: uuid.UUID, body: CardUpdate, admin: AdminUser, session: SessionDep):
    card = await session.get(NfcCard, card_id)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "card_not_found")
    await _school_student(session, admin, card.student_id)
    if card.status == "replaced":
        raise HTTPException(status.HTTP_409_CONFLICT, "card_replaced")
    if body.status is not None:
        if body.status == "active":
            other = await session.scalar(
                select(NfcCard.id).where(NfcCard.student_id == card.student_id, NfcCard.status == "active", NfcCard.id != card.id)
            )
            if other:
                raise HTTPException(status.HTTP_409_CONFLICT, "student_has_active_card")
        card.status = body.status
    if body.daily_limit_paise is not None:
        card.daily_limit_paise = body.daily_limit_paise
    await session.commit()
    return card


@router.post("/merchants", response_model=MerchantOut, status_code=201)
async def create_merchant(body: MerchantCreate, admin: AdminUser, session: SessionDep):
    merchant = Merchant(school_id=admin.school_id, name=body.name, kind=body.kind)
    session.add(merchant)
    await session.commit()
    return merchant


@router.post("/terminals", response_model=TerminalCredentials, status_code=201)
async def create_terminal(body: TerminalCreate, admin: AdminUser, session: SessionDep):
    """Register an NFC reader. The secret is only shown here (and on rotation)."""
    await _school_merchant(session, admin, body.merchant_id)
    terminal = Terminal(merchant_id=body.merchant_id, name=body.name, secret_version=1)
    session.add(terminal)
    await session.commit()
    return TerminalCredentials(terminal_id=terminal.id, name=terminal.name, secret=terminal_secret(terminal.id, 1))


@router.post("/terminals/{terminal_id}/rotate-secret", response_model=TerminalCredentials)
async def rotate_terminal_secret(terminal_id: uuid.UUID, admin: AdminUser, session: SessionDep):
    terminal = await session.get(Terminal, terminal_id)
    if terminal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "terminal_not_found")
    await _school_merchant(session, admin, terminal.merchant_id)
    terminal.secret_version += 1
    await session.commit()
    return TerminalCredentials(
        terminal_id=terminal.id, name=terminal.name, secret=terminal_secret(terminal.id, terminal.secret_version)
    )


@router.get("/reward-config", response_model=list[RewardConfigItem])
async def get_reward_config(admin: AdminUser, session: SessionDep):
    return await leaderboard.reward_config(session, admin.school_id)


@router.put("/reward-config", response_model=list[RewardConfigItem])
async def set_reward_config(body: list[RewardConfigItem], admin: AdminUser, session: SessionDep):
    """Set which prize each leaderboard rank wins (default: top 3)."""
    if len({item.rank for item in body}) != len(body):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "duplicate_rank")
    await session.execute(delete(RewardConfig).where(RewardConfig.school_id == admin.school_id))
    for item in body:
        session.add(RewardConfig(school_id=admin.school_id, **item.model_dump()))
    await session.commit()
    return await leaderboard.reward_config(session, admin.school_id)


@router.post("/leaderboard/close-week", response_model=CloseWeekOut)
async def close_week(admin: AdminUser, session: SessionDep, week: str | None = Query(default=None, examples=["2026-W39"])):
    """Issue the week's prizes now (runs automatically every Sunday 23:59). Defaults to last week."""
    try:
        target = parse_iso_week(week) if week else previous_iso_week(iso_week())
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_week")
    issued = await leaderboard.close_week(session, target, admin.school_id)
    return CloseWeekOut(week=target, rewards_issued=len(issued))
