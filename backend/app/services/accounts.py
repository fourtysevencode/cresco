import secrets
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password
from app.models import NfcCard, Student, User, Wallet


async def create_user(session: AsyncSession, *, role: str, name: str, email: str | None, phone: str | None, password: str, **fields) -> User:
    """Adds (does not commit) a user. Raises 409 if the email or phone is taken."""
    email = email.strip().lower() if email else None
    clauses = []
    if email:
        clauses.append(User.email == email)
    if phone:
        clauses.append(User.phone == phone)
    for clause in clauses:
        if await session.scalar(select(User.id).where(clause)):
            raise HTTPException(status.HTTP_409_CONFLICT, "account_exists")
    user = User(role=role, name=name, email=email, phone=phone, password_hash=hash_password(password), **fields)
    session.add(user)
    await session.flush()
    return user


async def create_student(session: AsyncSession, school_id: uuid.UUID, *, grade: int, **account) -> User:
    user = await create_user(session, role="student", school_id=school_id, **account)
    session.add(Student(user_id=user.id, grade=grade))
    session.add(Wallet(student_id=user.id, balance_paise=0))
    await session.flush()
    return user


async def issue_card(session: AsyncSession, student: User, tag_uid: str | None, daily_limit_paise: int | None) -> NfcCard:
    """Issues a new card, retiring any previous active card. Caller commits."""
    current = (
        await session.execute(select(NfcCard).where(NfcCard.student_id == student.id, NfcCard.status == "active"))
    ).scalars().all()
    for card in current:
        card.status = "replaced"
    await session.flush()
    card = NfcCard(
        student_id=student.id,
        card_token=secrets.token_hex(16),  # 128-bit random; not guessable from the student's id
        tag_uid=tag_uid.upper().replace(":", "") if tag_uid else None,
        status="active",
        daily_limit_paise=daily_limit_paise if daily_limit_paise is not None else get_settings().default_daily_limit_paise,
    )
    session.add(card)
    await session.flush()
    return card


def ndef_text(card: NfcCard, student: User) -> str:
    """The text written to the tag: format version, card token, and first name for display."""
    return f"CRESCO1|{card.card_token}|{student.first_name[:16]}"
