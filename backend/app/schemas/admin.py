from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.languages import LanguageCode


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


class StudentCreate(_Account):
    grade: int = Field(ge=1, le=12)
    preferred_language: LanguageCode = "ta"


class StudentOut(BaseModel):
    id: uuid.UUID
    name: str
    grade: int
    school_id: uuid.UUID
    preferred_language: str | None


class ParentCreate(_Account):
    preferred_language: LanguageCode | None = None


class StaffCreate(_Account):
    merchant_id: uuid.UUID


class LinkStudentIn(BaseModel):
    student_id: uuid.UUID


class CardIssueIn(BaseModel):
    # Hardware UID of the tag (hex). Optional: if omitted it is bound on the first tap.
    tag_uid: str | None = Field(default=None, max_length=32)
    daily_limit_paise: int | None = Field(default=None, ge=0)


class CardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    student_id: uuid.UUID
    tag_uid: str | None
    status: str
    daily_limit_paise: int


class CardIssued(CardOut):
    card_token: str
    # Write this as an NDEF Text record on the tag.
    ndef_text: str


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
