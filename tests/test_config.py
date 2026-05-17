from app.core.config import Settings


def test_parse_lists_from_env_strings() -> None:
    settings = Settings(
        BOT_TOKEN="token",
        ADMIN_IDS="1,2",
        DEEPSEEK_API_KEYS="sk-one,sk-two",
    )

    assert settings.admin_ids_list == [1, 2]
    assert settings.deepseek_api_keys_list == ["sk-one", "sk-two"]
    assert settings.is_admin(1) is True
    assert settings.is_admin(3) is False
    assert settings.yookassa_configured() is False


def test_yookassa_env_detects_configuration() -> None:
    s = Settings(
        BOT_TOKEN="t",
        DEEPSEEK_API_KEYS="k",
        YOOKASSA_SHOP_ID="123",
        YOOKASSA_SECRET_KEY="secret",
    )
    assert s.yookassa_configured() is True


def test_telegram_provider_token_optional() -> None:
    empty = Settings(BOT_TOKEN="t", DEEPSEEK_API_KEYS="k")
    assert empty.telegram_native_payment_token_configured() is False
    filled = Settings(
        BOT_TOKEN="t",
        DEEPSEEK_API_KEYS="k",
        TELEGRAM_PAYMENT_PROVIDER_TOKEN="stripe-like-token",
    )
    assert filled.telegram_native_payment_token_configured() is True
