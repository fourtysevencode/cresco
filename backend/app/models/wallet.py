from datetime import datetime
import uuid

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, created_at_col, uuid_pk

CARD_STATUSES = ("active", "blocked", "lost", "replaced")
MERCHANT_KINDS = ("canteen", "bookstore")
ENTRY_TYPES = ("topup", "purchase", "refund", "adjustment")


class NfcCard(Base):
    """A physical NFC tag, identified only by its factory serial number (UID). Nothing is written to
    the tag; the student and the money live here."""

    __tablename__ = "nfc_cards"
    __table_args__ = (
        CheckConstraint(f"status IN {CARD_STATUSES}", name="status"),
        # At most one active card per student, and a tag can belong to only one active card.
        Index("uq_nfc_cards_one_active", "student_id", unique=True, postgresql_where=text("status = 'active'")),
        Index("uq_nfc_cards_active_uid", "tag_uid", unique=True, postgresql_where=text("status = 'active'")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    # Upper-case hex without separators, e.g. "04A1B2C3D4E5F6".
    tag_uid: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(10), default="active")
    daily_limit_paise: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = created_at_col()


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (CheckConstraint("balance_paise >= 0", name="non_negative"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    balance_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = created_at_col()


class Merchant(Base):
    __tablename__ = "merchants"
    __table_args__ = (CheckConstraint(f"kind IN {MERCHANT_KINDS}", name="kind"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schools.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = created_at_col()


class Terminal(Base):
    __tablename__ = "terminals"

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    secret_version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()


class TerminalNonce(Base):
    """Seen request nonces, to reject replayed terminal requests. Pruned by a periodic job."""

    __tablename__ = "terminal_nonces"

    terminal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("terminals.id", ondelete="CASCADE"), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = created_at_col()


class LedgerEntry(Base):
    """Append-only record of every balance change. Only app.services.ledger writes these."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint(f"type IN {ENTRY_TYPES}", name="type"),
        # A purchase can be refunded at most once.
        Index(
            "uq_ledger_entries_one_refund",
            "related_entry_id",
            unique=True,
            postgresql_where=text("type = 'refund'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    wallet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wallets.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger)  # negative = debit
    balance_after_paise: Mapped[int] = mapped_column(BigInteger)
    type: Mapped[str] = mapped_column(String(12))
    description: Mapped[str | None] = mapped_column(String(200))
    card_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nfc_cards.id"))
    terminal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("terminals.id"))
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("merchants.id"), index=True)
    related_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ledger_entries.id"))
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = created_at_col()



class PendingAction(Base):
    """What the next tap on a reader should do, set by the cashier on the dashboard ("charge ₹40",
    "redeem a prize"). A reader has at most one pending action; a successful tap completes it."""

    __tablename__ = "pending_actions"
    __table_args__ = (
        CheckConstraint("kind IN ('charge', 'redeem')", name="kind"),
        CheckConstraint("status IN ('pending', 'completed', 'cancelled')", name="status"),
        Index("uq_pending_actions_one_open", "terminal_id", unique=True, postgresql_where=text("status = 'pending'")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    terminal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("terminals.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    amount_paise: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_scan_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = created_at_col()


class TagScan(Base):
    """Every tap on every reader, with the response sent back. Doubles as the reader's idempotency
    record: a retried scan (same scan_id) gets the stored response instead of being processed again."""

    __tablename__ = "tag_scans"
    __table_args__ = (UniqueConstraint("terminal_id", "scan_id", name="uq_tag_scans_terminal_scan"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    terminal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("terminals.id", ondelete="CASCADE"), index=True)
    scan_id: Mapped[str] = mapped_column(String(64))
    tag_uid: Mapped[str] = mapped_column(String(32), index=True)
    student_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    pending_action_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pending_actions.id"))
    result: Mapped[str] = mapped_column(String(12))  # approved | declined | identified | unknown
    reason: Mapped[str | None] = mapped_column(String(40))
    response: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_col()
