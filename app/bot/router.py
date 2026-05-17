from aiogram import Router

from app.bot.handlers import admin, common, consultation, documents, history, telegram_payments


def build_router() -> Router:
    router = Router()
    router.include_router(admin.router)
    router.include_router(common.router)
    router.include_router(telegram_payments.router)
    router.include_router(history.router)
    router.include_router(documents.router)
    router.include_router(consultation.router)
    return router
