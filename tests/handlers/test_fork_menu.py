"""Подменю «Ещё» форка: смена клавиатуры под сообщением меню."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageReplyMarkup

from app.config import settings
from app.handlers import fork_menu
from app.services.menu_layout import fork_layout
from app.services.menu_layout.service import MenuLayoutService


@pytest.fixture
def fork_config(monkeypatch):
    config = fork_layout.build_fork_menu_config()

    async def fake_get_config(db):
        return config

    monkeypatch.setattr(MenuLayoutService, 'get_config', fake_get_config)
    monkeypatch.setattr(MenuLayoutService, '_evaluate_conditions', staticmethod(lambda conditions, context: True))
    return config


def _callback():
    callback = MagicMock()
    callback.message.edit_reply_markup = AsyncMock()
    callback.answer = AsyncMock()
    return callback


def _user():
    return SimpleNamespace(language='ru', telegram_id=111)


@pytest.mark.asyncio
async def test_theme_on_swaps_keyboard_to_submenu(monkeypatch, fork_config):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    callback = _callback()

    await fork_menu.show_more_menu(callback, _user(), MagicMock())

    callback.message.edit_reply_markup.assert_awaited_once()
    keyboard = callback.message.edit_reply_markup.await_args.kwargs['reply_markup']
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert [button.callback_data for button in buttons] == [
        'menu_promocode',
        'menu_info',
        'menu_language',
        'contests_menu',
        'back_to_menu',
    ]
    assert buttons[-1].callback_data == 'back_to_menu'
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_theme_off_does_nothing_but_answer(monkeypatch, fork_config):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    callback = _callback()

    await fork_menu.show_more_menu(callback, _user(), MagicMock())

    callback.message.edit_reply_markup.assert_not_awaited()
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_user_does_nothing_but_answer(monkeypatch, fork_config):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    callback = _callback()

    await fork_menu.show_more_menu(callback, None, MagicMock())

    callback.message.edit_reply_markup.assert_not_awaited()
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_message_not_modified_is_swallowed(monkeypatch, fork_config):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    callback = _callback()
    callback.message.edit_reply_markup.side_effect = TelegramBadRequest(
        method=EditMessageReplyMarkup(chat_id=1, message_id=1),
        message='Bad Request: message is not modified',
    )

    await fork_menu.show_more_menu(callback, _user(), MagicMock())

    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_other_bad_request_is_logged_and_answered(monkeypatch, fork_config):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    callback = _callback()
    callback.message.edit_reply_markup.side_effect = TelegramBadRequest(
        method=EditMessageReplyMarkup(chat_id=1, message_id=1),
        message='Bad Request: message to edit not found',
    )
    logger = MagicMock()
    monkeypatch.setattr(fork_menu, 'logger', logger)

    await fork_menu.show_more_menu(callback, _user(), MagicMock())

    logger.error.assert_called_once()
    assert logger.error.call_args.kwargs['exc_info'] is True
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_config_without_more_rows_keeps_menu_untouched(monkeypatch, fork_config):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    del fork_config['more_rows']
    callback = _callback()

    await fork_menu.show_more_menu(callback, _user(), MagicMock())

    callback.message.edit_reply_markup.assert_not_awaited()
    callback.answer.assert_awaited_once()


def test_register_handlers_registers_menu_more():
    dp = MagicMock()

    fork_menu.register_handlers(dp)

    dp.callback_query.register.assert_called_once()
    assert dp.callback_query.register.call_args.args[0] is fork_menu.show_more_menu
