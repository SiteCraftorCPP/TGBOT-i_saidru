import pytest

from app.db.models import PaymentKind
from app.services.telegram_invoice_finalize import (
    TELEGRAM_RUB_MIN_AMOUNT_MINOR,
    TelegramInvoiceAmountTooLowError,
    enforce_telegram_rub_invoice_minimum,
    rub_amount_to_telegram_minor_units,
)
from app.services.telegram_invoice_payload import (
    SUBSCRIPTION_MONTH_INVOICE_PAYLOAD,
    encode_document_payment_payload,
    is_subscription_month_invoice_payload,
    parse_telegram_invoice_payload,
)


def test_subscription_invoice_payload_constant() -> None:
    assert is_subscription_month_invoice_payload(SUBSCRIPTION_MONTH_INVOICE_PAYLOAD)
    assert is_subscription_month_invoice_payload(" " + SUBSCRIPTION_MONTH_INVOICE_PAYLOAD + " ")


def test_invoice_payload_document_roundtrip() -> None:
    raw = encode_document_payment_payload(42)
    assert parse_telegram_invoice_payload(raw) == (PaymentKind.DOCUMENT.value, 42)


def test_rub_to_telegram_minor_units() -> None:
    assert rub_amount_to_telegram_minor_units(150) == 15000


def test_zero_rub_rejected_for_telegram() -> None:
    with pytest.raises(TelegramInvoiceAmountTooLowError):
        rub_amount_to_telegram_minor_units(0)


def test_rub_below_telegram_min_rejected_in_enforcer() -> None:
    ceil_rub = (TELEGRAM_RUB_MIN_AMOUNT_MINOR + 99) // 100
    enforce_telegram_rub_invoice_minimum(ceil_rub, what="test")
    with pytest.raises(TelegramInvoiceAmountTooLowError):
        enforce_telegram_rub_invoice_minimum(ceil_rub - 1, what="test")
