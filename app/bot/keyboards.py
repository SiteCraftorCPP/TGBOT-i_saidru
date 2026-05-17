import re
from pathlib import Path

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.constants import (
    MAIN_MENU_CONSULTATION,
    MAIN_MENU_DOCUMENT,
    MAIN_MENU_HISTORY,
    MAIN_MENU_SUBSCRIPTION,
)
from app.core.telegram_text import compact_document_title
from app.services.catalog import TemplateCatalog
MENU_MAIN = "menu_main"
MENU_IGNORE = "ignore"
ADMIN_BUYERS_PREFIX = "admin_buyers:"
PAY_DONE_PREFIX = "pay_done:"

# В главное меню (текст + эмодзи, не «один только смайлик»)
BTN_MAIN_MENU = "🏠 Главное меню"
# Один шаг «назад» к приветствию/меню (как просили: 🔙 + слово)
BTN_BACK_MENU = "🔙 Назад"


def kb_main_menu_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text=BTN_MAIN_MENU, callback_data=MENU_MAIN)


def kb_back_to_menu_button() -> InlineKeyboardButton:
    """То же действие, что главное меню, но подпись «назад» (экран ввода запроса документа)."""
    return InlineKeyboardButton(text=BTN_BACK_MENU, callback_data=MENU_MAIN)


def kb_back_main_row() -> list[InlineKeyboardButton]:
    return [kb_main_menu_button()]


def nav_blank_button(text: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=MENU_IGNORE)


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=MAIN_MENU_DOCUMENT, callback_data="menu_document"),
                InlineKeyboardButton(text=MAIN_MENU_CONSULTATION, callback_data="menu_consultation")
            ],
            [
                InlineKeyboardButton(text=MAIN_MENU_HISTORY, callback_data="menu_history"),
            ],
            [
                InlineKeyboardButton(text=MAIN_MENU_SUBSCRIPTION, callback_data="subscribe_month"),
            ],
        ]
    )


def consultation_actions(
    consultation_id: int,
    *,
    document_price_label: str,
    subscription_price_label: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"📄 Документ — {document_price_label}",
                    callback_data=f"doc_from_consult:{consultation_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"⭐ Подписка {subscription_price_label} / мес",
                    callback_data="subscribe_month",
                )
            ],
            [InlineKeyboardButton(text="💬 Новая консультация", callback_data="new_consultation")],
            kb_back_main_row(),
        ]
    )


def subscription_offer_keyboard(subscription_price_label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💳 Оплатить подписку — {subscription_price_label}",
                    callback_data="subscribe_pay_month",
                )
            ],
            kb_back_main_row(),
        ]
    )


def categories_keyboard(catalog: TemplateCatalog) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"📁 {category}", callback_data=f"category:{category}")]
            for category in catalog.categories()
        ]
    )


def documents_keyboard(catalog: TemplateCatalog, category: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📝 {template.title}", callback_data=f"direct_doc:{template.document_type}")]
        for template in catalog.by_category(category)
    ]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_categories")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_generation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Сформировать документ", callback_data="generate_document")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="document_back_details")],
        ]
    )


def document_flow_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[kb_back_to_menu_button()]])


def document_questions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔙 Назад", callback_data="document_back_request"),
            ],
            kb_back_main_row(),
        ]
    )


def history_keyboard(document_id: int, has_docx: bool, has_pdf: bool, *, list_page: int) -> InlineKeyboardMarkup:
    row = []
    if has_docx:
        row.append(InlineKeyboardButton(text="📥 DOCX", callback_data=f"download_docx:{document_id}"))
    if has_pdf:
        row.append(InlineKeyboardButton(text="📥 PDF", callback_data=f"download_pdf:{document_id}"))
    
    rows = [row] if row else []
    rows.append(
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"history_page:{list_page}")],
    )
    rows.append(kb_back_main_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def document_display_title(doc: object) -> str:
    answers = getattr(doc, "answers_json", None) or {}
    title = (answers.get("title") or answers.get("document_title") or "").strip()
    if title:
        return compact_document_title(title)

    for path_attr in ("docx_path", "pdf_path"):
        path_value = getattr(doc, path_attr, None)
        if not path_value:
            continue
        stem = Path(path_value).stem
        stem = re.sub(r"_\d{8}_[0-9a-f]{8}$", "", stem, flags=re.IGNORECASE)
        title = stem.replace("_", " ").strip()
        if title:
            return title

    document_text = (getattr(doc, "document_text", None) or "").strip()
    if document_text:
        for line in document_text.splitlines():
            line = line.strip()
            if line:
                return line[:60]

    document_type = getattr(doc, "document_type", None)
    if document_type and document_type != "dynamic":
        return str(document_type).replace("_", " ").strip()

    return "Документ"


def history_list_keyboard(documents: list, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows = []
    for doc in documents:
        title = document_display_title(doc)
        label = f"📄 {title}"[:60]
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"history_doc:{doc.id}:{page}",
                )
            ],
        )
    
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"history_page:{page - 1}"))
    if total_pages > 1:
        nav_row.append(nav_blank_button(f"{page}/{total_pages}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"history_page:{page + 1}"))
    
    if nav_row:
        rows.append(nav_row)
    rows.append(kb_back_main_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def history_empty_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[kb_back_main_row()])


def admin_buyers_keyboard(*, page: int, total: int, page_size: int) -> InlineKeyboardMarkup | None:
    if total <= page_size:
        return None
    total_pages = max(1, (total + page_size - 1) // page_size)
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"{ADMIN_BUYERS_PREFIX}{page - 1}"),
        )
    nav.append(nav_blank_button(f"{page + 1}/{total_pages}"))
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(text="➡️", callback_data=f"{ADMIN_BUYERS_PREFIX}{page + 1}"),
        )
    return InlineKeyboardMarkup(inline_keyboard=[nav])


def yookassa_checkout_keyboard(pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💳 Оплатить в ЮKassa", url=pay_url),
            ],
            kb_back_main_row(),
        ],
    )


def pay_done_continue_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Продолжить ➡️", callback_data=f"{PAY_DONE_PREFIX}{payment_id}"),
            ],
            kb_back_main_row(),
        ],
    )
