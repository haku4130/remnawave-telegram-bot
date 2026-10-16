"""Подменю «Ещё» главного меню форка: промокод, инфо, язык, конкурсы и «Назад»."""

import structlog
from aiogram import Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.services.menu_layout_service import MenuContext, MenuLayoutService


logger = structlog.get_logger(__name__)


async def show_more_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession) -> None:
    """Меняет клавиатуру под сообщением меню на подменю; текст сообщения (rich-меню) остаётся."""
    if not settings.PREMIUM_THEME_ENABLED or db_user is None:
        await callback.answer()
        return

    context = MenuContext(language=db_user.language, is_admin=settings.is_admin(db_user.telegram_id))
    keyboard = await MenuLayoutService.build_keyboard(db, context, rows_key='more_rows')
    if not keyboard.inline_keyboard:
        # Сохранённая в БД раскладка без more_rows: пустая клавиатура стёрла бы кнопки меню.
        logger.warning('В раскладке меню нет more_rows, подменю «Ещё» не показано')
        await callback.answer()
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except TelegramBadRequest as error:
        if 'message is not modified' not in str(error).lower():
            logger.error('Не удалось показать подменю «Ещё»', exc_info=True)
    await callback.answer()


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(show_more_menu, F.data == 'menu_more')
