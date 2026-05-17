"""Минимальные схемы под ответ API ЮKassa (частично, см. объект Payment в справочнике API)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Money(BaseModel):
    value: str
    currency: str = "RUB"


class ConfirmationRedirect(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str = "redirect"
    confirmation_url: str | None = None


class YooPaymentObject(BaseModel):
    """Ответ создания платежа / объект в webhook (выбранные поля)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    status: str
    paid: bool = False
    amount: Money | None = None
    confirmation: ConfirmationRedirect | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
