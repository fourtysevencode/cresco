from datetime import datetime
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class ScanIn(BaseModel):
    tag_uid: str = Field(max_length=40, examples=["04A1B2C3D4E5F6"])
    # Unique per tap, chosen by the reader; resend the same one when retrying a timed-out request.
    scan_id: str = Field(min_length=1, max_length=64)


class ScanOut(BaseModel):
    result: Literal["approved", "declined", "identified", "unknown"]
    reason: str | None = None
    name: str | None = None
    balance_paise: int | None = None
    amount_paise: int | None = None
    reward: str | None = None


class HeartbeatOut(BaseModel):
    ok: bool = True
    terminal: str
    merchant: str
    server_time: int


# --- Cashier (dashboard) -------------------------------------------------------------------------


class PendingIn(BaseModel):
    kind: Literal["charge", "redeem"] = "charge"
    amount_paise: int | None = Field(default=None, gt=0, le=10_000_000)

    @model_validator(mode="after")
    def _amount_for_charge(self):
        if self.kind == "charge" and self.amount_paise is None:
            raise ValueError("amount_paise is required for a charge")
        return self


class PendingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    amount_paise: int | None
    status: str
    expires_at: datetime


class TerminalStatusOut(BaseModel):
    terminal_id: uuid.UUID
    terminal_name: str
    last_seen_at: datetime | None
    pending: PendingOut | None
    # The most recent scan on this reader, so the cashier sees the result of the tap.
    last_scan: ScanOut | None
    last_scan_at: datetime | None
    last_scan_uid: str | None
