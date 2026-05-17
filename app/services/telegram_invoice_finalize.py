"""Финализация оплат через Telegram Invoice (ЮKassa / провайдер в BotFather)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import PaymentKind, PaymentStatus
from app.db.repositories import PaymentRepository
from app.services.payment_effects import apply_paid_payment_effects

logger = logging.getLogger(__name__)

# Официальный справочник валют для Bot Payments — поле «min_amount» для RUB (в минорных единицах exp=2, т.е. копейки):
# https://core.telegram.org/bots/payments/currencies.json
TELEGRAM_RUB_MIN_AMOUNT_MINOR = 8773


class TelegramInvoiceAmountTooLowError(ValueError):
    """Цена ниже нижней границы Telegram для fiat-RUB счёта."""


def rub_amount_to_telegram_minor_units(amount_rub: int) -> int:
    """Целые рубли → копейки (соответствует RUB.exp=2 в Telegram)."""
    rub = int(amount_rub)
    if rub <= 0:
        raise TelegramInvoiceAmountTooLowError(
            "Сумма платежа в рублях должна быть целым положительным числом для Telegram Payments."
        )
    return rub * 100


def enforce_telegram_rub_invoice_minimum(amount_rub: int, *, what: str) -> None:
    """
    Если суммы не хватает для Telegram Payments, платёж у провайдера часто завершается ошибкой уже после pre-checkout.

    Telegram зашивает ограничения в currencies.json для каждого кода валюты.
    """
    minor = rub_amount_to_telegram_minor_units(amount_rub)
    if minor < TELEGRAM_RUB_MIN_AMOUNT_MINOR:
        min_whole_rub = (TELEGRAM_RUB_MIN_AMOUNT_MINOR + 99) // 100
        raise TelegramInvoiceAmountTooLowError(
            f"Сумма {amount_rub} ₽ ниже минимальной для платежа счёта в Telegram (правило RUB в "
            f"https://core.telegram.org/bots/payments/currencies.json — не меньше ~{min_whole_rub} ₽ как целое). "
            f"Поднимите цену в .env ({what})."
        )


async def finalize_telegram_invoice_payment(
    session: AsyncSession,
    *,
    payment_db_id: int,
    telegram_user_id: int,
    telegram_payment_charge_id: str,
    total_amount_minor: int,
    currency: str,
) -> tuple[dict[str, Any] | None, int | None, bool]:
    """
    То же семейство возвращаемых значений, что у finalize_yoo_provider_payment:
    (meta или None, payment.id или None, notify_user).
    """
    pays = PaymentRepository(session)
    payment = await pays.get_payment(payment_db_id)
    tg_charge = telegram_payment_charge_id.strip()

    if payment is None:
        logger.warning("Telegram invoice: платёж не найден id=%s", payment_db_id)
        return None, None, False

    meta = payment.payment_meta or {}

    meta_owner = meta.get("telegram_user_id")
    try:
        owner_tid = int(meta_owner)
    except (TypeError, ValueError):
        owner_tid = None

    cur = (currency or "").strip().upper()
    paid_cur = (payment.currency or "RUB").strip().upper()
    expected_minor = rub_amount_to_telegram_minor_units(int(payment.amount))

    invalid = owner_tid != telegram_user_id or cur != paid_cur or int(total_amount_minor) != expected_minor
    if invalid:
        logger.warning(
            "Telegram invoice: несовпадение данных платежа db_id=%s user=%s!=%s amount=%s!=%s cur=%s!=%s",
            payment_db_id,
            owner_tid,
            telegram_user_id,
            total_amount_minor,
            expected_minor,
            cur,
            paid_cur,
        )
        return None, None, False

    kind = payment.payment_kind or PaymentKind.DOCUMENT.value
    if kind not in (
        PaymentKind.DOCUMENT.value,
        PaymentKind.SUBSCRIPTION.value,
    ):
        logger.warning(
            "Telegram invoice: неподдерживаемый тип платежа kind=%s id=%s",
            kind,
            payment_db_id,
        )
        return None, None, False

    if payment.status == PaymentStatus.PAID.value:
        existing = payment.telegram_payment_charge_id
        if existing and tg_charge and existing != tg_charge:
            logger.info(
                "Telegram invoice: уже paid другим charge db_id=%s",
                payment_db_id,
            )
        return meta, payment.id, False

    if payment.status != PaymentStatus.PENDING.value:
        logger.info(
            "Telegram invoice: статус не pending (%s), db_id=%s",
            payment.status,
            payment.id,
        )
        return None, None, False

    payment.telegram_payment_charge_id = tg_charge
    pays.mark_paid(payment)
    await apply_paid_payment_effects(session, payment)
    await session.flush()
    return payment.payment_meta or {}, payment.id, True


async def finalize_telegram_invoice_session(
    db_session: AsyncSession,
    *,
    payment_db_id: int,
    telegram_user_id: int,
    telegram_payment_charge_id: str,
    total_amount_minor: int,
    currency: str,
) -> tuple[dict[str, Any] | None, int | None, bool]:
    meta, pid, notify = await finalize_telegram_invoice_payment(
        db_session,
        payment_db_id=payment_db_id,
        telegram_user_id=telegram_user_id,
        telegram_payment_charge_id=telegram_payment_charge_id,
        total_amount_minor=total_amount_minor,
        currency=currency,
    )
    await db_session.commit()
    return meta, pid, notify


async def try_finalize_telegram_invoice_payment(
    *,
    session_factory: async_sessionmaker,
    payment_db_id: int,
    telegram_user_id: int,
    telegram_payment_charge_id: str,
    total_amount_minor: int,
    currency: str,
) -> tuple[dict[str, Any] | None, int | None, bool]:
    async with session_factory() as session:
        return await finalize_telegram_invoice_session(
            session,
            payment_db_id=payment_db_id,
            telegram_user_id=telegram_user_id,
            telegram_payment_charge_id=telegram_payment_charge_id,
            total_amount_minor=total_amount_minor,
            currency=currency,
        )
