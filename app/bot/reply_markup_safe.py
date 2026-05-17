"""Снятие reply-клавиатуры и одно итоговое сообщение с инлайн-кнопками — без дубля текста."""

from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message, ReplyKeyboardRemove

logger = logging.getLogger(__name__)

# WORD JOINER — короткое служебное сообщение, сразу удаляем (в ленте не остаётся второго такого же текста)
_STRIP_PLACEHOLDER = "\u2060"


async def answer_with_inline_after_strip_reply_keyboard(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
) -> Message:
    """
    Отдельно снимаем нижнюю клавиатуру, затем шлём ровно одно сообщение с нужным текстом и инлайном.

    Раньше использовался edit_text после ReplyKeyboardRemove; при сбое и неудачном delete
    уходило второе полное сообщение — пользователь видел один и тот же текст два раза.
    """
    strip = await message.answer(_STRIP_PLACEHOLDER, reply_markup=ReplyKeyboardRemove())
    try:
        await strip.delete()
    except TelegramBadRequest:
        logger.debug("Не удалось удалить служебное сообщение со снятием reply-клавиатуры", exc_info=True)

    kw: dict = {}
    if parse_mode is not None:
        kw["parse_mode"] = parse_mode
    if reply_markup is not None:
        kw["reply_markup"] = reply_markup
    return await message.answer(text, **kw)
