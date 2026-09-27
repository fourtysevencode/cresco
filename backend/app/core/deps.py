from datetime import UTC, datetime
from typing import Annotated
import uuid

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import decode_token, terminal_secret, verify_terminal_signature
from app.models import Merchant, ParentStudent, Terminal, TerminalNonce, User

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
    try:
        payload = decode_token(credentials.credentials, "access")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token")
    user = await session.get(User, uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: str):
    async def checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        return user

    return Depends(checker)


StudentUser = Annotated[User, require_role("student")]
ParentUser = Annotated[User, require_role("parent")]
AdminUser = Annotated[User, require_role("admin")]
StaffUser = Annotated[User, require_role("merchant_staff")]


async def ensure_can_view_student(session: AsyncSession, user: User, student_id: uuid.UUID) -> User:
    """Students see themselves, parents see linked children, admins see their school. Returns the student."""
    student = await session.get(User, student_id)
    if student is None or student.role != "student":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "student_not_found")
    allowed = False
    if user.role == "student":
        allowed = user.id == student_id
    elif user.role == "parent":
        allowed = await session.get(ParentStudent, (user.id, student_id)) is not None
    elif user.role == "admin":
        allowed = user.school_id is not None and user.school_id == student.school_id
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "student_not_found")
    return student


# --- Terminal (ESP32) authentication -------------------------------------------------------


class TerminalContext:
    def __init__(self, terminal: Terminal, merchant: Merchant):
        self.terminal = terminal
        self.merchant = merchant


async def get_terminal(
    request: Request,
    session: SessionDep,
    x_terminal_id: Annotated[str, Header()],
    x_timestamp: Annotated[str, Header()],
    x_nonce: Annotated[str, Header()],
    x_signature: Annotated[str, Header()],
) -> TerminalContext:
    """Verifies X-Signature = hex(HMAC-SHA256(secret, "<timestamp>.<nonce>.<raw body>"))."""
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_signature")
    try:
        terminal_id = uuid.UUID(x_terminal_id)
        ts = int(x_timestamp)
    except ValueError:
        raise unauthorized
    if not (8 <= len(x_nonce) <= 64):
        raise unauthorized
    if abs(datetime.now(UTC).timestamp() - ts) > get_settings().terminal_clock_skew_seconds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "stale_timestamp")

    terminal = await session.get(Terminal, terminal_id)
    if terminal is None or not terminal.active:
        raise unauthorized
    body = await request.body()
    secret = terminal_secret(terminal.id, terminal.secret_version)
    if not verify_terminal_signature(secret, x_timestamp, x_nonce, body, x_signature):
        raise unauthorized

    inserted = await session.execute(
        insert(TerminalNonce)
        .values(terminal_id=terminal.id, nonce=x_nonce)
        .on_conflict_do_nothing()
        .returning(TerminalNonce.nonce)
    )
    if inserted.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "replayed_nonce")
    terminal.last_seen_at = datetime.now(UTC)
    await session.commit()

    merchant = (await session.execute(select(Merchant).where(Merchant.id == terminal.merchant_id))).scalar_one()
    return TerminalContext(terminal, merchant)


TerminalDep = Annotated[TerminalContext, Depends(get_terminal)]
