"""HTTP endpoint для webhook ЮKassa (aiohttp, рядом с polling Telegram)."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web
from aiogram import Bot

from app.bot.keyboards import pay_done_continue_keyboard
from app.core.config import Settings
from app.integrations.yookassa.webhook_security import yookassa_webhook_peer_allowed
from app.services.yookassa_finalize import try_finalize_payment

logger = logging.getLogger(__name__)


def _parse_payment_notification(payload: dict[str, Any]) -> tuple[str | None, int | None, bool]:
    """
    (provider_payment_id, fallback_payment_db_id, should_finalize_success).
    """
    evt = str(payload.get("event") or "")
    obj = payload.get("object")
    if not isinstance(obj, dict):
        obj = {}

    oid = obj.get("id")
    oid_s = str(oid).strip() if oid else None
    paid_flag = obj.get("paid") is True
    status_ok = str(obj.get("status") or "") == "succeeded"
    finalize = evt == "payment.succeeded" or paid_flag or status_ok
    if not finalize:
        return oid_s, None, False

    md = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    fallback: int | None = None
    raw_db = md.get("payment_db_id")
    if isinstance(raw_db, str) and raw_db.isdigit():
        fallback = int(raw_db)
    elif isinstance(raw_db, int):
        fallback = raw_db

    return oid_s, fallback, True


async def handle_yookassa_webhook(request: web.Request) -> web.Response:
    session_factory = request.app["session_factory"]

    forwarded = ""
    xf = request.headers.get("X-Forwarded-For")
    if xf:
        forwarded = xf.split(",")[0].strip()

    peer = ""
    peer_obj = None
    if request.transport:
        peer_obj = request.transport.get_extra_info("peername")
        if isinstance(peer_obj, tuple) and peer_obj:
            peer = peer_obj[0] or ""

    if not peer and request.remote:
        peer = request.remote

    candidate = forwarded or peer
    if not candidate:
        logger.warning("ЮKassa webhook: не удалось определить IP (нет X-Forwarded-For и peer)")
        return web.Response(status=403, text="forbidden")

    if not yookassa_webhook_peer_allowed(candidate.split("%")[0].strip()):
        logger.warning("ЮKassa webhook: недоверенный peer %s (xff=%s raw=%s)", candidate, xf, peer)
        return web.Response(status=403, text="forbidden")

    try:
        body = await request.json()
        if not isinstance(body, dict):
            return web.Response(status=400)

        oid_s, fallback_id, finalize = _parse_payment_notification(body)
        if not finalize:
            return web.Response(status=200)

        if not oid_s:
            logger.warning("ЮKassa webhook: без id платежа")
            return web.Response(status=200)

        meta, pid, notify = await try_finalize_payment(
            oid_s,
            session_factory=session_factory,
            fallback_payment_db_id=fallback_id,
        )

        bot: Bot | None = request.app.get("bot")

        if notify and pid is not None and bot is not None and meta:
            telegram_raw = meta.get("telegram_user_id")
            try:
                tid = int(telegram_raw)
            except (TypeError, ValueError):
                tid = None
            if tid is not None:
                try:
                    await bot.send_message(
                        tid,
                        "Оплата получена ✅\nЕсли нужно продолжить оформление — нажмите кнопку ниже.",
                        reply_markup=pay_done_continue_keyboard(pid),
                        parse_mode=None,
                    )
                except Exception:
                    logger.exception("ЮKassa: не удалось уведомить пользователя %s", tid)

        if meta is None and pid is None and fallback_id:
            logger.info("ЮKassa webhook: finalize не нашёл платёж id=%s (возможен гон между созданием и id)", oid_s)

        return web.Response(status=200)
    except Exception:
        logger.exception("ЮKassa webhook: ошибка разбора/БД")
        return web.Response(status=200)


def create_yookassa_app(settings: Settings, bot: Bot, session_factory: Any) -> web.Application:
    app = web.Application()
    app["settings"] = settings
    app["bot"] = bot
    app["session_factory"] = session_factory

    path = (settings.yookassa_webhook_path or "/yookassa/webhook").strip() or "/yookassa/webhook"
    app.router.add_post(path, handle_yookassa_webhook)

    async def ping(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app.router.add_get("/health", ping)

    return app


class YooKassaHttpRunner:
    def __init__(self) -> None:
        self._runner: web.AppRunner | None = None

    async def start(self, settings: Settings, bot: Bot, session_factory: Any) -> None:
        if settings.yookassa_webhook_listen_port <= 0:
            return

        application = create_yookassa_app(settings, bot, session_factory)
        self._runner = web.AppRunner(application)
        await self._runner.setup()
        site = web.TCPSite(
            self._runner,
            host=settings.yookassa_webhook_listen_host,
            port=settings.yookassa_webhook_listen_port,
        )
        await site.start()
        logger.info(
            "ЮKassa webhook HTTP на %s:%s",
            settings.yookassa_webhook_listen_host,
            settings.yookassa_webhook_listen_port,
        )

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
