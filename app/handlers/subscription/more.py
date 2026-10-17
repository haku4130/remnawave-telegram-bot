"""Подменю «Ещё» экрана «Подписка» (тема форка): копирование ссылки, настройки, подарок и прочее второстепенное.

Как «Ещё» главного меню (app/handlers/fork_menu.py): меняются только кнопки под сообщением,
текст экрана подписки остаётся. «Назад» (menu_subscription) рисует экран подписки заново.
"""

import structlog
from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InaccessibleMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_subscription_more_keyboard


logger = structlog.get_logger(__name__)


async def fork_has_tariff_choice(db: AsyncSession) -> bool:
    """Есть ли из чего выбирать: «Тариф» в подменю «Ещё» — только при двух и более активных тарифах."""
    if not settings.is_tariffs_mode():
        return False
    from app.database.crud.tariff import count_tariffs

    return await count_tariffs(db) >= 2


async def show_subscription_more(callback: types.CallbackQuery, db_user: User, db: AsyncSession) -> None:
    subscription = db_user.subscription if db_user is not None else None
    if (
        not settings.PREMIUM_THEME_ENABLED
        or settings.is_multi_tariff_enabled()
        or subscription is None
        or isinstance(callback.message, InaccessibleMessage)
    ):
        await callback.answer()
        return

    # Кнопки тарифа и докупки трафика смотрят на тариф подписки — подгружаем его, как экран подписки.
    if settings.is_tariffs_mode() and subscription.tariff_id:
        from app.database.crud.tariff import get_tariff_by_id

        tariff = await get_tariff_by_id(db, subscription.tariff_id)
        if tariff:
            subscription.tariff = tariff

    keyboard = get_subscription_more_keyboard(
        db_user.language,
        subscription,
        is_trial=bool(subscription.is_trial),
        # Как на экране подписки: подарки там включены всегда.
        gift_enabled=True,
        multiple_tariffs=await fork_has_tariff_choice(db),
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except TelegramBadRequest as error:
        if 'message is not modified' not in str(error).lower():
            logger.error('Не удалось показать подменю «Ещё» подписки', exc_info=True)
    await callback.answer()
