from app.integrations.yookassa.webhook_security import yookassa_webhook_peer_allowed


def test_yookassa_webhook_ip_allowlist_from_docs() -> None:
    assert yookassa_webhook_peer_allowed("185.71.76.1") is True
    assert yookassa_webhook_peer_allowed("77.75.156.11") is True
    assert yookassa_webhook_peer_allowed("2a02:5180::1") is True
    assert yookassa_webhook_peer_allowed("8.8.8.8") is False
    assert yookassa_webhook_peer_allowed("not-an-ip") is False
