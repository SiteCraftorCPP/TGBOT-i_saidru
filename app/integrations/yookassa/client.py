"""HTTP-клиент к api.yookassa.ru/v3 (async, через httpx)."""

from __future__ import annotations

import base64
import uuid
from typing import Any

import httpx

from app.core.config import Settings
from app.integrations.yookassa.schemas import YooPaymentObject


class YooKassaApiError(RuntimeError):
    pass


class YooKassaClient:
    """
    Авторизация: HTTP Basic, строка `shopId:secretKey` в Base64 —
    см. «Формат взаимодействия» в документации ЮKassa.
    """

    _BASE_URL = "https://api.yookassa.ru/v3"

    def __init__(self, settings: Settings, *, http_timeout: float = 60.0) -> None:
        if not settings.yookassa_configured():
            raise YooKassaApiError("YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY должны быть заданы.")
        raw = f"{settings.yookassa_shop_id}:{settings.yookassa_secret_key}".encode("utf-8")
        token = base64.b64encode(raw).decode("ascii")
        self._headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }
        self._settings = settings
        self._timeout = http_timeout

    def _need_return_url(self) -> str:
        u = self._settings.yookassa_return_url.strip()
        if not u:
            raise YooKassaApiError(
                "YOOKASSA_RETURN_URL не задан. Для confirmation.type=redirect в API нужен HTTPS return_url "
                "(см. объект confirmation в создании платежа)."
            )
        return u

    async def create_payment_redirect(
        self,
        *,
        amount_value: str,
        description: str,
        metadata: dict[str, Any],
        idempotence_key: str | None = None,
    ) -> YooPaymentObject:
        """POST /payments с confirmation.type=redirect (официальный сценарий из справочника API)."""
        key = idempotence_key or str(uuid.uuid4())
        body: dict[str, Any] = {
            "amount": {"value": amount_value, "currency": "RUB"},
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": self._need_return_url(),
            },
            "description": description[:255],
            "metadata": metadata or {},
        }
        headers = {**self._headers, "Idempotence-Key": key}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(f"{self._BASE_URL}/payments", headers=headers, json=body)
        if response.status_code >= 400:
            raise YooKassaApiError(f"YooKassa HTTP {response.status_code}: {response.text[:500]}")
        data = response.json()
        return YooPaymentObject.model_validate(data)

    async def get_payment(self, *, payment_id: str) -> YooPaymentObject:
        """GET /payments/{payment_id} — верификация статуса после webhook (рекомендация документации)."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                f"{self._BASE_URL}/payments/{payment_id}",
                headers=self._headers,
            )
        if response.status_code >= 400:
            raise YooKassaApiError(f"YooKassa HTTP {response.status_code}: {response.text[:500]}")
        return YooPaymentObject.model_validate(response.json())
