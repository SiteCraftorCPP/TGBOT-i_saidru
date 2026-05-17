from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.config import Settings
from app.db.models import Document, DocumentStatus, Payment, PaymentKind, PaymentStatus, User
from app.db.repositories import DocumentRepository, PaymentRepository
from app.integrations.yookassa.client import YooKassaApiError, YooKassaClient


PAYMENTS_CONFIGURE_HELP_TEXT = (
    "Оплата включена (PAYMENTS_ENABLED), но платёжка не сконфигурирована.\n\n"
    "Чтобы платить без браузера (кнопкой в Telegram), укажите TELEGRAM_PAYMENT_PROVIDER_TOKEN "
    "(BotFather → Payments, провайдер ЮKassa / ЮMoney и токен).\n\n"
    "Либо настройте сценарий по ссылке: YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, YOOKASSA_RETURN_URL "
    "и проброс webhook на ваш порт из YOOKASSA_WEBHOOK_HOST / YOOKASSA_WEBHOOK_PORT."
)


PAYMENTS_TURNED_OFF_USER_MESSAGE = (
    "Платное оформление документов сейчас недоступно. "
    "Если нужна помощь — напишите администратору."
)


class PaymentService:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def subscription_active(user: User) -> bool:
        until = user.subscription_until
        if until is None:
            return False
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return until > datetime.now(timezone.utc)

    def is_usage_unlimited(self, user: User) -> bool:
        """
        По умолчанию без отдельной оплаты за документ — только ADMIN_IDS.
        Если SUBSCRIPTION_INCLUDES_UNLIMITED_DOCS=true и подписка активна по дате — тоже без отдельной оплаты.
        """
        if self.settings.is_admin(user.telegram_id):
            return True
        return bool(self.settings.subscription_includes_unlimited_docs and self.subscription_active(user))

    async def prepare_document_access(
        self,
        *,
        document: Document,
        documents: DocumentRepository,
        payments: PaymentRepository,
        user: User,
        flow_context: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None, int | None]:
        """
        True, None, None — можно генерировать (без оплаты / уже оплачено).
        False, url или None, None — redirect ЮKassa (открыть ссылку браузере).
        False, None, payment_row_id — нативный invoice в Telegram для этой строки payments.
        False, None, None — нельзя продолжить: PAYMENTS_ENABLED=false или нет средств платежки.
        """
        if self.is_usage_unlimited(user):
            await documents.update_status(document, DocumentStatus.PAID)
            return True, None, None

        if not self.settings.payments_enabled:
            return False, None, None

        tg_native = self.settings.telegram_native_payment_token_configured()
        yoo = self.settings.yookassa_configured()
        if not tg_native and not yoo:
            return False, None, None

        ctx = flow_context or {}
        idem = str(uuid.uuid4())
        db_meta: dict[str, Any] = {
            "telegram_user_id": user.telegram_id,
            "payment_purpose": "document",
            "request_text": str(ctx.get("request_text", ""))[:20000],
            "details_text": str(ctx.get("details_text", ""))[:20000],
            "document_title": str(ctx.get("document_title", ""))[:500],
        }
        amount_rub = self.settings.document_price_rub

        pay = await payments.create(
            user_id=document.user_id,
            document_id=document.id,
            amount=amount_rub,
            payment_kind=PaymentKind.DOCUMENT,
            status=PaymentStatus.PENDING,
            payment_meta=db_meta,
            idempotency_key=idem,
        )

        if tg_native:
            await payments.flush()
            return False, None, pay.id

        assert yoo

        yoo_meta = {
            "payment_db_id": str(pay.id),
            "telegram_user_id": str(user.telegram_id),
            "purpose": PaymentKind.DOCUMENT.value,
        }

        amt_str = f"{amount_rub}.00"

        try:
            client = YooKassaClient(self.settings)
            payment_response = await client.create_payment_redirect(
                amount_value=amt_str,
                description=f"Документ №{document.id}",
                metadata=yoo_meta,
                idempotence_key=idem,
            )
        except YooKassaApiError:
            raise

        pay_url = ""
        if payment_response.confirmation and payment_response.confirmation.confirmation_url:
            pay_url = payment_response.confirmation.confirmation_url
        pay.provider_payment_charge_id = payment_response.id
        await payments.flush()
        return False, pay_url or None, None


async def create_pending_subscription_payment(
    payments: PaymentRepository,
    user: User,
    settings: Settings,
) -> Payment:
    """Строка PENDING для подписки без вызова API ЮKassa (для Telegram invoice)."""
    idem = str(uuid.uuid4())
    amount_rub = settings.subscription_price_rub
    db_meta: dict[str, Any] = {
        "telegram_user_id": user.telegram_id,
        "payment_purpose": PaymentKind.SUBSCRIPTION.value,
    }
    return await payments.create(
        user_id=user.id,
        document_id=None,
        amount=amount_rub,
        payment_kind=PaymentKind.SUBSCRIPTION,
        status=PaymentStatus.PENDING,
        payment_meta=db_meta,
        idempotency_key=idem,
    )


async def create_yookassa_subscription_payment(
    *,
    settings: Settings,
    payments: PaymentRepository,
    user: User,
) -> tuple[str | None, int | None]:
    """
    POST /payments в ЮKassa для подписки. Возвращает (confirmation_url или None, payment_row_id или None).
    Вызывает YooKassaApiError при сбое API (запись PENDING уже будет в БД).
    """
    pay = await create_pending_subscription_payment(payments, user, settings)
    idem = pay.idempotency_key or str(uuid.uuid4())

    yoo_meta = {
        "payment_db_id": str(pay.id),
        "telegram_user_id": str(user.telegram_id),
        "purpose": PaymentKind.SUBSCRIPTION.value,
    }

    client = YooKassaClient(settings)
    yoo = await client.create_payment_redirect(
        amount_value=f"{settings.subscription_price_rub}.00",
        description="Подписка на 30 дней",
        metadata=yoo_meta,
        idempotence_key=idem,
    )
    url = ""
    if yoo.confirmation and yoo.confirmation.confirmation_url:
        url = yoo.confirmation.confirmation_url
    pay.provider_payment_charge_id = yoo.id
    await payments.flush()
    return url or None, pay.id
