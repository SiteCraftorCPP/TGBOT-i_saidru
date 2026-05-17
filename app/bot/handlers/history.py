from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.keyboards import (
    document_display_title,
    history_empty_keyboard,
    history_keyboard,
    history_list_keyboard,
)
from app.bot.reply_markup_safe import answer_with_inline_after_strip_reply_keyboard
from app.core.constants import MAIN_MENU_HISTORY
from app.db.repositories import DocumentRepository, UserRepository

router = Router()

ITEMS_PER_PAGE = 5

LIST_HEADER = "Ваши документы: 📁\nВыберите документ для скачивания:"


@router.message(Command("history"))
@router.message(F.text == MAIN_MENU_HISTORY)
async def history(message: Message, session_factory: async_sessionmaker) -> None:
    uid = message.from_user.id
    uname = message.from_user.username
    async with session_factory() as session:
        user = await UserRepository(session).get_or_create(
            telegram_id=uid,
            username=uname,
        )
        documents = await DocumentRepository(session).list_for_user(user.id)

    if not documents:
        await answer_with_inline_after_strip_reply_keyboard(
            message,
            "История пока пустая. 📭",
            reply_markup=history_empty_keyboard(),
        )
        return

    total_pages = (len(documents) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page = 1
    start_idx = 0
    end_idx = ITEMS_PER_PAGE
    page_docs = documents[start_idx:end_idx]
    markup = history_list_keyboard(page_docs, page, total_pages)
    await answer_with_inline_after_strip_reply_keyboard(
        message,
        LIST_HEADER,
        reply_markup=markup,
    )

@router.callback_query(F.data == "menu_history")
async def history_callback(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    await _show_history_page(callback.message, session_factory, page=1, user_id=callback.from_user.id, username=callback.from_user.username, is_callback=True)
    await callback.answer()

@router.callback_query(F.data.startswith("history_page:"))
async def history_page_callback(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    page = int(callback.data.split(":")[1])
    await _show_history_page(callback.message, session_factory, page=page, user_id=callback.from_user.id, username=callback.from_user.username, is_callback=True)
    await callback.answer()

async def _show_history_page(message: Message, session_factory: async_sessionmaker, page: int, user_id: int | None = None, username: str | None = None, is_callback: bool = False) -> None:
    uid = user_id or message.from_user.id
    uname = username or message.from_user.username
    async with session_factory() as session:
        user = await UserRepository(session).get_or_create(
            telegram_id=uid,
            username=uname,
        )
        documents = await DocumentRepository(session).list_for_user(user.id)

    if not documents:
        text = "История пока пустая. 📭"
        if is_callback:
            await message.edit_text(text, reply_markup=history_empty_keyboard())
        else:
            await message.answer(text, reply_markup=history_empty_keyboard())
        return

    total_pages = (len(documents) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page = max(1, min(page, total_pages))
    
    start_idx = (page - 1) * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_docs = documents[start_idx:end_idx]

    text = LIST_HEADER
    markup = history_list_keyboard(page_docs, page, total_pages)
    
    if is_callback:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)

@router.callback_query(F.data.startswith("history_doc:"))
async def history_doc_callback(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    raw = callback.data.removeprefix("history_doc:")
    parts = raw.split(":")
    doc_id = int(parts[0])
    list_page = int(parts[1]) if len(parts) > 1 else 1
    async with session_factory() as session:
        document = await DocumentRepository(session).get(doc_id)
        
    if not document:
        await callback.message.edit_text(
            "Документ не найден. ❌",
            reply_markup=history_empty_keyboard(),
        )
        await callback.answer()
        return

    has_docx = bool(document.docx_path and Path(document.docx_path).exists())
    has_pdf = bool(document.pdf_path and Path(document.pdf_path).exists())
    
    title = document_display_title(document)
    text = f"📄 {title}\nСтатус: {document.status}"
    
    await callback.message.edit_text(
        text,
        reply_markup=history_keyboard(document.id, has_docx, has_pdf, list_page=list_page),
    )
    await callback.answer()

@router.callback_query(F.data.startswith("download_docx:"))
async def download_docx(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    await _send_document(callback, session_factory, file_kind="docx")

@router.callback_query(F.data.startswith("download_pdf:"))
async def download_pdf(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    await _send_document(callback, session_factory, file_kind="pdf")

async def _send_document(
    callback: CallbackQuery,
    session_factory: async_sessionmaker,
    *,
    file_kind: str,
) -> None:
    document_id = int(callback.data.split(":", 1)[1])
    async with session_factory() as session:
        document = await DocumentRepository(session).get(document_id)

    if not document:
        await callback.message.answer("Файл не найден. ❌")
        await callback.answer()
        return
    path_value = document.docx_path if file_kind == "docx" else document.pdf_path
    if not path_value or not Path(path_value).exists():
        await callback.message.answer("Файл не найден. ❌")
        await callback.answer()
        return

    await callback.message.answer_document(
        FSInputFile(path_value, filename=Path(path_value).name),
        caption=file_kind.upper(),
    )
    await callback.answer()
