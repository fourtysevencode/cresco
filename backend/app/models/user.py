from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, ForeignKey, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, created_at_col, uuid_pk

ROLES = ("student", "parent", "admin", "merchant_staff")


class School(Base):
    __tablename__ = "schools"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = created_at_col()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(f"role IN {ROLES}", name="role"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    role: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    preferred_language: Mapped[str | None] = mapped_column(String(5))
    # Students and admins belong to a school; merchant staff belong to a merchant.
    school_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("schools.id"), index=True)
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("merchants.id"))
    created_at: Mapped[datetime] = created_at_col()

    student: Mapped["Student | None"] = relationship(back_populates="user", uselist=False, lazy="raise")

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name else ""


class Student(Base):
    __tablename__ = "students"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    grade: Mapped[int] = mapped_column(SmallInteger)

    user: Mapped[User] = relationship(back_populates="student", lazy="raise")


class ParentStudent(Base):
    __tablename__ = "parent_students"

    parent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
