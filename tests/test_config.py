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


def test_provider_token_survives_utf8_bom_prefix() -> None:
    bom_tok = "\ufeffstripe-like:token:value"
    s = Settings(
        BOT_TOKEN="t",
        DEEPSEEK_API_KEYS="k",
        TELEGRAM_PAYMENT_PROVIDER_TOKEN=bom_tok,
    )
    assert s.telegram_payment_provider_token == "stripe-like:token:value"
    assert s.telegram_native_payment_token_configured() is True


def test_deepseek_generation_timeout_default() -> None:
    s = Settings(BOT_TOKEN="t", DEEPSEEK_API_KEYS="k")
    assert s.deepseek_generation_timeout_seconds >= 120


def test_deepseek_model_strips_spaces() -> None:
    s = Settings(
        BOT_TOKEN="t",
        DEEPSEEK_API_KEYS="k",
        DEEPSEEK_MODEL="  deepseek-v4-pro ",
    )
    assert s.deepseek_model == "deepseek-v4-pro"

def test_telegram_provider_token_optional() -> None:
    empty = Settings(BOT_TOKEN="t", DEEPSEEK_API_KEYS="k")
    assert empty.telegram_native_payment_token_configured() is False
    filled = Settings(
        BOT_TOKEN="t",
        DEEPSEEK_API_KEYS="k",
        TELEGRAM_PAYMENT_PROVIDER_TOKEN="stripe-like-token",
    )
    assert filled.telegram_native_payment_token_configured() is True
