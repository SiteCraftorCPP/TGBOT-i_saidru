from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(default="", alias="BOT_TOKEN")
    bot_username: str = Field(default="My1Documents_bot", alias="BOT_USERNAME")
    admin_ids: str = Field(default="", alias="ADMIN_IDS")

    deepseek_api_keys: str = Field(default="", alias="DEEPSEEK_API_KEYS")
    deepseek_base_url: str = Field(default="https://api.deepseek.com/v1", alias="DEEPSEEK_BASE_URL")
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")
    deepseek_timeout_seconds: int = Field(default=45, alias="DEEPSEEK_TIMEOUT_SECONDS")
    deepseek_temperature: float = Field(default=0.2, alias="DEEPSEEK_TEMPERATURE")

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/my_documents_bot",
        alias="DATABASE_URL",
    )
    redis_url: str | None = Field(default=None, alias="REDIS_URL")

    telegram_proxy_url: str | None = Field(default=None, alias="TELEGRAM_PROXY_URL")
    payments_enabled: bool = Field(default=False, alias="PAYMENTS_ENABLED")
    document_price_rub: int = Field(default=100, alias="DOCUMENT_PRICE_RUB")
    subscription_price_rub: int = Field(default=499, alias="SUBSCRIPTION_PRICE_RUB")
    subscription_includes_unlimited_docs: bool = Field(
        default=False,
        alias="SUBSCRIPTION_INCLUDES_UNLIMITED_DOCS",
        description=(
            "Если true и у пользователя активна подписка (subscription_until) — генерация документов без отдельной оплаты. "
            "Если false — без отдельной оплаты только у ADMIN_IDS."
        ),
    )

    yookassa_shop_id: str = Field(default="", alias="YOOKASSA_SHOP_ID")
    yookassa_secret_key: str = Field(default="", alias="YOOKASSA_SECRET_KEY")
    yookassa_return_url: str = Field(default="", alias="YOOKASSA_RETURN_URL")
    telegram_payment_provider_token: str = Field(default="", alias="TELEGRAM_PAYMENT_PROVIDER_TOKEN")
    # Чек 54‑ФЗ через ЮKassa в Telegram: при 0 в send_invoice не передаём provider_data с receipt.
    yookassa_tax_system_code: int = Field(default=0, alias="YOOKASSA_TAX_SYSTEM_CODE")
    yookassa_vat_code: int = Field(default=1, alias="YOOKASSA_VAT_CODE")

    yookassa_webhook_listen_host: str = Field(default="127.0.0.1", alias="YOOKASSA_WEBHOOK_HOST")
    yookassa_webhook_listen_port: int = Field(default=0, alias="YOOKASSA_WEBHOOK_PORT")
    yookassa_webhook_path: str = Field(default="/yookassa/webhook", alias="YOOKASSA_WEBHOOK_PATH")

    libreoffice_path: str = Field(default="soffice", alias="LIBREOFFICE_PATH")
    storage_dir: Path = Field(default=Path("storage/generated"), alias="STORAGE_DIR")
    templates_dir: Path = Field(default=Path("templates"), alias="TEMPLATES_DIR")

    @field_validator(
        "telegram_payment_provider_token",
        "yookassa_shop_id",
        "yookassa_secret_key",
        "yookassa_return_url",
        mode="before",
    )
    @classmethod
    def _strip_sensitive_strings(cls, v: object) -> object:
        if v is None:
            return ""
        if isinstance(v, str):
            return v.replace("\ufeff", "").strip()
        return v

    @field_validator("deepseek_model", "deepseek_base_url", mode="before")
    @classmethod
    def _strip_deepseek_strings(cls, v: object) -> object:
        if v is None:
            return v
        if isinstance(v, str):
            return v.replace("\ufeff", "").strip()
        return v

    @property
    def admin_ids_list(self) -> list[int]:
        if not self.admin_ids:
            return []
        return [int(item.strip()) for item in self.admin_ids.split(",") if item.strip()]

    def is_admin(self, telegram_user_id: int) -> bool:
        return telegram_user_id in self.admin_ids_list

    def yookassa_configured(self) -> bool:
        return bool(self.yookassa_shop_id.strip() and self.yookassa_secret_key.strip())

    def telegram_native_payment_token_configured(self) -> bool:
        return bool(self.telegram_payment_provider_token.strip())

    @property
    def deepseek_api_keys_list(self) -> list[str]:
        if not self.deepseek_api_keys:
            return []
        return [item.strip() for item in self.deepseek_api_keys.split(",") if item.strip()]

    def validate_runtime(self) -> None:
        missing = []
        if not self.bot_token:
            missing.append("BOT_TOKEN")
        if not self.deepseek_api_keys:
            missing.append("DEEPSEEK_API_KEYS")
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(f"Заполните обязательные переменные окружения: {joined}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
