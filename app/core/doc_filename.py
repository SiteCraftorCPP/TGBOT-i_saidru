from __future__ import annotations

import hashlib
import re
from datetime import datetime


def build_dynamic_docx_filename(*, title: str, request_text: str, details_text: str) -> str:
    """Имя файла без порядкового id: тема + дата + короткий хэш от текста пользователя."""
    raw = (title or "").strip() or "документ"
    safe = re.sub(r"[^\w\s\-—––А-Яа-яЁё0-9]+", "", raw, flags=re.UNICODE)
    safe = re.sub(r"\s+", "_", safe).strip("._-")
    if not safe:
        safe = "документ"
    if len(safe) > 50:
        safe = safe[:50].rstrip("._-")
    digest = hashlib.sha256(
        f"{request_text}\n{details_text}".encode("utf-8"),
    ).hexdigest()[:8]
    day = datetime.now().strftime("%Y%m%d")
    return f"{safe}_{day}_{digest}.docx"
