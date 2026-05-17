"""Проверка отправителей HTTP-уведомлений ЮKassa по списку сетей из документации."""

from __future__ import annotations

import ipaddress


# Сети из раздела «Notification authentication» (IP authentication):
# https://yookassa.ru/developers/using-api/webhooks
_YOOKASSA_WEBHOOK_CIDRS = tuple(
    ipaddress.ip_network(s, strict=False)
    for s in (
        "185.71.76.0/27",
        "185.71.77.0/27",
        "77.75.154.128/25",
        "77.75.156.11/32",
        "77.75.156.35/32",
        "77.75.153.0/25",
        "2a02:5180::/32",
    )
)


def yookassa_webhook_peer_allowed(remote_ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(remote_ip.split("%")[0].strip())
    except ValueError:
        return False
    return any(addr in net for net in _YOOKASSA_WEBHOOK_CIDRS)
