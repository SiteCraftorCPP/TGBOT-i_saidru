from app.db.models import PaymentKind
from app.services.telegram_invoice_finalize import rub_amount_to_telegram_minor_units
from app.services.telegram_invoice_payload import encode_document_payment_payload, parse_telegram_invoice_payload


def test_invoice_payload_document_roundtrip() -> None:
    raw = encode_document_payment_payload(42)
    assert parse_telegram_invoice_payload(raw) == (PaymentKind.DOCUMENT.value, 42)


def test_rub_to_telegram_minor_units() -> None:
    assert rub_amount_to_telegram_minor_units(150) == 15000
