import json

from app.bot.handlers.telegram_payments import yookassa_telegram_invoice_extra_kwargs
from app.core.config import Settings


def _base(**kwargs: object) -> Settings:
    data = dict(
        BOT_TOKEN="t",
        DEEPSEEK_API_KEYS="k",
    )
    data.update(kwargs)
    return Settings(**data)


def test_no_provider_data_when_tax_code_zero() -> None:
    s = _base(YOOKASSA_TAX_SYSTEM_CODE=0)
    d = yookassa_telegram_invoice_extra_kwargs(
        s,
        minor_amount=10_000,
        receipt_item_description="Тестовая услуга",
    )
    assert "provider_data" not in d
    assert d.get("need_email") is True
    assert d.get("need_phone_number") is True


def test_receipt_payload_when_tax_nonzero_matches_reference_subject() -> None:
    s = _base(YOOKASSA_TAX_SYSTEM_CODE=1, YOOKASSA_VAT_CODE=1)
    d = yookassa_telegram_invoice_extra_kwargs(
        s,
        minor_amount=10_099,
        receipt_item_description="Подготовка документа",
    )
    assert d.get("need_email") is True
    pd = json.loads(str(d["provider_data"]))
    item = pd["receipt"]["items"][0]
    assert item["amount"]["value"] == 100.99
    assert item["amount"]["currency"] == "RUB"
    assert item["vat_code"] == 1
    assert item["payment_subject"] == "commodity"


def test_receipt_uses_integer_rub_when_kopecks_divisible_by_100() -> None:
    s = _base(YOOKASSA_TAX_SYSTEM_CODE=6, YOOKASSA_VAT_CODE=2)
    d = yookassa_telegram_invoice_extra_kwargs(
        s,
        minor_amount=9_800,
        receipt_item_description="Услуга",
    )
    item = json.loads(str(d["provider_data"]))["receipt"]["items"][0]
    assert item["amount"]["value"] == 98
