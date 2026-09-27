import uuid

from fastapi import APIRouter, HTTPException, status
import jwt
from sqlalchemy import or_, select

from app.core.deps import CurrentUser, SessionDep
from app.core.security import create_token, decode_token, verify_password
from app.models import User
from app.schemas.auth import LoginIn, RefreshIn, TokensOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens(user: User) -> TokensOut:
    return TokensOut(
        access_token=create_token(user.id, user.role, "access"),
        refresh_token=create_token(user.id, user.role, "refresh"),
    )


@router.post("/login", response_model=TokensOut)
async def login(body: LoginIn, session: SessionDep):
    login_id = body.login.strip()
    user = (
        await session.execute(select(User).where(or_(User.email == login_id.lower(), User.phone == login_id)))
    ).scalar_one_or_none()
    if user is None or not verify_password(user.password_hash, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials")
    return _tokens(user)


@router.post("/refresh", response_model=TokensOut)
async def refresh(body: RefreshIn, session: SessionDep):
    try:
        payload = decode_token(body.refresh_token, "refresh")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token")
    user = await session.get(User, uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token")
    return _tokens(user)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return user
