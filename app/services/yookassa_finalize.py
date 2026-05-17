"""Применение успешной оплаты ЮKassa к записям в БД (webhook idempotent)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import DocumentStatus, PaymentKind, PaymentStatus, User
from app.db.repositories import DocumentRepository, PaymentRepository, UserRepository

logger = logging.getLogger(__name__)


async def finalize_yoo_provider_payment(
    session: AsyncSession,
    *,
    provider_payment_id: str,
    fallback_payment_db_id: int | None = None,
) -> tuple[dict[str, Any] | None, int | None, bool]:
    """
    (meta или None, payment.id или None, notify_user).
    notify_user=True только при первом переводе pending → paid в этой транзакции.
    """
    pays = PaymentRepository(session)

    payment = await pays.by_provider_charge_id(provider_payment_id)
    if payment is None and fallback_payment_db_id is not None:
        payment = await pays.get_payment(fallback_payment_db_id)
        if payment is not None and not payment.provider_payment_charge_id:
            payment.provider_payment_charge_id = provider_payment_id

    if payment is None:
        logger.warning("YooKassa: платёж не найден для id=%s", provider_payment_id)
        return None, None, False

    if payment.status == PaymentStatus.PAID.value:
        return payment.payment_meta or {}, payment.id, False

    if payment.status != PaymentStatus.PENDING.value:
        logger.info(
            "YooKassa: статус не pending (%s), db_id=%s — пропуск",
            payment.status,
            payment.id,
        )
        return None, None, False

    payment.provider_payment_charge_id = provider_payment_id
    pays.mark_paid(payment)

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
    return payment.payment_meta or {}, payment.id, True


async def finalize_yoo_payment_session(
    db_session: AsyncSession,
    *,
    provider_payment_id: str,
    fallback_payment_db_id: int | None = None,
) -> tuple[dict[str, Any] | None, int | None, bool]:
    meta, pid, notify = await finalize_yoo_provider_payment(
        db_session,
        provider_payment_id=provider_payment_id,
        fallback_payment_db_id=fallback_payment_db_id,
    )
    await db_session.commit()
    return meta, pid, notify


async def try_finalize_payment(
    provider_payment_id: str,
    *,
    session_factory: async_sessionmaker,
    fallback_payment_db_id: int | None = None,
) -> tuple[dict[str, Any] | None, int | None, bool]:
    async with session_factory() as session:
        return await finalize_yoo_payment_session(
            session,
            provider_payment_id=provider_payment_id,
            fallback_payment_db_id=fallback_payment_db_id,
        )
