"""Handler for RollyPay balance top-up."""

import structlog
from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_back_keyboard
from app.keyboards.topup_amounts import get_topup_amount_keyboard
from app.localization.texts import get_texts
from app.services.payment_service import PaymentService
from app.states import BalanceStates
from app.utils.decorators import error_handler
from app.utils.payment_utils import topup_restriction_keyboard, topup_restriction_text


logger = structlog.get_logger(__name__)


_RESTRICTION_TEMPLATE = '\U0001f6ab <b>Пополнение ограничено</b>\n\n{reason}'


def _check_topup_restriction(db_user: User, texts) -> InlineKeyboardMarkup | None:
    """Проверяет ограничение на пополнение. Возвращает клавиатуру если ограничен, иначе None."""
    if not getattr(db_user, 'restriction_topup', False):
        return None
    return topup_restriction_keyboard(texts)


def _rubles(amount_kopeks: int) -> str:
    """Сумма в рублях для шаблонов форка: «500» или «500.50», без «.00»."""
    if amount_kopeks % 100 == 0:
        return str(amount_kopeks // 100)
    return f'{amount_kopeks / 100:.2f}'


def _limit_rubles(amount_kopeks: int) -> int | str:
    """Предел суммы в рублях: под темой с пробелом между тысячами («100 000»), иначе целое, как у апстрима."""
    rubles = amount_kopeks // 100
    return f'{rubles:,}'.replace(',', ' ') if settings.PREMIUM_THEME_ENABLED else rubles


async def _create_rollypay_payment_and_respond(
    message_or_callback,
    db_user: User,
    db: AsyncSession,
    amount_kopeks: int,
    edit_message: bool = False,
):
    """
    Common logic for creating RollyPay payment and sending response.
    """
    texts = get_texts(db_user.language)
    amount_rub = amount_kopeks / 100

    # Create payment
    payment_service = PaymentService()

    description = settings.PAYMENT_BALANCE_TEMPLATE.format(
        service_name=settings.PAYMENT_SERVICE_NAME,
        description='Пополнение баланса',
    )

    result = await payment_service.create_rollypay_payment(
        db=db,
        user_id=db_user.id,
        amount_kopeks=amount_kopeks,
        description=description,
        email=getattr(db_user, 'email', None),
        language=db_user.language,
    )

    if not result:
        error_text = texts.t(
            'PAYMENT_CREATE_ERROR',
            'Не удалось создать платёж. Попробуйте позже.',
        )
        if edit_message:
            await message_or_callback.edit_text(
                error_text,
                reply_markup=get_back_keyboard(db_user.language),
                parse_mode='HTML',
            )
        else:
            await message_or_callback.answer(
                error_text,
                parse_mode='HTML',
            )
        return

    payment_url = result.get('payment_url')
    themed = settings.PREMIUM_THEME_ENABLED
    display_name = settings.get_rollypay_display_name_html() if themed else settings.get_rollypay_display_name()
    # Под темой сумма без «.00» («500»), а не «500.00»; кнопка — не «500» от округления копеек.
    button_amount = _rubles(amount_kopeks) if themed else f'{amount_rub:.0f}'
    text_amount = _rubles(amount_kopeks) if themed else f'{amount_rub:.2f}'

    pay_button = InlineKeyboardButton(
        text=texts.t(
            'PAY_BUTTON',
            '\U0001f4b3 Оплатить {amount}\u20bd',
        ).format(amount=button_amount),
        url=payment_url,
    )
    if themed:
        # URL-кнопке цвет по таблице не достаётся; оплатить — главное действие экрана.
        pay_button = pay_button.model_copy(update={'style': 'success'})

    # Create keyboard with payment button
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [pay_button],
            [
                InlineKeyboardButton(
                    text=texts.t('BACK_BUTTON', '\u25c0\ufe0f Назад'),
                    callback_data='menu_balance',
                )
            ],
        ]
    )

    response_text = texts.t(
        'ROLLYPAY_PAYMENT_CREATED',
        '\U0001f4b3 <b>Оплата через {name}</b>\n\n'
        'Сумма: <b>{amount}\u20bd</b>\n\n'
        'Нажмите кнопку ниже для оплаты.\n'
        'После успешной оплаты баланс будет пополнен автоматически.',
    ).format(name=display_name, amount=text_amount)

    if edit_message:
        await message_or_callback.edit_text(
            response_text,
            reply_markup=keyboard,
            parse_mode='HTML',
        )
    else:
        await message_or_callback.answer(
            response_text,
            reply_markup=keyboard,
            parse_mode='HTML',
        )

    logger.info('RollyPay payment created', telegram_id=db_user.telegram_id, amount_rub=amount_rub)


@error_handler
async def process_rollypay_payment_amount(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    amount_kopeks: int,
    state: FSMContext,
):
    """
    Process payment amount directly.
    """
    texts = get_texts(db_user.language)

    restriction_kb = _check_topup_restriction(db_user, texts)
    if restriction_kb:
        await message.answer(
            topup_restriction_text(texts, db_user, _RESTRICTION_TEMPLATE),
            parse_mode='HTML',
            reply_markup=restriction_kb,
        )
        await state.clear()
        return

    # Validate amount
    min_amount = settings.ROLLYPAY_MIN_AMOUNT_KOPEKS
    max_amount = settings.ROLLYPAY_MAX_AMOUNT_KOPEKS

    if amount_kopeks < min_amount:
        await message.answer(
            texts.t(
                'PAYMENT_AMOUNT_TOO_LOW',
                'Минимальная сумма пополнения: {min_amount}\u20bd',
            ).format(min_amount=_limit_rubles(min_amount)),
            reply_markup=get_back_keyboard(db_user.language),
            parse_mode='HTML',
        )
        return

    if amount_kopeks > max_amount:
        await message.answer(
            texts.t(
                'PAYMENT_AMOUNT_TOO_HIGH',
                'Максимальная сумма пополнения: {max_amount}\u20bd',
            ).format(max_amount=_limit_rubles(max_amount)),
            reply_markup=get_back_keyboard(db_user.language),
            parse_mode='HTML',
        )
        return

    await state.clear()

    await _create_rollypay_payment_and_respond(
        message_or_callback=message,
        db_user=db_user,
        db=db,
        amount_kopeks=amount_kopeks,
        edit_message=False,
    )


@error_handler
async def start_rollypay_topup(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """
    Start RollyPay top-up process - ask for amount.
    """
    texts = get_texts(db_user.language)

    restriction_kb = _check_topup_restriction(db_user, texts)
    if restriction_kb:
        await callback.message.edit_text(
            topup_restriction_text(texts, db_user, _RESTRICTION_TEMPLATE),
            parse_mode='HTML',
            reply_markup=restriction_kb,
        )
        return

    await state.set_state(BalanceStates.waiting_for_amount)
    await state.update_data(payment_method='rollypay')

    min_amount = settings.ROLLYPAY_MIN_AMOUNT_KOPEKS // 100
    max_amount = settings.ROLLYPAY_MAX_AMOUNT_KOPEKS // 100
    display_name = (
        settings.get_rollypay_display_name_html()
        if settings.PREMIUM_THEME_ENABLED
        else settings.get_rollypay_display_name()
    )

    keyboard = await get_topup_amount_keyboard('rollypay', db_user.language)

    await callback.message.edit_text(
        texts.t(
            'ROLLYPAY_ENTER_AMOUNT',
            '\U0001f4b3 <b>Пополнение через {name}</b>\n\n'
            'Введите сумму пополнения в рублях.\n\n'
            'Минимум: {min_amount}\u20bd\n'
            'Максимум: {max_amount}\u20bd',
        ).format(
            name=display_name,
            min_amount=min_amount,
            max_amount=f'{max_amount:,}'.replace(',', ' '),
        ),
        parse_mode='HTML',
        reply_markup=keyboard,
    )
