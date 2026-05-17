"""Нативные платежи Telegram: invoice, PreCheckoutQuery, SuccessfulPayment."""

from __future__ import annotations

import json
import logging

from aiogram import F, Router
from aiogram.types import LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.keyboards import main_menu, pay_done_continue_keyboard
from app.core.config import Settings
from app.db.models import PaymentKind, PaymentStatus
from app.db.repositories import PaymentRepository
from app.services.telegram_invoice_finalize import (
    finalize_subscription_month_invoice_via_telegram,
    rub_amount_to_telegram_minor_units,
    try_finalize_telegram_invoice_payment,
)
from app.services.telegram_invoice_payload import (
    SUBSCRIPTION_MONTH_INVOICE_PAYLOAD,
    encode_document_payment_payload,
    is_subscription_month_invoice_payload,
    parse_telegram_invoice_payload,
)

logger = logging.getLogger(__name__)
router = Router()

CHECK_FAILED_PRECHECKOUT = "Платёж не найден или данные счёта устарели. Начните оформление заново."

DOCUMENT_INVOICE_DESCRIPTION = "Подготовка документа"


def yookassa_telegram_invoice_extra_kwargs(
    settings: Settings,
    *,
    minor_amount: int,
    receipt_item_description: str,
) -> dict[str, object]:
    """
    Доп. аргументы send_invoice 1:1 по рабочему эталону (TGBOTAImbp77 subscribe_pay_callback): всегда
    need_email/need_phone для ЮKassa; при YOOKASSA_TAX_SYSTEM_CODE≠0 — provider_data с receipt.
    """
    out: dict[str, object] = {
        "need_email": True,
        "send_email_to_provider": True,
        "need_phone_number": True,
        "send_phone_number_to_provider": True,
    }

    tax_code_raw = getattr(settings, "yookassa_tax_system_code", 0)
    try:
        tax_code = int(tax_code_raw or 0)
    except (TypeError, ValueError):
        tax_code = 0
    if tax_code <= 0:
        return out

    desc = (receipt_item_description or "Услуга").strip()[:128]
    try:
        vat_code = int(settings.yookassa_vat_code or 1)
    except (TypeError, ValueError):
        vat_code = 1
    if minor_amount % 100 == 0:
        value_rub: float | int = int(minor_amount / 100)
    else:
        value_rub = round(minor_amount / 100.0, 2)

    receipt = {
        "receipt": {
            "tax_system_code": tax_code,
            "items": [
                {
                    "description": desc,
                    "quantity": 1,
                    "amount": {"value": value_rub, "currency": "RUB"},
                    "vat_code": vat_code,
                    "payment_mode": "full_payment",
                    # Как в эталонном боте (ЮKassa + Telegram принимает commodity).
                    "payment_subject": "commodity",
                },
            ],
        },
    }
    out["provider_data"] = json.dumps(receipt, ensure_ascii=False)
    return out


async def _validate_invoice_for_checkout(
    *,
    payload: str,
    telegram_user_id: int,
    total_amount_minor: int,
    currency: str,
    session_factory: async_sessionmaker,
) -> str | None:
    """Если платёж неверен — текст ошибки для answer(ok=False); иначе None."""
    raw = (payload or "").strip()
    parsed = parse_telegram_invoice_payload(raw)
    if not parsed:
        logger.warning(
            "Telegram pre-checkout: неверный формат invoice_payload user=%s payload(start 80)=%r",
            telegram_user_id,
            raw[:80],
        )
        return CHECK_FAILED_PRECHECKOUT
    kind_expected, pay_id = parsed
    async with session_factory() as session:
        pay = await PaymentRepository(session).get_payment(pay_id)
    if pay is None:
        logger.warning(
            "Telegram pre-checkout: нет платежа в БД pay_id=%s user=%s",
            pay_id,
            telegram_user_id,
        )
        return CHECK_FAILED_PRECHECKOUT
    meta = pay.payment_meta or {}
    try:
        owner_tid = int(meta.get("telegram_user_id"))
    except (TypeError, ValueError):
        logger.warning(
            "Telegram pre-checkout: в meta нет telegram_user_id pay_id=%s user=%s",
            pay_id,
            telegram_user_id,
        )
        return CHECK_FAILED_PRECHECKOUT
    cur = (currency or "").strip().upper()
    expected_cur = (pay.currency or "RUB").strip().upper()
    expected_minor = rub_amount_to_telegram_minor_units(int(pay.amount))
    actual_kind = pay.payment_kind or PaymentKind.DOCUMENT.value

    if (
        owner_tid != telegram_user_id
        or actual_kind != kind_expected
        or cur != expected_cur
        or int(total_amount_minor) != expected_minor
        or pay.status != PaymentStatus.PENDING.value
    ):
        mismatch = []
        if owner_tid != telegram_user_id:
            mismatch.append(f"owner_tid={owner_tid}!={telegram_user_id}")
        if actual_kind != kind_expected:
            mismatch.append(f"kind={actual_kind}!={kind_expected}")
        if cur != expected_cur:
            mismatch.append(f"currency={cur!r}!={expected_cur!r}")
        if int(total_amount_minor) != expected_minor:
            mismatch.append(f"amount_minor={total_amount_minor}!={expected_minor}")
        if pay.status != PaymentStatus.PENDING.value:
            mismatch.append(f"status={pay.status!r}!={PaymentStatus.PENDING.value}")
        logger.warning(
            "Telegram pre-checkout: отклонён платёж id=%s user=%s %s",
            pay_id,
            telegram_user_id,
            "; ".join(mismatch),
        )
        return CHECK_FAILED_PRECHECKOUT
    return None


@router.pre_checkout_query()
async def handle_pre_checkout(
    query: PreCheckoutQuery,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    uid = getattr(query.from_user, "id", 0)
    payload_s = str(query.invoice_payload or "")
    currency = query.currency or "RUB"
    total = int(query.total_amount)

    logger.info(
        "Telegram pre-checkout: вход user=%s total_minor=%s %s invoice_payload(first 80)=%r",
        uid,
        total,
        currency,
        payload_s[:80],
    )

    if not settings.payments_enabled or not settings.telegram_native_payment_token_configured():
        logger.warning(
            "Telegram pre-checkout: платежи отключены или нет TELEGRAM_PAYMENT_PROVIDER_TOKEN user=%s",
            uid,
        )
        await query.answer(ok=False, error_message="Платежи временно недоступны.")
        return
    payload_trim = payload_s.strip()

    if is_subscription_month_invoice_payload(payload_trim):
        cur = (currency or "").strip().upper()
        if cur != "RUB":
            logger.warning(
                "Telegram pre-checkout: подписка — нужен RUB, получено %s user=%s", cur, uid
            )
            await query.answer(ok=False, error_message="Поддерживается только RUB.")
            return
        try:
            expected_minor = rub_amount_to_telegram_minor_units(settings.subscription_price_rub)
        except Exception:
            logger.exception("Telegram pre-checkout: ошибка суммы подписки (.env)")
            await query.answer(ok=False, error_message="Платежи временно недоступны.")
            return
        if total != expected_minor:
            logger.warning(
                "Telegram pre-checkout: сумма подписки minor=%s != %s (.env)",
                total,
                expected_minor,
            )
            await query.answer(ok=False, error_message="Сумма не совпадает.")
            return
        logger.info(
            "Telegram pre-checkout: ок подписка (фикс. payload как эталон) user=%s total_minor=%s",
            uid,
            total,
        )
        await query.answer(ok=True)
        return

    err = await _validate_invoice_for_checkout(
        payload=payload_trim,
        telegram_user_id=uid,
        total_amount_minor=total,
        currency=currency,
        session_factory=session_factory,
    )
    if err:
        logger.warning(
            "Telegram pre-checkout: ответ ok=False user=%s total_minor=%s err=%s",
            uid,
            total,
            err[:300],
        )
        await query.answer(ok=False, error_message=err[:200])
        return

    parsed = parse_telegram_invoice_payload(payload_trim)
    pay_id_diag = parsed[1] if parsed else None
    logger.info(
        "Telegram pre-checkout: ок user=%s pay_db_id=%s total_minor=%s",
        uid,
        pay_id_diag,
        total,
    )
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def handle_successful_payment(
    message: Message,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    sp = message.successful_payment
    if sp is None:
        return
    if not settings.payments_enabled or not settings.telegram_native_payment_token_configured():
        logger.warning(
            "Telegram successful_payment без настройки платежей telegram_id=%s",
            message.from_user.id if message.from_user else "",
        )
        return

    tid = getattr(message.from_user, "id", 0)
    username = getattr(message.from_user, "username", None) if message.from_user else None
    payload_raw = str(sp.invoice_payload or "").strip()
    prov_charge = getattr(sp, "provider_payment_charge_id", None)

    if is_subscription_month_invoice_payload(payload_raw):
        meta, pid, notify = await finalize_subscription_month_invoice_via_telegram(
            session_factory=session_factory,
            subscription_price_rub=settings.subscription_price_rub,
            telegram_user_id=tid,
            telegram_username=username,
            telegram_payment_charge_id=sp.telegram_payment_charge_id,
            provider_payment_charge_id=prov_charge,
            total_amount_minor=int(sp.total_amount),
            currency=sp.currency,
        )
        logger.info(
            "Telegram successful_payment (подписка, эталон): user=%s pay_db_id=%s total_minor=%s tg_charge=%r prov_charge=%r",
            tid,
            pid,
            int(sp.total_amount),
            sp.telegram_payment_charge_id,
            prov_charge,
        )
    else:
        parsed = parse_telegram_invoice_payload(payload_raw)
        if not parsed:
            logger.warning(
                "Telegram successful_payment: не распарсили payload user=%s raw=%r",
                tid,
                sp.invoice_payload,
            )
            return
        _, pay_db_id = parsed

        meta, pid, notify = await try_finalize_telegram_invoice_payment(
            session_factory=session_factory,
            payment_db_id=pay_db_id,
            telegram_user_id=tid,
            telegram_payment_charge_id=sp.telegram_payment_charge_id,
            total_amount_minor=int(sp.total_amount),
            currency=sp.currency,
        )
        logger.info(
            "Telegram successful_payment от клиента: user=%s pay_db_id=%s total_minor=%s %s tg_charge=%r prov_charge=%r",
            tid,
            pay_db_id,
            int(sp.total_amount),
            sp.currency,
            sp.telegram_payment_charge_id,
            prov_charge,
        )

    if meta is None and pid is None:
        logger.warning("Telegram: не финализирован invoice payload=%s", payload_raw[:80])
        await message.answer("Не удалось подтвердить оплату в системе бота. Напишите в поддержку. ⚠️", parse_mode=None)
        return

    if notify and pid is not None:
        try:
            if is_subscription_month_invoice_payload(payload_raw):
                await message.answer(
                    "Подписка оплачена ✅\nДоступ продлён на 30 дней. Можете пользоваться ботом.",
                    reply_markup=main_menu(),
                    parse_mode=None,
                )
            else:
                await message.answer(
                    "Оплата получена ✅\nЕсли нужно продолжить оформление — нажмите кнопку ниже.",
                    reply_markup=pay_done_continue_keyboard(pid),
                    parse_mode=None,
                )
        except Exception:
            logger.exception("Telegram: не отправилось уведомление pay_id=%s", pid)


def telegram_document_invoice_kw(
    *,
    payment_row_id: int,
    price_rub: int,
    provider_token: str,
    settings: Settings,
) -> dict[str, object]:
    amount_minor = rub_amount_to_telegram_minor_units(price_rub)
    base = dict(
        title="Документ",
        description=DOCUMENT_INVOICE_DESCRIPTION,
        payload=encode_document_payment_payload(payment_row_id),
        provider_token=provider_token.strip(),
        currency="RUB",
        prices=[LabeledPrice(label="Документ", amount=amount_minor)],
    )
    extras = yookassa_telegram_invoice_extra_kwargs(
        settings,
        minor_amount=amount_minor,
        receipt_item_description=DOCUMENT_INVOICE_DESCRIPTION,
    )
    pd = extras.get("provider_data")
    tax_code = getattr(settings, "yookassa_tax_system_code", 0)
    logger.info(
        "Telegram invoice: формирование (документ) pay_row=%s minor=%s YOOKASSA_TAX_SYSTEM_CODE=%s provider_data=%s",
        payment_row_id,
        amount_minor,
        tax_code,
        "yes" if isinstance(pd, str) and pd else "no",
    )
    if isinstance(pd, str) and pd:
        logger.debug("Telegram invoice document provider_data: %s", pd[:900])

    base.update(extras)
    return base


def telegram_subscription_invoice_kw(
    *,
    price_rub: int,
    provider_token: str,
    settings: Settings,
) -> dict[str, object]:
    amount_minor = rub_amount_to_telegram_minor_units(price_rub)

    receipt_desc = f"Подписка @{settings.bot_username} (30 дн.)".replace("@@", "@")[:128]

    base = dict(
        title="Подписка 30 дней",
        description="Безлимитная генерация документов на месяц",
        payload=SUBSCRIPTION_MONTH_INVOICE_PAYLOAD,
        provider_token=provider_token.strip(),
        currency="RUB",
        prices=[LabeledPrice(label="Подписка на месяц", amount=amount_minor)],
    )
    extras = yookassa_telegram_invoice_extra_kwargs(
        settings,
        minor_amount=amount_minor,
        receipt_item_description=receipt_desc,
    )
    pd = extras.get("provider_data")
    tax_code = getattr(settings, "yookassa_tax_system_code", 0)
    logger.info(
        "Telegram invoice: формирование подписки minor=%s YOOKASSA_TAX_SYSTEM_CODE=%s provider_data=%s",
        amount_minor,
        tax_code,
        "yes" if isinstance(pd, str) and pd else "no",
    )
    if isinstance(pd, str) and pd:
        logger.debug("Telegram invoice subscription provider_data: %s", pd[:900])

    base.update(extras)
    return base


