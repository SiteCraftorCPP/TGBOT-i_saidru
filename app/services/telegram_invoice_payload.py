"""Короткие invoice payload для нативных платежей Telegram (лимит длины)."""

from __future__ import annotations

from app.db.models import PaymentKind

TG_DOC_PAYLOAD_PREFIX = "tgpd"
TG_SUB_PAYLOAD_PREFIX = "tgps"

SUBSCRIPTION_MONTH_INVOICE_PAYLOAD = "my1documents_sub_month_v1"


def is_subscription_month_invoice_payload(payload: str) -> bool:
    """Подписка в Telegram счёте без id строки БД — как эталон (pre-checkout только payload + сумма)."""
    return (payload or "").strip() == SUBSCRIPTION_MONTH_INVOICE_PAYLOAD


def encode_document_payment_payload(payment_id: int) -> str:
    return f"{TG_DOC_PAYLOAD_PREFIX}:{int(payment_id)}"


def parse_telegram_invoice_payload(payload: str) -> tuple[str, int] | None:
    raw = (payload or "").strip()
    if ":" not in raw:
        return None
    prefix, pid_s = raw.split(":", 1)
    prefix = prefix.strip()
    pid_s = pid_s.strip()
    if not pid_s.isdigit():
        return None
    pid = int(pid_s)
    if prefix == TG_DOC_PAYLOAD_PREFIX:
        kind = PaymentKind.DOCUMENT.value
    elif prefix == TG_SUB_PAYLOAD_PREFIX:
        kind = PaymentKind.SUBSCRIPTION.value
    else:
        return None
    return kind, pid
