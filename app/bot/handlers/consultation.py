import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.keyboards import consultation_actions
from app.bot.states import ConsultationStates
from app.core.config import Settings
from app.core.telegram_text import chunk_text
from app.db.repositories import ConsultationRepository, UserRepository
from app.services.catalog import TemplateCatalog
from app.services.deepseek import DeepSeekClient, DeepSeekError

logger = logging.getLogger(__name__)

router = Router()


@router.message(StateFilter(ConsultationStates.waiting_problem), F.text)
async def handle_problem(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker,
    deepseek: DeepSeekClient,
    catalog: TemplateCatalog,
    settings: Settings,
) -> None:
    problem_text = message.text.strip()
    if len(problem_text) < 10:
        await message.answer("Опишите ситуацию чуть подробнее: что случилось, с кем, чего хотите добиться.")
        return

    status = await message.answer("Разбираю ситуацию... ⏳")
    try:
        result = await deepseek.consult(problem_text, catalog.all(), telegram_id=message.from_user.id)
    except DeepSeekError as exc:
        await status.edit_text(f"Не смог получить консультацию: {exc}", parse_mode=None)
        return

    template = catalog.find(result.document_type) or catalog.match_by_title(result.recommended_document)
    document_type = template.document_type if template and result.document_required else None
    recommended_document = template.title if template else result.recommended_document

    async with session_factory() as session:
        user = await UserRepository(session).get_or_create(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
        )
        consultation = await ConsultationRepository(session).create(
            user_id=user.id,
            problem_text=problem_text,
            category=result.category,
            consultation_text=result.consultation,
            risks=result.risks,
            next_steps=result.next_steps,
            recommended_document=recommended_document,
            document_type=document_type,
            raw_ai_json=result.model_dump(),
        )
        await session.commit()

    await state.clear()

    price_doc = f"{settings.document_price_rub} ₽"
    price_sub = f"{settings.subscription_price_rub} ₽"

    risks_clean = (result.risks or "").strip()
    text = (
        f"Категория: {result.category} 📁\n\n"
        f"Консультация: 💡\n{result.consultation}\n\n"
    )
    if risks_clean:
        text += f"Риски: ⚠️\n{risks_clean}\n\n"
    text += f"Что делать дальше: 👣\n{result.next_steps}"

    if document_type and recommended_document:
        text += (
            "\n\nВ вашей ситуации рекомендуется подготовить документ:\n"
            f"«{recommended_document}»"
        )
    elif recommended_document:
        text += f"\n\nМожно оформить документ: «{recommended_document}»"
    elif result.document_required:
        text += "\n\nПо ситуации можно подготовить документ — оформите через кнопку ниже."

    try:
        await status.delete()
    except Exception:
        logger.exception("Не удалось удалить статусное сообщение консультации")

    for chunk in chunk_text(text):
        await message.answer(chunk, parse_mode=None)

    await message.answer(
        f"Документ — {price_doc} за штуку. Подписка на месяц — {price_sub} "
        "(безлимитная генерация документов). Выберите подходящий вариант:",
        reply_markup=consultation_actions(
            consultation.id,
            document_price_label=price_doc,
            subscription_price_label=price_sub,
        ),
        parse_mode=None,
    )
