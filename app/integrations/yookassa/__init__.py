"""
ЮKassa: REST API v3 по документации https://yookassa.ru/developers/api

- Создание платежей: POST /v3/payments (Basic Auth shopId:secretKey, заголовок Idempotence-Key).
- Уведомления (webhooks): см. https://yookassa.ru/developers/using-api/webhooks — ответить HTTP 200,
  дополнительно проверять IP отправителя и при необходимости статус платежа через GET /v3/payments/{id}.

В Telegram есть два типичных подхода:

1) **Ссылка на оплату (redirect)** — `YooKassaClient.create_payment_redirect` + `YOOKASSA_RETURN_URL` (HTTPS),
   пользователь оплачивает во внешнем браузере; подтверждение — через webhook и/или повторный GET платежа.

2) **Нативные платежи Bot API** (`send_invoice`, pre_checkout_query, successful_payment) — в личном кабинете/
   BotFather выдаёт провайдер-токен ЮKassa; в боте задаётся `TELEGRAM_PAYMENT_PROVIDER_TOKEN` (см. `Settings`).
   Это другой поток, не требующий `YOOKASSA_RETURN_URL`, но требующий обработчиков Bot API.
"""

from app.integrations.yookassa.client import YooKassaClient
from app.integrations.yookassa.webhook_security import yookassa_webhook_peer_allowed

__all__ = ["YooKassaClient", "yookassa_webhook_peer_allowed"]
