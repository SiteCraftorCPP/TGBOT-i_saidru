from app.core.telegram_text import TELEGRAM_MAX_MESSAGE_LEN, chunk_text


def test_chunk_text_single() -> None:
    assert chunk_text("hello") == ["hello"]


def test_chunk_text_splits_long_run() -> None:
    s = "a" * 5000
    parts = chunk_text(s, max_len=1000)
    assert len(parts) >= 5
    assert "".join(parts) == s
    assert all(len(p) <= 1000 for p in parts)


def test_chunk_text_respects_telegram_ceiling() -> None:
    s = "x" * (TELEGRAM_MAX_MESSAGE_LEN * 2)
    parts = chunk_text(s)
    assert parts
    assert max(len(p) for p in parts) < TELEGRAM_MAX_MESSAGE_LEN
