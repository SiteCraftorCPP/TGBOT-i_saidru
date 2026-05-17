from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.keyboards import (
    MENU_IGNORE,
    MENU_MAIN,
    main_menu,
    subscription_offer_keyboard,
    yookassa_checkout_keyboard,
)
from app.bot.reply_markup_safe import answer_with_inline_after_strip_reply_keyboard
from app.bot.states import ConsultationStates
from app.core.config import Settings
from app.core.constants import (
    MAIN_MENU_CONSULTATION,
    MAIN_MENU_DOCUMENT,
    MAIN_MENU_HISTORY,
)
from app.db.models import PaymentKind, PaymentStatus
from app.db.repositories import PaymentRepository, UserRepository
from app.bot.handlers.telegram_payments import telegram_subscription_invoice_kw
from app.integrations.yookassa.client import YooKassaApiError
from app.services.telegram_invoice_finalize import (
    TelegramInvoiceAmountTooLowError,
    enforce_telegram_rub_invoice_minimum,
)
from app.services.payments import (
    PAYMENTS_CONFIGURE_HELP_TEXT,
    PAYMENTS_DISABLED_ADMIN_DIAGNOSTIC,
    PAYMENTS_TURNED_OFF_USER_MESSAGE,
    create_pending_subscription_payment,
    create_yookassa_subscription_payment,
)

router = Router()


WELCOME_TEXT = (
    "Опишите вашу ситуацию простыми словами. 📝\n\n"
    "Например:\n"
    "• магазин не возвращает деньги 🛒\n"
    "• нужна жалоба работодателю 💼\n"
    "• нужен договор аренды 🏠\n"
    "• нужно написать заявление ✍️"
)


@router.message(Command("start"))
async def start(message: Message, state: FSMContext, session_factory: async_sessionmaker) -> None:
    await state.clear()
    await state.set_state(ConsultationStates.waiting_problem)
    async with session_factory() as session:
        await UserRepository(session).get_or_create(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
        )
        await session.commit()
    # Сразу одно сообщение: приветствие + инлайн-меню. Без strip-сообщения (иначе в ленту
    # на мгновение попадает пустышка и только потом «нормальный» экран).
    await message.answer(WELCOME_TEXT, reply_markup=main_menu())


@router.callback_query(F.data == MENU_MAIN)
async def menu_main_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ConsultationStates.waiting_problem)
    try:
        await callback.message.edit_text(WELCOME_TEXT, reply_markup=main_menu())
    except Exception:
        await callback.message.answer(WELCOME_TEXT, reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == MENU_IGNORE)
async def noop_callback(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == "subscribe_month")
async def subscribe_month_handler(
    callback: CallbackQuery,
    settings: Settings,
) -> None:
    price = f"{settings.subscription_price_rub} ₽"
    if settings.subscription_includes_unlimited_docs:
        value_intro = (
            "Что входит:\n"
            "• безлимитная генерация документов на 30 дней\n"
            "• документы по консультациям и из каталога без дополнительной оплаты за каждый файл\n\n"
        )
    else:
        value_intro = (
            "С генерацией документов платёж всё равно за каждый файл по тарифу бота; запись подписки "
            "в аккаунте продлевается на 30 дней (назначение — по правилам проекта).\n\n"
        )
    await callback.message.answer(
        "⭐ Подписка на месяц\n\n"
        f"Стоимость: {price} / месяц.\n"
        + value_intro
        + "После оплаты подписка активируется на 30 дней.",
        reply_markup=subscription_offer_keyboard(price),
        parse_mode=None,
    )
    await callback.answer()


@router.callback_query(F.data == "subscribe_pay_month")
async def subscribe_pay_month_handler(
    callback: CallbackQuery,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    if settings.is_admin(callback.from_user.id):
        await callback.message.answer(
            "У аккаунта администратора уже безлимитный доступ к функциям бота. 🔑",
            reply_markup=main_menu(),
            parse_mode=None,
        )
        await callback.answer()
        return

    if settings.payments_enabled and settings.telegram_native_payment_token_configured():
        try:
            enforce_telegram_rub_invoice_minimum(settings.subscription_price_rub, what="SUBSCRIPTION_PRICE_RUB")
        except TelegramInvoiceAmountTooLowError as exc:
            await callback.message.answer(str(exc), reply_markup=main_menu(), parse_mode=None)
            await callback.answer()
            return

    async with session_factory() as session:
        repo = UserRepository(session)
        user = await repo.get_or_create(callback.from_user.id, callback.from_user.username)
        pays = PaymentRepository(session)

        if settings.payments_enabled:
            tg_native = settings.telegram_native_payment_token_configured()
            yoo_ok = settings.yookassa_configured()

            if not tg_native and not yoo_ok:
                await callback.message.answer(
                    PAYMENTS_CONFIGURE_HELP_TEXT,
                    reply_markup=main_menu(),
                    parse_mode=None,
                )
                await session.commit()
                await callback.answer()
                return

            if tg_native:
                pay = await create_pending_subscription_payment(pays, user, settings)
                await session.commit()
                await callback.message.answer_invoice(
                    **telegram_subscription_invoice_kw(
                        payment_row_id=pay.id,
                        price_rub=settings.subscription_price_rub,
                        provider_token=settings.telegram_payment_provider_token,
                    ),
                )
                await callback.answer()
                return

            try:
                pay_url, _pid = await create_yookassa_subscription_payment(
                    settings=settings,
                    payments=pays,
                    user=user,
                )
            except YooKassaApiError as exc:
                await session.commit()
                await callback.message.answer(
                    f"Не удалось создать платёж ЮKassa. Попробуйте позже.\nПричина: {exc}",
                    reply_markup=main_menu(),
                    parse_mode=None,
                )
                await callback.answer()
                return

            await session.commit()
            if pay_url:
                await callback.message.answer(
                    "Оформление подписки: оплатите месяц в ЮKassa.\n"
                    "После оплаты бот отправит сообщение — нажмите «Продолжить» там.",
                    reply_markup=yookassa_checkout_keyboard(pay_url),
                    parse_mode=None,
                )
            else:
                await callback.message.answer(
                    "Платёж создан, но ссылку не удалось получить из ответа ЮKassa.",
                    reply_markup=main_menu(),
                    parse_mode=None,
                )
            await callback.answer()
            return

        # PAYMENTS_ENABLED=false (не-админы: админы отсеяны в начале обработчика).
        await callback.message.answer(
            PAYMENTS_TURNED_OFF_USER_MESSAGE,
            reply_markup=main_menu(),
            parse_mode=None,
        )
        await session.commit()
        await callback.answer()
        return

@router.message(Command("new"))
async def new_consultation(message: Message, state: FSMContext) -> None:
    from app.bot.keyboards import document_flow_start_keyboard

    await state.clear()
    await state.set_state(ConsultationStates.waiting_problem)
    text = "Опишите ситуацию одним сообщением. Я разберу ее и предложу документ, если он нужен. 💬"
    await answer_with_inline_after_strip_reply_keyboard(
        message,
        text,
        reply_markup=document_flow_start_keyboard(),
    )


@router.callback_query(F.data == "menu_consultation")
@router.message(F.text == MAIN_MENU_CONSULTATION)
async def new_consultation_callback(event: CallbackQuery | Message, state: FSMContext) -> None:
    from app.bot.keyboards import document_flow_start_keyboard

    await state.clear()
    await state.set_state(ConsultationStates.waiting_problem)
    message = event.message if isinstance(event, CallbackQuery) else event
    text = "Опишите ситуацию одним сообщением. Я разберу ее и предложу документ, если он нужен. 💬"

    if isinstance(event, Message):
        await answer_with_inline_after_strip_reply_keyboard(
            message,
            text,
            reply_markup=document_flow_start_keyboard(),
        )
    else:
        await message.answer(text, reply_markup=document_flow_start_keyboard())
        await event.answer()

@router.callback_query(F.data == "new_consultation")
async def new_consultation_callback_duplicate(callback: CallbackQuery, state: FSMContext) -> None:
    from app.bot.keyboards import document_flow_start_keyboard

    await state.clear()
    await state.set_state(ConsultationStates.waiting_problem)
    await callback.message.answer(
        "Опишите ситуацию одним сообщением. Я разберу ее и предложу документ, если он нужен. 💬",
        reply_markup=document_flow_start_keyboard(),
    )
    await callback.answer()

@router.message(Command("delete_my_data"))
async def delete_my_data(message: Message, state: FSMContext, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        await UserRepository(session).delete_user_data(message.from_user.id)
        await session.commit()
    await state.clear()
    await answer_with_inline_after_strip_reply_keyboard(
        message,
        "Ваши данные удалены. 🗑️",
        reply_markup=main_menu(),
    )

