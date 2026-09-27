from datetime import datetime
import uuid

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, created_at_col, uuid_pk

CARD_STATUSES = ("active", "blocked", "lost", "replaced")
MERCHANT_KINDS = ("canteen", "bookstore")
ENTRY_TYPES = ("topup", "purchase", "refund", "adjustment")


class NfcCard(Base):
    """A physical NFC tag. The tag holds only `card_token` (+ the student's name); money lives here."""

    __tablename__ = "nfc_cards"
    __table_args__ = (
        CheckConstraint(f"status IN {CARD_STATUSES}", name="status"),
        # At most one active card per student.
        Index("uq_nfc_cards_one_active", "student_id", unique=True, postgresql_where=text("status = 'active'")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    card_token: Mapped[str] = mapped_column(String(64), unique=True)
    # Hardware UID of the tag. If null, it is bound on the first successful tap.
    tag_uid: Mapped[str | None] = mapped_column(String(32))
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

