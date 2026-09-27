import uuid

from pydantic import BaseModel, ConfigDict


class LoginIn(BaseModel):
    login: str  # email or phone
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class TokensOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    name: str
    email: str | None
    phone: str | None
    preferred_language: str | None
    school_id: uuid.UUID | None
    merchant_id: uuid.UUID | None
