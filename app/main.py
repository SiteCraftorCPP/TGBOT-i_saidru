import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from app.bot.router import build_router
from app.core.config import get_settings
from app.db.session import make_session_factory
from app.http.yookassa_webhook import YooKassaHttpRunner
from app.services.catalog import TemplateCatalog
from app.services.deepseek import DeepSeekClient
from app.services.documents import DocumentGenerator
from app.services.payments import PaymentService


async def main() -> None:
    log_level_name = (os.getenv("LOG_LEVEL") or "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger(__name__)
    settings = get_settings()
    settings.validate_runtime()
    logger.info(
        "DeepSeek активен: model=%s timeout_s=%s base_url=%s",
        settings.deepseek_model,
        settings.deepseek_timeout_seconds,
        settings.deepseek_base_url.rstrip("/"),
    )

    if settings.payments_enabled:
        if not settings.telegram_native_payment_token_configured() and not settings.yookassa_configured():
            logger.warning(
                "PAYMENTS_ENABLED=true, но в конфиге пустые TELEGRAM_PAYMENT_PROVIDER_TOKEN "
                "и пара YOOKASSA_SHOP_ID + YOOKASSA_SECRET_KEY. Платежи не заработают. "
                "Проверьте .env в каталоге WorkingDirectory systemd (одна строка, без кавычек вокруг значения "
                "и без пробелов вокруг знака '='), сохраните и сделайте restart сервиса."
            )
        elif settings.telegram_native_payment_token_configured() and int(settings.yookassa_tax_system_code or 0) <= 0:
            logger.warning(
                "ЮKassa через Telegram-счёт: YOOKASSA_TAX_SYSTEM_CODE=0 или не задан — в sendInvoice не отправляется "
                "provider_data с receipt (чек по 54‑ФЗ). Если в профиле магазина включены чеки ЮKassa, типичное "
                "сообщение у пользователя после pre-checkout — «Не удалось провести операцию». Задайте код СНО (1–6), "
                "как для рабочего эталона, и YOOKASSA_VAT_CODE."
            )

    catalog = TemplateCatalog(settings.templates_dir).load()
    deepseek = DeepSeekClient(settings)
    generator = DocumentGenerator(settings=settings, catalog=catalog, deepseek=deepseek)
    payment_service = PaymentService(settings)
    session_factory = make_session_factory(settings)

    storage = MemoryStorage()
    if settings.redis_url:
        storage = RedisStorage(Redis.from_url(settings.redis_url))

    session = AiohttpSession(proxy=settings.telegram_proxy_url)
    bot = Bot(
        token=settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=storage)
    dp.include_router(build_router())
    dp.workflow_data.update(
        settings=settings,
        session_factory=session_factory,
        catalog=catalog,
        deepseek=deepseek,
        generator=generator,
        payment_service=payment_service,
    )

    await bot.delete_webhook(drop_pending_updates=True)

    webhook_runner = YooKassaHttpRunner()
    if settings.payments_enabled and settings.yookassa_configured():
        if settings.yookassa_webhook_listen_port <= 0:
            logger.warning(
                "ЮKassa: PAYMENTS_ENABLED и ключи заданы, но YOOKASSA_WEBHOOK_PORT=0 — "
                "HTTP webhook не запущен, статус платежей не будет обновляться из уведомлений ЮKassa."
            )
        else:
            await webhook_runner.start(settings, bot, session_factory)

    try:
        await dp.start_polling(bot)
    finally:
        await webhook_runner.stop()
        await deepseek.aclose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
