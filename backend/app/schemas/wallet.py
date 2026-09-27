from datetime import datetime
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field


class WalletOut(BaseModel):
    student_id: uuid.UUID
    student_name: str
    balance_paise: int
    card_status: str | None
    daily_limit_paise: int | None
    spent_today_paise: int


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    amount_paise: int
    balance_after_paise: int
    description: str | None
    created_at: datetime


class TopupIn(BaseModel):
    amount_paise: int = Field(gt=0)
    # Send the same key if the button is pressed twice for one top-up; it's credited once.
    idempotency_key: str | None = Field(default=None, max_length=64)


class SpendLimitIn(BaseModel):
    daily_limit_paise: int = Field(ge=0)


class RefundOut(BaseModel):
    refund_id: uuid.UUID
    amount_paise: int
    balance_after_paise: int


class MerchantTransactionOut(TransactionOut):
    student_name: str


# --- Terminal (ESP32) -------------------------------------------------------------------------
# Kept flat and small so ArduinoJson on the reader can parse them cheaply.


class ChargeIn(BaseModel):
    card_token: str = Field(max_length=64)
    tag_uid: str | None = Field(default=None, max_length=32)
    amount_paise: int = Field(gt=0, le=10_000_000)
    idempotency_key: str = Field(min_length=1, max_length=64)


class ChargeOut(BaseModel):
    status: Literal["approved", "declined"]
    reason: str | None = None
    name: str | None = None
    balance_paise: int | None = None


class RedeemIn(BaseModel):
    card_token: str = Field(max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=64)


class RedeemOut(BaseModel):
    status: Literal["approved", "declined"]
    reason: str | None = None
    name: str | None = None
    reward: str | None = None
    value_paise: int | None = None


class HeartbeatOut(BaseModel):
    ok: bool = True
    terminal: str
    merchant: str
    server_time: int
