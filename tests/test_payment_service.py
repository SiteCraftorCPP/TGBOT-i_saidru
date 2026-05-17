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
