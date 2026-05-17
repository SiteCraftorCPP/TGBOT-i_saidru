"""Нативные платежи Telegram: invoice, PreCheckoutQuery, SuccessfulPayment."""

from __future__ import annotations

import json
import logging

from aiogram import F, Router
from aiogram.types import LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.keyboards import pay_done_continue_keyboard
from app.core.config import Settings
from app.db.models import PaymentKind, PaymentStatus
from app.db.repositories import PaymentRepository
from app.services.telegram_invoice_finalize import (
    rub_amount_to_telegram_minor_units,
    try_finalize_telegram_invoice_payment,
)
from app.services.telegram_invoice_payload import (
    encode_document_payment_payload,
    encode_subscription_payment_payload,
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
    parsed = parse_telegram_invoice_payload(payload)
    if not parsed:
        return CHECK_FAILED_PRECHECKOUT
    kind_expected, pay_id = parsed
    async with session_factory() as session:
        pay = await PaymentRepository(session).get_payment(pay_id)
    if pay is None:
        return CHECK_FAILED_PRECHECKOUT
    meta = pay.payment_meta or {}
    try:
        owner_tid = int(meta.get("telegram_user_id"))
    except (TypeError, ValueError):
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
        logger.info(
            "Telegram invoice: отклонён pre-checkout pay_id=%s telegram_user=%s",
            pay_id,
            telegram_user_id,
        )
        return CHECK_FAILED_PRECHECKOUT
    return None


@router.pre_checkout_query()
async def handle_pre_checkout(
    query: PreCheckoutQuery,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    if not settings.payments_enabled or not settings.telegram_native_payment_token_configured():
        await query.answer(ok=False, error_message="Платежи временно недоступны.")
        return
    err = await _validate_invoice_for_checkout(
        payload=str(query.invoice_payload or ""),
        telegram_user_id=query.from_user.id,
        total_amount_minor=int(query.total_amount),
        currency=query.currency or "RUB",
        session_factory=session_factory,
    )
    if err:
        await query.answer(ok=False, error_message=err[:200])
    else:
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

    payload = parse_telegram_invoice_payload(sp.invoice_payload)
    if not payload:
        return
    _, pay_db_id = payload

    tid = getattr(message.from_user, "id", 0)

    meta, pid, notify = await try_finalize_telegram_invoice_payment(
        session_factory=session_factory,
        payment_db_id=pay_db_id,
        telegram_user_id=tid,
        telegram_payment_charge_id=sp.telegram_payment_charge_id,
        total_amount_minor=int(sp.total_amount),
        currency=sp.currency,
    )

    if meta is None and pid is None:
        logger.warning("Telegram: не финализирован invoice pay_id=%s", pay_db_id)
        await message.answer("Не удалось подтвердить оплату в системе бота. Напишите в поддержку. ⚠️", parse_mode=None)
        return

    if notify and pid is not None:
        try:
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
    base.update(
        yookassa_telegram_invoice_extra_kwargs(
            settings,
            minor_amount=amount_minor,
            receipt_item_description=DOCUMENT_INVOICE_DESCRIPTION,
        ),
    )
    return base


def telegram_subscription_invoice_kw(
    *,
    payment_row_id: int,
    price_rub: int,
    provider_token: str,
    settings: Settings,
) -> dict[str, object]:
    amount_minor = rub_amount_to_telegram_minor_units(price_rub)

    base = dict(
        title="Подписка 30 дней",
        description="Безлимитная генерация документов на месяц",
        payload=encode_subscription_payment_payload(payment_row_id),
        provider_token=provider_token.strip(),
        currency="RUB",
        prices=[LabeledPrice(label="Подписка на месяц", amount=amount_minor)],
    )
    base.update(
        yookassa_telegram_invoice_extra_kwargs(
            settings,
            minor_amount=amount_minor,
            receipt_item_description=f"Подписка @{settings.bot_username} (30 дн.)".replace("@@", "@")[
                :128
            ],
        ),
    )
    return base
