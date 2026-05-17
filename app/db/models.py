from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.types import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class DocumentStatus(StrEnum):
    UNPAID = "unpaid"
    PAID = "paid"
    COLLECTING_DATA = "collecting_data"
    GENERATING = "generating"
    GENERATED = "generated"
    DELIVERED = "delivered"


class PaymentStatus(StrEnum):
    BYPASSED = "bypassed"
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"


class PaymentKind(StrEnum):
    DOCUMENT = "document"
    SUBSCRIPTION = "subscription"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    subscription_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    consultations: Mapped[list["Consultation"]] = relationship(back_populates="user")
    documents: Mapped[list["Document"]] = relationship(back_populates="user")
    payments: Mapped[list["Payment"]] = relationship(back_populates="user")


class Consultation(TimestampMixin, Base):
    __tablename__ = "consultations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    problem_text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    consultation_text: Mapped[str] = mapped_column(Text, nullable=False)
    risks: Mapped[str | None] = mapped_column(Text)
    next_steps: Mapped[str | None] = mapped_column(Text)
    recommended_document: Mapped[str | None] = mapped_column(String(255))
    document_type: Mapped[str | None] = mapped_column(String(120))
    raw_ai_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    user: Mapped[User] = relationship(back_populates="consultations")
    documents: Mapped[list["Document"]] = relationship(back_populates="consultation")


class Document(TimestampMixin, Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    consultation_id: Mapped[int | None] = mapped_column(ForeignKey("consultations.id"))
    document_type: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    answers_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    document_text: Mapped[str | None] = mapped_column(Text)
    instruction_text: Mapped[str | None] = mapped_column(Text)
    docx_path: Mapped[str | None] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32), default=DocumentStatus.UNPAID.value, nullable=False, index=True
    )

    user: Mapped[User] = relationship(back_populates="documents")
    consultation: Mapped[Consultation | None] = relationship(back_populates="documents")
    payments: Mapped[list["Payment"]] = relationship(back_populates="document")


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(16), default="RUB", nullable=False)
    payment_kind: Mapped[str] = mapped_column(
        String(32), default=PaymentKind.DOCUMENT.value, nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default=PaymentStatus.PENDING.value, nullable=False, index=True
    )
    provider_payment_charge_id: Mapped[str | None] = mapped_column(String(255))
    telegram_payment_charge_id: Mapped[str | None] = mapped_column(String(255))
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    payment_meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    user: Mapped[User] = relationship(back_populates="payments")
    document: Mapped["Document | None"] = relationship(back_populates="payments")
