from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.core.config import Settings
from app.services.payments import PaymentService


def test_admin_treated_as_unlimited_usage() -> None:
    settings = Settings(BOT_TOKEN="t", DEEPSEEK_API_KEYS="k", ADMIN_IDS="100,200")
    svc = PaymentService(settings)
    admin = SimpleNamespace(telegram_id=100, subscription_until=None)
    assert svc.is_usage_unlimited(admin) is True
    other = SimpleNamespace(telegram_id=999, subscription_until=None)
    assert svc.is_usage_unlimited(other) is False


def test_subscription_document_bypass_requires_flag() -> None:
    future = datetime.now(timezone.utc) + timedelta(days=1)
    subscribed = SimpleNamespace(telegram_id=500, subscription_until=future)

    off = Settings(
        BOT_TOKEN="t",
        DEEPSEEK_API_KEYS="k",
        SUBSCRIPTION_INCLUDES_UNLIMITED_DOCS=False,
    )
    assert PaymentService(off).is_usage_unlimited(subscribed) is False

    on = Settings(
        BOT_TOKEN="t",
        DEEPSEEK_API_KEYS="k",
        SUBSCRIPTION_INCLUDES_UNLIMITED_DOCS=True,
    )
    assert PaymentService(on).is_usage_unlimited(subscribed) is True
