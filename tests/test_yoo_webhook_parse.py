from app.http.yookassa_webhook import _parse_payment_notification


def test_parse_payment_succeeded_event() -> None:
    oid, fb, fn = _parse_payment_notification(
        {"event": "payment.succeeded", "object": {"id": "2d7eaa8c-d7c9-4973-8316-927a", "paid": False}}
    )
    assert fn is True
    assert oid == "2d7eaa8c-d7c9-4973-8316-927a"
    assert fb is None


def test_parse_metadata_fallback_payment_db_id() -> None:
    oid, fb, fn = _parse_payment_notification(
        {
            "event": "payment.succeeded",
            "object": {
                "id": "abc",
                "metadata": {"payment_db_id": "42"},
            },
        }
    )
    assert fn is True
    assert oid == "abc"
    assert fb == 42


def test_waiting_for_capture_ignored_when_not_success() -> None:
    oid, fb, fn = _parse_payment_notification(
        {
            "event": "payment.waiting_for_capture",
            "object": {"id": "x", "status": "waiting_for_capture", "paid": False},
        }
    )
    assert fn is False

