"""Побочные эффекты после перевода платежа в paid (документ / подписка)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DocumentStatus, Payment, PaymentKind, User
from app.db.repositories import DocumentRepository, UserRepository

__all__ = ["apply_paid_payment_effects"]


async def apply_paid_payment_effects(session: AsyncSession, payment: Payment) -> None:
    """Вызывать когда у записи Payment уже status=paid (после mark_paid)."""
    kind = payment.payment_kind or PaymentKind.DOCUMENT.value

    if kind == PaymentKind.DOCUMENT.value:
        docs = DocumentRepository(session)
        if payment.document_id is not None:
            doc = await docs.get(payment.document_id)
            if doc is not None:
                await docs.update_status(doc, DocumentStatus.PAID)

    elif kind == PaymentKind.SUBSCRIPTION.value:
        users_repo = UserRepository(session)
        user = await session.get(User, payment.user_id)
        if user is not None:
            await users_repo.extend_subscription_month(user)

    await session.flush()
