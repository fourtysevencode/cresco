import re
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password
from app.models import NfcCard, Student, User, Wallet


NO_LOGIN = "!"  # password_hash for accounts that can't log in (e.g. a student who only uses their card)

_UID = re.compile(r"[0-9A-F]{8,20}")


def normalize_uid(raw: str) -> str:
    """'04:a1:b2:c3' / '04 A1 B2 C3' / '04a1b2c3' -> '04A1B2C3'. Raises ValueError."""
    uid = re.sub(r"[\s:\-]", "", raw).upper()
    if not _UID.fullmatch(uid) or len(uid) % 2:
        raise ValueError("tag_uid must be the card's 4-10 byte serial number in hex, e.g. 04:A1:B2:C3")
    return uid


async def create_user(
    session: AsyncSession, *, role: str, name: str, email: str | None, phone: str | None, password: str | None, **fields
) -> User:
    """Adds (does not commit) a user. Raises 409 if the email or phone is taken. Without a password
    the account can't log in."""
    email = email.strip().lower() if email else None
    clauses = []
    if email:
        clauses.append(User.email == email)
    if phone:
        clauses.append(User.phone == phone)
    for clause in clauses:
        if await session.scalar(select(User.id).where(clause)):
            raise HTTPException(status.HTTP_409_CONFLICT, "account_exists")
    password_hash = hash_password(password) if password else NO_LOGIN
    user = User(role=role, name=name, email=email, phone=phone, password_hash=password_hash, **fields)
    session.add(user)
    await session.flush()
    return user


async def create_student(session: AsyncSession, school_id: uuid.UUID, *, grade: int, **account) -> User:
    user = await create_user(session, role="student", school_id=school_id, **account)
    session.add(Student(user_id=user.id, grade=grade))
    session.add(Wallet(student_id=user.id, balance_paise=0))
    await session.flush()
    return user


async def issue_card(session: AsyncSession, student: User, tag_uid: str, daily_limit_paise: int | None) -> NfcCard:
    """Maps a card's serial number to a student, retiring the student's previous card. Raises 409 if
    the card already belongs to someone else. Caller commits."""
    owner = (
        await session.execute(
            select(NfcCard, User.name)
            .join(User, User.id == NfcCard.student_id)
            .where(NfcCard.tag_uid == tag_uid, NfcCard.status == "active")
        )
    ).first()
    if owner:
        card, owner_name = owner
        if card.student_id == student.id:
            if daily_limit_paise is not None:
                card.daily_limit_paise = daily_limit_paise
            return card
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "card_in_use", "student_name": owner_name})

    current = (
        await session.execute(select(NfcCard).where(NfcCard.student_id == student.id, NfcCard.status == "active"))
    ).scalars().all()
    for old in current:
        old.status = "replaced"
    await session.flush()
    card = NfcCard(
        student_id=student.id,
        tag_uid=tag_uid,
        status="active",
        daily_limit_paise=daily_limit_paise if daily_limit_paise is not None else get_settings().default_daily_limit_paise,
    )
    session.add(card)
    await session.flush()
    return card
