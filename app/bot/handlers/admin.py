"""Админ-хендлеры: панель с оплатами и подписками."""

from __future__ import annotations

from datetime import timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.keyboards import ADMIN_BUYERS_PREFIX, admin_buyers_keyboard
from app.core.config import Settings
from app.db.repositories import PaidBuyerRow, PaymentRepository

PAGE_SIZE = 8


def _fmt_amount(amount: int, currency: str) -> str:
    cur = currency.strip().upper()
    if cur == "RUB":
        return f"{amount} RUB"
    return f"{amount} {currency.strip()}"


def _fmt_row_line(idx: int, r: PaidBuyerRow) -> str:
    ts = r.last_payment_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    when = ts.strftime("%d.%m.%Y %H:%M")
    uname = f"@{r.username.lstrip('@')}" if r.username else f"id:{r.telegram_id}"
    amt = _fmt_amount(r.amount, r.currency)
    return f"{idx}. {uname} · {amt} · {when}"


def _build_admin_message(
    *,
    rows: list[PaidBuyerRow],
    list_total: int,
    payments_doc: int,
    payments_sub: int,
    page: int,
    page_size: int,
) -> str:
    lines = [
        "Панель администратора 📊",
        "Ниже — число успешных транзакций (статус paid в базе):",
        f" • оплат за документы: {payments_doc}",
        f" • оплат подписки: {payments_sub}",
        "Дальше список людей у которых хотя бы один платёж paid;",
        "для каждого показывают последнюю успешную оплату.",
    ]
    if list_total > page_size:
        total_pages = max(1, (list_total + page_size - 1) // page_size)
        shown_from = page * page_size + 1 if list_total else 0
        shown_to = page * page_size + len(rows)
        lines.append(f"Стр. {page + 1}/{total_pages} ({shown_from}–{shown_to} из {list_total})")

    header = "\n".join(lines)

    if not rows:
        return header

    base = page * page_size
    body = [_fmt_row_line(base + i + 1, r) for i, r in enumerate(rows)]
    return f"{header}\n\n" + "\n".join(body)


router = Router()


@router.message(Command("admin"))
async def cmd_admin(
    message: Message,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    if not settings.is_admin(message.from_user.id):
        await message.answer("Нет доступа. 🚫")
        return

    async with session_factory() as session:
        pays = PaymentRepository(session)
        doc_n, sub_n = await pays.count_completed_by_kind()
        rows, total = await pays.list_buyers_who_paid(offset=0, limit=PAGE_SIZE)

    text = _build_admin_message(
        rows=rows,
        list_total=total,
        payments_doc=doc_n,
        payments_sub=sub_n,
        page=0,
        page_size=PAGE_SIZE,
    )
    kb = admin_buyers_keyboard(page=0, total=total, page_size=PAGE_SIZE)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith(ADMIN_BUYERS_PREFIX))
async def admin_buyers_page(
    callback: CallbackQuery,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    if not settings.is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    raw = callback.data or ""
    try:
        page_req = int(raw.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer()
        return

    page_req = max(0, page_req)

    async with session_factory() as session:
        pays = PaymentRepository(session)
        doc_n, sub_n = await pays.count_completed_by_kind()
        rows, total = await pays.list_buyers_who_paid(offset=page_req * PAGE_SIZE, limit=PAGE_SIZE)

    max_page = max(0, (total - 1) // PAGE_SIZE) if total else 0
    if total and page_req > max_page:
        page_req = max_page
        async with session_factory() as session:
            pays = PaymentRepository(session)
            doc_n, sub_n = await pays.count_completed_by_kind()
            rows, total = await pays.list_buyers_who_paid(offset=page_req * PAGE_SIZE, limit=PAGE_SIZE)

    text = _build_admin_message(
        rows=rows,
        list_total=total,
        payments_doc=doc_n,
        payments_sub=sub_n,
        page=page_req,
        page_size=PAGE_SIZE,
    )
    kb = admin_buyers_keyboard(page=page_req, total=total, page_size=PAGE_SIZE)

    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)
    await callback.answer()
