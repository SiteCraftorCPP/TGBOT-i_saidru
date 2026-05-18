import logging
from html import escape
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.handlers.telegram_payments import telegram_document_invoice_kw
from app.bot.keyboards import (
    PAY_DONE_PREFIX,
    confirm_generation_keyboard,
    document_flow_start_keyboard,
    document_questions_keyboard,
    main_menu,
    yookassa_checkout_keyboard,
)
from app.bot.reply_markup_safe import answer_with_inline_after_strip_reply_keyboard
from app.bot.states import DocumentStates
from app.core.config import Settings
from app.core.constants import MAIN_MENU_DOCUMENT
from app.core.telegram_text import chunk_text, compact_document_title
from app.db.models import DocumentStatus, PaymentKind, PaymentStatus, User
from app.db.repositories import (
    ConsultationRepository,
    DocumentRepository,
    PaymentRepository,
    UserRepository,
)
from app.integrations.yookassa.client import YooKassaApiError
from app.services.telegram_invoice_finalize import TelegramInvoiceAmountTooLowError
from app.services.deepseek import DeepSeekClient, DeepSeekError
from app.services.documents import DocumentGenerator
from app.services.payments import (
    PAYMENTS_CONFIGURE_HELP_TEXT,
    PAYMENTS_DISABLED_ADMIN_DIAGNOSTIC,
    PAYMENTS_TURNED_OFF_USER_MESSAGE,
    PaymentService,
)
from app.schemas.ai import DocumentQuestionsResult

router = Router()
logger = logging.getLogger(__name__)

DOCUMENT_PROMPT = (
    "Какой документ вам нужен? Опишите своими словами: "
    "<b>кто участвует</b>, <b>что нужно закрепить</b> — кратко или подробно.\n\n"
    "<b>Важно:</b> после анализа бот задаёт уточнения <b>не списком</b>, а <b>по одному вопросу за раз</b> "
    "(следующий — только после ответа на текущий). Так собирается база для черновика. "
    "Когда цепочка пройдена, проверяется полнота сведений; только после этого — оформление и генерация. 📝"
)

_MAX_READINESS_GATE_ROUNDS = 10


def _build_document_collecting_intro(result: DocumentQuestionsResult, title_compact: str) -> str:
    summary = (result.extracted_facts_summary or "").strip()
    summary_block = ""
    if summary:
        summary_block = f"<b>Что уже понятно из вашего сообщения:</b>\n{escape(summary)}\n\n"
    title_e = escape(title_compact)
    if result.clarification_needed:
        head = "<b>В запросе пока не хватает конкретики</b> — задаю уточняющие вопросы по одному.\n\n"
    else:
        head = ""
    return (
        "✅ <b>Запрос разобран.</b>\n\n"
        f"{head}{summary_block}"
        f"<b>Будем готовить:</b> {title_e} 📄\n\n"
        "Бот ведёт опрос <b>последовательно</b>: один вопрос за раз, ответ — одним сообщением. "
        "После ответов проверяется полнота сведений; при необходимости добавляются уточнения — тоже по одному. "
        "К оформлению и генерации переходим, когда данных достаточно.\n\n"
    )


def _format_qa_step_html(current: int, total: int, question: str) -> str:
    return (
        f"<b>Вопрос {current} из {total}</b> "
        "<i>(ответьте сейчас только на него; следующий придёт после вашего ответа)</i>\n\n"
        f"{escape(question.strip())}\n\n"
        "Один ответ — <b>одним сообщением</b>. Если чего-то не знаете — так и напишите."
    )


async def _offer_document_checkout_after_clarifications(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker,
    payment_service: PaymentService,
    settings: Settings,
    *,
    details_text: str,
    request_text: str,
    document_title: str,
) -> None:
    """Создаёт запись документа, при необходимости выставляет оплату, затем подтверждение генерации."""
    async with session_factory() as session:
        user = await UserRepository(session).get_or_create(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
        )
        documents = DocumentRepository(session)
        document = await documents.create(user_id=user.id, document_type="dynamic")
        try:
            paid, pay_url, telegram_invoice_pay_id = await payment_service.prepare_document_access(
                document=document,
                documents=documents,
                payments=PaymentRepository(session),
                user=user,
                flow_context={
                    "request_text": request_text,
                    "details_text": details_text,
                    "document_title": str(document_title),
                },
            )
        except TelegramInvoiceAmountTooLowError as exc:
            await session.rollback()
            await message.answer(str(exc), parse_mode=None)
            return
        except YooKassaApiError as exc:
            await session.commit()
            logger.warning("ЮKassa create_payment_redirect: %s", exc)
            await message.answer(
                f"Не удалось создать платёж в ЮKassa. Попробуйте позже.\nПричина: {exc}",
                parse_mode=None,
            )
            return

        await session.commit()

    if not paid:
        if telegram_invoice_pay_id is not None:
            await message.answer_invoice(
                **telegram_document_invoice_kw(
                    payment_row_id=telegram_invoice_pay_id,
                    price_rub=settings.document_price_rub,
                    provider_token=settings.telegram_payment_provider_token,
                    settings=settings,
                ),
            )
        elif pay_url:
            await message.answer(
                f"Чтобы продолжить, оплатите {settings.document_price_rub} ₽.\n"
                "После успешной оплаты вы получите сообщение — нажмите «Продолжить» там.",
                parse_mode=None,
                reply_markup=yookassa_checkout_keyboard(pay_url),
            )
        else:
            if not settings.payments_enabled:
                if settings.is_admin(message.from_user.id):
                    await message.answer(PAYMENTS_DISABLED_ADMIN_DIAGNOSTIC, parse_mode=None)
                else:
                    await message.answer(PAYMENTS_TURNED_OFF_USER_MESSAGE, parse_mode=None)
            else:
                await message.answer(PAYMENTS_CONFIGURE_HELP_TEXT, parse_mode=None)
        return

    await state.set_state(DocumentStates.confirming_generation)
    await state.update_data(
        document_id=document.id,
        details_text=details_text,
        request_text=request_text,
        document_title=compact_document_title(str(document_title).strip()),
    )

    await message.answer(
        f"Сведений достаточно для черновика. Сформировать документ <b>{escape(str(document_title))}</b>? 📋",
        reply_markup=confirm_generation_keyboard(),
        parse_mode="HTML",
    )


async def _run_readiness_gate_and_checkout(
    message: Message,
    state: FSMContext,
    deepseek: DeepSeekClient,
    session_factory: async_sessionmaker,
    payment_service: PaymentService,
    settings: Settings,
) -> None:
    """После завершения списка вопросов оценивает полноту; при готовности запускает оплату/подтверждение."""
    data = await state.get_data()
    request_text = (data.get("request_text") or "").strip()
    transcript = (data.get("qa_transcript") or "").strip()
    document_title = data.get("document_title", "Документ")
    gate_rounds = int(data.get("qa_gate_rounds", 0))
    questions = list(data.get("questions_queue") or [])

    progress = await message.answer("Проверяю, достаточно ли данных для черновика документа... ⏳", parse_mode=None)
    try:
        assessment = await deepseek.assess_document_readiness(request_text, transcript)
    except DeepSeekError as exc:
        await progress.edit_text(f"Не удалось проверить полноту данных: {exc} ⚠️", parse_mode=None)
        return

    if assessment.ready:
        await progress.delete()
        await _offer_document_checkout_after_clarifications(
            message,
            state,
            session_factory,
            payment_service,
            settings,
            details_text=transcript,
            request_text=request_text,
            document_title=str(document_title),
        )
        return

    await progress.delete()

    if not assessment.ready and data.get("qa_sent_finale"):
        note = (
            "\n\nПримечание для генерации (по ответам данных всё ещё может не хватать): "
            + (assessment.reason_short or "").strip()
        )
        await _offer_document_checkout_after_clarifications(
            message,
            state,
            session_factory,
            payment_service,
            settings,
            details_text=(transcript + note).strip(),
            request_text=request_text,
            document_title=str(document_title),
        )
        return

    extra = list(assessment.follow_up_questions)[:4]
    if not assessment.ready and gate_rounds >= _MAX_READINESS_GATE_ROUNDS and not data.get("qa_sent_finale"):
        await message.answer(
            "Дальше — <b>одно финальное сообщение</b>: соберите в нём всё критичное для документа.",
            parse_mode="HTML",
            reply_markup=document_questions_keyboard(),
        )
        tail_q = (
            "Финальное уточнение одним сообщением: стороны, регион или город, предмет документа, суммы, сроки, "
            "реквизиты (что применимо)."
        )
        questions.append(tail_q)
        new_idx = len(questions) - 1
        await state.update_data(
            questions_queue=questions,
            qa_index=new_idx,
            qa_sent_finale=True,
            qa_gate_rounds=gate_rounds + 1,
        )
        reason = (assessment.reason_short or "").strip()
        reason_html = f"<i>{escape(reason)}</i>\n\n" if reason else ""
        await message.answer(
            f"{reason_html}{_format_qa_step_html(new_idx + 1, len(questions), questions[new_idx])}",
            parse_mode="HTML",
            reply_markup=document_questions_keyboard(),
        )
        return

    if not extra:
        extra = [
            "Кратко одним сообщением перечислите недостающие сведения (что ещё важно для этого документа).",
        ]

    old_len = len(questions)
    questions.extend(extra)
    new_idx = old_len
    await state.update_data(
        questions_queue=questions,
        qa_index=new_idx,
        qa_gate_rounds=gate_rounds + 1,
    )

    reason = (assessment.reason_short or "").strip()
    reason_html = f"<b>Нужно уточнить.</b> {escape(reason)}\n\n" if reason else "<b>Нужно уточнить.</b>\n\n"

    await message.answer(
        f"{reason_html}{_format_qa_step_html(new_idx + 1, len(questions), questions[new_idx])}",
        parse_mode="HTML",
        reply_markup=document_questions_keyboard(),
    )


async def _process_document_request(
    message: Message,
    state: FSMContext,
    deepseek: DeepSeekClient,
    request_text: str,
) -> None:
    status = await message.answer("Анализирую ваш запрос... ⏳", parse_mode=None)

    try:
        result = await deepseek.generate_document_questions(request_text)
    except DeepSeekError as exc:
        await status.edit_text(f"Не удалось обработать запрос: {exc} ⚠️", parse_mode=None)
        return
    except Exception:
        logger.exception("Ошибка анализа запроса документа")
        await status.edit_text("Внутренняя ошибка. Попробуйте позже. ❌", parse_mode=None)
        return

    title_compact = compact_document_title(result.document_title.strip())
    intro = _build_document_collecting_intro(result, title_compact)
    qs = [q.strip() for q in result.questions if isinstance(q, str) and q.strip()]
    if not qs:
        qs = [
            "Какой именно документ нужен и в каком населённом пункте или регионе он будет использоваться "
            "(если это важно для текста)?",
        ]
    total = len(qs)
    first_step = _format_qa_step_html(1, total, qs[0])

    await state.set_state(DocumentStates.collecting_document_qa)
    await state.update_data(
        request_text=request_text,
        document_title=title_compact,
        questions_prompt_text=intro,
        questions_queue=qs,
        qa_index=0,
        qa_transcript="",
        qa_gate_rounds=0,
        qa_amend_mode=False,
    )

    await status.edit_text(intro, parse_mode="HTML")
    await message.answer(
        first_step,
        parse_mode="HTML",
        reply_markup=document_questions_keyboard(),
    )


@router.callback_query(F.data == "menu_document")
@router.message(F.text == MAIN_MENU_DOCUMENT)
async def document_menu_callback(event: CallbackQuery | Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(DocumentStates.waiting_document_request)
    message = event.message if isinstance(event, CallbackQuery) else event
    
    if isinstance(event, Message):
        await answer_with_inline_after_strip_reply_keyboard(
            message,
            DOCUMENT_PROMPT,
            reply_markup=document_flow_start_keyboard(),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            DOCUMENT_PROMPT,
            reply_markup=document_flow_start_keyboard(),
            parse_mode="HTML",
        )
        await event.answer()


@router.message(StateFilter(DocumentStates.waiting_document_request), F.text)
async def handle_document_request(
    message: Message,
    state: FSMContext,
    deepseek: DeepSeekClient,
) -> None:
    await _process_document_request(message, state, deepseek, message.text.strip())


@router.message(StateFilter(DocumentStates.collecting_document_qa), F.text)
async def handle_collecting_document_qa(
    message: Message,
    state: FSMContext,
    deepseek: DeepSeekClient,
    session_factory: async_sessionmaker,
    payment_service: PaymentService,
    settings: Settings,
) -> None:
    data = await state.get_data()
    reply = message.text.strip()
    questions = list(data.get("questions_queue") or [])

    if data.get("qa_amend_mode"):
        await state.update_data(qa_amend_mode=False)
        transcript_prev = (data.get("qa_transcript") or "").strip()
        transcript = transcript_prev + "\n\nДополнение (редактирование):\n" + reply + "\n"
        await state.update_data(qa_transcript=transcript.strip())
        await _run_readiness_gate_and_checkout(
            message,
            state,
            deepseek,
            session_factory,
            payment_service,
            settings,
        )
        return

    idx = int(data.get("qa_index", 0))
    if idx >= len(questions):
        await message.answer("Сессия оформления сбилась. Начните документ заново из меню. 🔄", parse_mode=None)
        return

    current_q = questions[idx]
    block = f"Вопрос: {current_q}\nОтвет: {reply}\n"
    transcript = ((data.get("qa_transcript") or "").strip() + "\n\n" + block).strip()
    idx += 1
    await state.update_data(qa_transcript=transcript, qa_index=idx)

    if idx < len(questions):
        step = _format_qa_step_html(idx + 1, len(questions), questions[idx])
        await message.answer(step, parse_mode="HTML", reply_markup=document_questions_keyboard())
        return

    await _run_readiness_gate_and_checkout(
        message,
        state,
        deepseek,
        session_factory,
        payment_service,
        settings,
    )


@router.callback_query(F.data.startswith("doc_from_consult:"))
async def document_from_consultation(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker,
    deepseek: DeepSeekClient,
) -> None:
    consultation_id = int(callback.data.split(":", 1)[1])
    async with session_factory() as session:
        consultation = await ConsultationRepository(session).get(consultation_id)
        if not consultation:
            await callback.message.answer("Консультация не найдена. ❌")
            await callback.answer()
            return

    title_hint = (
        consultation.recommended_document
        or consultation.document_type
        or "документ по ситуации пользователя"
    )
    request_text = (
        f"Сделай документ: {title_hint}.\n"
        f"Исходная проблема: {consultation.problem_text}\n"
        f"Контекст консультации: {consultation.consultation_text[:2000]}"
    )

    await state.set_state(DocumentStates.waiting_document_request)
    await _process_document_request(
        callback.message,
        state,
        deepseek,
        request_text,
    )
    await callback.answer()


@router.message(StateFilter(DocumentStates.waiting_document_details), F.text)
async def handle_document_details(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker,
    payment_service: PaymentService,
    settings: Settings,
) -> None:
    details_text = message.text.strip()
    data = await state.get_data()
    document_title = data.get("document_title", "Документ")
    request_text = data.get("request_text", "")

    existing_raw = data.get("document_id")
    if existing_raw:
        document_id = int(existing_raw)
        async with session_factory() as session:
            document = await DocumentRepository(session).get(document_id)
        if not document:
            await message.answer("Сессия устарела. Начните оформление заново. 🔄")
            await state.clear()
            return
        await state.set_state(DocumentStates.confirming_generation)
        await state.update_data(details_text=details_text)
        await message.answer(
            f"Данные получены. Сформировать документ <b>{escape(str(document_title))}</b>? 📋",
            reply_markup=confirm_generation_keyboard(),
            parse_mode="HTML",
        )
        return

    await _offer_document_checkout_after_clarifications(
        message,
        state,
        session_factory,
        payment_service,
        settings,
        details_text=details_text,
        request_text=request_text,
        document_title=str(document_title),
    )


@router.callback_query(F.data.startswith(PAY_DONE_PREFIX))
async def pay_done_continue(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    raw = callback.data or ""
    parts = raw.split(":", maxsplit=1)
    try:
        payment_id = int(parts[1])
    except (IndexError, ValueError):
        await callback.answer()
        return

    async with session_factory() as session:
        payment = await PaymentRepository(session).get_payment(payment_id)

    if not payment:
        await callback.answer("Платёж не найден", show_alert=True)
        return

    if payment.status != PaymentStatus.PAID.value:
        await callback.answer("Платёж ещё не подтверждён.", show_alert=True)
        return

    meta = payment.payment_meta or {}
    try:
        owner_telegram_id = int(meta["telegram_user_id"])
    except (KeyError, TypeError, ValueError):
        owner_telegram_id = callback.from_user.id

    if owner_telegram_id != callback.from_user.id:
        await callback.answer("Не ваш платёж", show_alert=True)
        return

    if payment.payment_kind == PaymentKind.SUBSCRIPTION.value:
        async with session_factory() as session:
            usr = await session.get(User, payment.user_id)
            if usr is None:
                await callback.answer("Не найден аккаунт подписки", show_alert=True)
                return
            if usr.telegram_id != callback.from_user.id:
                await callback.answer("Не ваш платёж", show_alert=True)
                return
            await session.refresh(usr)
            until_dt = usr.subscription_until
            until_s = until_dt.strftime("%d.%m.%Y %H:%M") if until_dt else ""

        msg = (
            "✅ Подписка активна.\n"
            + (f"Действует до {until_s}.\n\n" if until_s else "\n")
            + (
                "Теперь можно генерировать документы без отдельной оплаты за каждый."
                if settings.subscription_includes_unlimited_docs
                else "Подписка учтена. Каждый документ оплачивается отдельно по тарифу бота."
            )
        )
        await callback.message.answer(msg, reply_markup=main_menu(), parse_mode=None)
        await callback.answer()
        return

    if payment.payment_kind != PaymentKind.DOCUMENT.value or payment.document_id is None:
        await callback.answer()
        return

    doc_id = payment.document_id
    title_compact = compact_document_title(str(meta.get("document_title", "Документ")).strip())
    meta_req = meta.get("request_text", "") or ""
    meta_det = meta.get("details_text", "") or ""

    merged = await state.get_data()
    req_final = meta_req.strip() or (merged.get("request_text") or "")
    details_final = meta_det.strip() or (merged.get("details_text") or "")

    if not req_final or not details_final:
        await callback.answer("Сессия истекла. Начните документ заново из меню.", show_alert=True)
        return

    async with session_factory() as session:
        docs = DocumentRepository(session)
        document = await docs.get(doc_id)
        user = await UserRepository(session).get_or_create(
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
        )
        if document is None or document.user_id != user.id:
            await callback.answer("Документ не найден", show_alert=True)
            return
        if document.status != DocumentStatus.PAID.value:
            await callback.answer("Документ не оплачен", show_alert=True)
            return

    await state.set_state(DocumentStates.confirming_generation)
    await state.update_data(
        document_id=doc_id,
        details_text=details_final,
        request_text=req_final,
        document_title=title_compact,
        questions_prompt_text=merged.get("questions_prompt_text"),
    )
    await callback.message.answer(
        f"Данные получены. Сформировать документ <b>{escape(title_compact)}</b>? 📋",
        reply_markup=confirm_generation_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(
    StateFilter(DocumentStates.collecting_document_qa, DocumentStates.waiting_document_details),
    F.data == "document_back_request",
)
async def document_back_request(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(DocumentStates.waiting_document_request)
    await state.update_data(
        document_title=None,
        questions_prompt_text=None,
        request_text=None,
        document_id=None,
        questions_queue=None,
        qa_index=None,
        qa_transcript=None,
        qa_gate_rounds=None,
        qa_amend_mode=None,
        qa_sent_finale=None,
    )
    await callback.message.edit_text(
        DOCUMENT_PROMPT,
        reply_markup=document_flow_start_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(StateFilter(DocumentStates.confirming_generation), F.data == "document_back_details")
async def document_back_details(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    prompt = data.get("questions_prompt_text")
    document_title = data.get("document_title", "Документ")
    transcript = (data.get("qa_transcript") or "").strip()

    if transcript:
        await state.set_state(DocumentStates.collecting_document_qa)
        await state.update_data(qa_amend_mode=True)
        tr_disp = transcript[-3000:] if len(transcript) > 3000 else transcript
        body = (
            f"<b>Редактирование данных</b> — «{escape(str(document_title))}».\n\n"
            f"<b>Собрано:</b>\n{escape(tr_disp)}\n\n"
            "Пришлите <b>одним сообщением</b>, что добавить или уточнить. После этого снова проверю полноту сведений."
        )
        await callback.message.edit_text(
            body,
            parse_mode="HTML",
            reply_markup=document_questions_keyboard(),
        )
        await callback.answer()
        return

    if not prompt:
        prompt = (
            f"Уточните данные по документу <b>{escape(str(document_title))}</b>. 📝"
        )
    await state.set_state(DocumentStates.waiting_document_details)
    await callback.message.edit_text(
        prompt,
        parse_mode="HTML",
        reply_markup=document_questions_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_document")
async def cancel_document(callback: CallbackQuery, state: FSMContext) -> None:
    from app.bot.keyboards import main_menu

    await state.clear()
    await answer_with_inline_after_strip_reply_keyboard(
        callback.message,
        "Оформление отменено. ❌",
        reply_markup=main_menu(),
    )
    await callback.answer()


@router.callback_query(StateFilter(DocumentStates.confirming_generation), F.data == "generate_document")
async def generate_document(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker,
    generator: DocumentGenerator,
) -> None:
    data = await state.get_data()
    document_id = data.get("document_id")
    request_text = data.get("request_text")
    details_text = data.get("details_text")
    
    if not document_id or not request_text or not details_text:
        await callback.message.answer("Сессия оформления устарела. Начните оформление заново. 🔄")
        await state.clear()
        await callback.answer()
        return

    document_id = int(document_id)

    progress = await callback.message.answer("Формирую DOCX... ⚙️", parse_mode=None)
    async with session_factory() as session:
        documents = DocumentRepository(session)
        document = await documents.get(document_id)
        if not document:
            await progress.edit_text("Документ не найден. ❌", parse_mode=None)
            await callback.answer()
            return
        await documents.update_status(document, DocumentStatus.GENERATING)
        await session.commit()

    try:
        document_text, instruction, docx_path, pdf_path = await generator.generate_dynamic(
            document_id=document_id,
            request_text=request_text,
            details_text=details_text,
        )
    except DeepSeekError as exc:
        async with session_factory() as session:
            documents = DocumentRepository(session)
            document = await documents.get(document_id)
            if document:
                await documents.update_status(document, DocumentStatus.PAID)
                await session.commit()
        await progress.edit_text(
            f"Не удалось сгенерировать документ: {exc} ⚠️",
            parse_mode=None,
        )
        await callback.answer()
        return
    except Exception:
        logger.exception("Ошибка генерации документа document_id=%s", document_id)
        async with session_factory() as session:
            documents = DocumentRepository(session)
            document = await documents.get(document_id)
            if document:
                await documents.update_status(document, DocumentStatus.PAID)
                await session.commit()
        await progress.edit_text(
            "Внутренняя ошибка при генерации. Попробуйте позже или начните оформление снова. ❌",
            parse_mode=None,
        )
        await callback.answer()
        return

    async with session_factory() as session:
        documents = DocumentRepository(session)
        document = await documents.get(document_id)
        if not document:
            await progress.edit_text("Документ пропал из базы после генерации. ❌", parse_mode=None)
            await callback.answer()
            return
        await documents.save_generated(
            document,
            answers_json={
                "title": data.get("document_title", "Документ"),
                "request": request_text,
                "details": details_text,
            },
            document_text=document_text,
            instruction_text=instruction,
            docx_path=str(docx_path),
            pdf_path=str(pdf_path) if pdf_path else None,
        )
        await documents.update_status(document, DocumentStatus.DELIVERED)
        await session.commit()

    await state.clear()
    await progress.edit_text("Документ готов. 🎉", parse_mode=None)

    await callback.message.answer_document(
        FSInputFile(docx_path, filename=Path(docx_path).name),
        caption="DOCX файл",
        parse_mode=None,
    )
    if pdf_path and Path(pdf_path).exists():
        await callback.message.answer_document(
            FSInputFile(pdf_path, filename=Path(pdf_path).name),
            caption="PDF файл",
            parse_mode=None,
        )

    await callback.message.answer("Инструкция: ℹ️", parse_mode=None)
    instruction_chunks = list(chunk_text(instruction))
    if not instruction_chunks:
        await callback.message.answer("✅", reply_markup=main_menu(), parse_mode=None)
    else:
        for i, chunk in enumerate(instruction_chunks):
            if i == len(instruction_chunks) - 1:
                await callback.message.answer(chunk, reply_markup=main_menu(), parse_mode=None)
            else:
                await callback.message.answer(chunk, parse_mode=None)
    await callback.answer()
