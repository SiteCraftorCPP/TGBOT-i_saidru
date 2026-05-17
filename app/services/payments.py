from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.config import Settings
from app.db.models import Document, DocumentStatus, PaymentKind, PaymentStatus, User
from app.db.repositories import DocumentRepository, PaymentRepository
from app.integrations.yookassa.client import YooKassaApiError, YooKassaClient


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
        """Администраторы (ADMIN_IDS) и пользователи с активной подпиской — без отдельной оплаты за документ."""
        if self.settings.is_admin(user.telegram_id):
            return True
        return self.subscription_active(user)

    async def prepare_document_access(
        self,
        *,
        document: Document,
        documents: DocumentRepository,
        payments: PaymentRepository,
        user: User,
        flow_context: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        """
        True, None — можно генерировать (подписка / безлимит / тест без списания).
        False, url — нужна ЮKassa, открыть ссылку возврата redirect.
        False, None — оплата «включена», но платёжка не сконфигурирована (URL не создан).
        """
        if self.is_usage_unlimited(user):
            await documents.update_status(document, DocumentStatus.PAID)
            return True, None

        if self.settings.payments_enabled:
            if not self.settings.yookassa_configured():
                return False, None

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

            yoo_meta = {
                "payment_db_id": str(pay.id),
                "telegram_user_id": str(user.telegram_id),
                "purpose": PaymentKind.DOCUMENT.value,
            }

            amt_str = f"{amount_rub}.00"

            try:
                client = YooKassaClient(self.settings)
                yoo = await client.create_payment_redirect(
                    amount_value=amt_str,
                    description=f"Документ №{document.id}",
                    metadata=yoo_meta,
                    idempotence_key=idem,
                )
            except YooKassaApiError:
                raise

            pay_url = ""
            if yoo.confirmation and yoo.confirmation.confirmation_url:
                pay_url = yoo.confirmation.confirmation_url
            pay.provider_payment_charge_id = yoo.id
            await payments.flush()
            return False, pay_url or None

        await payments.create(
            user_id=document.user_id,
            document_id=document.id,
            amount=self.settings.document_price_rub,
            payment_kind=PaymentKind.DOCUMENT,
            status=PaymentStatus.BYPASSED,
        )
        await documents.update_status(document, DocumentStatus.PAID)
        return True, None


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
    idem = str(uuid.uuid4())
    amount_rub = settings.subscription_price_rub
    db_meta: dict[str, Any] = {
        "telegram_user_id": user.telegram_id,
        "payment_purpose": PaymentKind.SUBSCRIPTION.value,
    }
    pay = await payments.create(
        user_id=user.id,
        document_id=None,
        amount=amount_rub,
        payment_kind=PaymentKind.SUBSCRIPTION,
        status=PaymentStatus.PENDING,
        payment_meta=db_meta,
        idempotency_key=idem,
    )

    yoo_meta = {
        "payment_db_id": str(pay.id),
        "telegram_user_id": str(user.telegram_id),
        "purpose": PaymentKind.SUBSCRIPTION.value,
    }

    client = YooKassaClient(settings)
    yoo = await client.create_payment_redirect(
        amount_value=f"{amount_rub}.00",
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
