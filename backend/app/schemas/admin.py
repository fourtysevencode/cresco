from datetime import datetime
from typing import Annotated, Literal
import uuid

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.services.accounts import normalize_uid
from app.services.languages import LanguageCode

# A card's serial number; accepts "04:A1:B2:C3", "04 a1 b2 c3", "04A1B2C3".
TagUid = Annotated[str, Field(max_length=40, examples=["04:A1:B2:C3:D4:E5:F6"]), AfterValidator(normalize_uid)]


class _Account(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str | None = None
    phone: str | None = None
    password: str = Field(min_length=6)

    @model_validator(mode="after")
    def _needs_login(self):
        if not self.email and not self.phone:
            raise ValueError("email or phone is required")
        return self


class StudentCreate(BaseModel):
    """Create a student, optionally mapped straight to their NFC card's serial number. A login
    (email/phone + password) is only needed for the AI tutor; paying needs just the card."""

    name: str = Field(min_length=1, max_length=200)
    grade: int = Field(ge=1, le=12)
    preferred_language: LanguageCode = "ta"
    tag_uid: TagUid | None = None
    daily_limit_paise: int | None = Field(default=None, ge=0)
    email: str | None = None
    phone: str | None = None
    password: str | None = Field(default=None, min_length=6)

    @model_validator(mode="after")
    def _login_complete(self):
        if bool(self.email or self.phone) != bool(self.password):
            raise ValueError("a login needs both (email or phone) and password")
        return self


class StudentOut(BaseModel):
    id: uuid.UUID
    name: str
    grade: int
    school_id: uuid.UUID
    preferred_language: str | None
    email: str | None = None
    phone: str | None = None
    tag_uid: str | None = None
    card_status: str | None = None
    balance_paise: int = 0


class ParentCreate(_Account):
    preferred_language: LanguageCode | None = None


class StaffCreate(_Account):
    merchant_id: uuid.UUID


class LinkStudentIn(BaseModel):
    student_id: uuid.UUID


class CardIssueIn(BaseModel):
    tag_uid: TagUid
    daily_limit_paise: int | None = Field(default=None, ge=0)


class CardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    student_id: uuid.UUID
    tag_uid: str
    status: str
    daily_limit_paise: int


class CardLookupOut(BaseModel):
    tag_uid: str
    registered: bool
    student_id: uuid.UUID | None = None
    student_name: str | None = None
    card_status: str | None = None


class CardUpdate(BaseModel):
    status: Literal["active", "blocked", "lost"] | None = None
    daily_limit_paise: int | None = Field(default=None, ge=0)


class MerchantCreate(BaseModel):
    name: str
    kind: Literal["canteen", "bookstore"]


class MerchantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: str


class TerminalCreate(BaseModel):
    merchant_id: uuid.UUID
    name: str


class TerminalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    merchant_id: uuid.UUID
    merchant_name: str | None = None
    merchant_kind: str | None = None
    active: bool
    last_seen_at: datetime | None


class ScanLogOut(BaseModel):
    id: uuid.UUID
    terminal_id: uuid.UUID
    terminal_name: str
    tag_uid: str
    student_id: uuid.UUID | None
    student_name: str | None
    result: str
    reason: str | None
    amount_paise: int | None
    created_at: datetime


class TerminalCredentials(BaseModel):
    terminal_id: uuid.UUID
    name: str
    # Shown once. Put it in the reader's config.h as TERMINAL_SECRET.
    secret: str


class RewardConfigItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rank: int = Field(ge=1, le=10)
    type: Literal["free_icecream", "book_discount", "olympiad_fee"]
    title: str
    value_paise: int | None = None
    merchant_kind: Literal["canteen", "bookstore"]


class CloseWeekOut(BaseModel):
    week: str
    rewards_issued: int
