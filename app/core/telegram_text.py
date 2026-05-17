"""Helpers for Telegram message limits and safe text modes."""

from __future__ import annotations

import re

TELEGRAM_MAX_MESSAGE_LEN = 4096

# Хвосты «нейтральных» заголовков из этапа уточнений — не показываем пользователю в финальных экранах.
_TITLE_NEUTRAL_SUFFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s+[—\-]\s+уточняем\s.+$", re.IGNORECASE | re.UNICODE),
    re.compile(r"\s+[—\-]\s+нужны\s+уточнения\s*$", re.IGNORECASE | re.UNICODE),
)


def compact_document_title(title: str) -> str:
    """Убирает служебный хвост вида «— уточняем …» / «— нужны уточнения» для краткого отображения."""
    t = (title or "").strip()
    if not t:
        return t
    for rx in _TITLE_NEUTRAL_SUFFIX_PATTERNS:
        nt = rx.sub("", t).strip()
        if nt:
            t = nt
    return t


def chunk_text(text: str, max_len: int = TELEGRAM_MAX_MESSAGE_LEN - 64) -> list[str]:
    """Split plain text into chunks suitable for Telegram without breaking mid-word aggressively."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= max_len:
            chunks.append(rest)
            break
        split_at = rest.rfind("\n\n", 0, max_len)
        if split_at == -1:
            split_at = rest.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = rest.rfind(" ", 0, max_len)
        if split_at <= 0:
            split_at = max_len
        chunks.append(rest[:split_at].rstrip())
        rest = rest[split_at:].lstrip()
    return chunks
