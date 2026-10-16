"""Путь новичка форка в start.py: автоязык, ?start=rules, приветствие."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, User as TgUser

from app.config import settings
from app.handlers import start


def _state(user_id=7):
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=user_id, user_id=user_id))


def _message(text='/start', language_code='en', user_id=7):
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.from_user = TgUser(id=user_id, is_bot=False, first_name='Тест', language_code=language_code)
    msg.chat = Chat(id=user_id, type='private')
    msg.answer = AsyncMock()
    msg.bot = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_auto_language_skips_prompt_and_stores_detected(monkeypatch):
    monkeypatch.setattr(settings, 'LANGUAGE_AUTO_DETECT', True)
    monkeypatch.setattr(settings, 'LANGUAGE_SELECTION_ENABLED', True)
    monkeypatch.setattr(settings, 'AVAILABLE_LANGUAGES', 'ru,en')
    prompt = AsyncMock()
    monkeypatch.setattr(start, '_prompt_language_selection', prompt)

    state = _state()
    proceed = await start._resolve_new_user_language(_message(language_code='uk'), state)

    assert proceed is True
    prompt.assert_not_awaited()
    assert (await state.get_data())['language'] == 'ru'


@pytest.mark.asyncio
async def test_without_auto_language_prompt_is_shown(monkeypatch):
    monkeypatch.setattr(settings, 'LANGUAGE_AUTO_DETECT', False)
    monkeypatch.setattr(settings, 'LANGUAGE_SELECTION_ENABLED', True)
    prompt = AsyncMock()
    monkeypatch.setattr(start, '_prompt_language_selection', prompt)
    state = _state()
    proceed = await start._resolve_new_user_language(_message(), state)
    assert proceed is False
    prompt.assert_awaited_once()


@pytest.mark.asyncio
async def test_rules_deeplink_for_new_user_shows_rules_without_registration(monkeypatch):
    from app.utils import fork_onboarding

    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    monkeypatch.setattr(settings, 'LANGUAGE_AUTO_DETECT', True)
    monkeypatch.setattr(settings, 'AVAILABLE_LANGUAGES', 'ru,en')
    monkeypatch.setattr(start, 'get_pending_payload_from_redis', AsyncMock(return_value=None))
    monkeypatch.setattr(start, 'get_user_by_telegram_id', AsyncMock(return_value=None))
    monkeypatch.setattr(
        start, '_evaluate_telegram_registration_access', AsyncMock(return_value=MagicMock(allowed=True, reason=None))
    )
    sent = AsyncMock()
    monkeypatch.setattr(fork_onboarding, 'send_rules_screen', sent)
    register = AsyncMock()
    monkeypatch.setattr(start, '_resolve_new_user_language', register)

    await start.cmd_start(_message('/start rules', language_code='ru'), _state(), AsyncMock(), db_user=None)

    sent.assert_awaited_once()
    assert sent.await_args.kwargs['with_menu_button'] is False
    assert sent.await_args.args[2] == 'ru'
    register.assert_not_awaited()


@pytest.mark.asyncio
async def test_rules_deeplink_for_registered_user_uses_own_language_and_menu_button(monkeypatch):
    from app.database.models import UserStatus
    from app.utils import fork_onboarding

    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    monkeypatch.setattr(start, 'get_pending_payload_from_redis', AsyncMock(return_value=None))
    monkeypatch.setattr(
        start, '_evaluate_telegram_registration_access', AsyncMock(return_value=MagicMock(allowed=True, reason=None))
    )
    sent = AsyncMock()
    monkeypatch.setattr(fork_onboarding, 'send_rules_screen', sent)
    user = MagicMock(status=UserStatus.ACTIVE.value, language='en')

    await start.cmd_start(_message('/start rules', language_code='ru'), _state(), AsyncMock(), db_user=user)

    sent.assert_awaited_once()
    assert sent.await_args.kwargs['with_menu_button'] is True
    assert sent.await_args.args[2] == 'en'


@pytest.mark.asyncio
async def test_send_rules_screen_paginates_and_adds_menu_button(monkeypatch):
    from app.utils import fork_onboarding
    from app.utils.telegram_html import stored_html_to_telegram_pages

    rules_html = ''.join(f'<p>Пункт {i}. Пользователь обязуется соблюдать правила сервиса.</p>' for i in range(200))
    assert len(stored_html_to_telegram_pages(rules_html, max_length=3500)) >= 2
    monkeypatch.setattr(fork_onboarding, 'get_current_rules_content', AsyncMock(return_value=rules_html))
    msg = _message()
    await fork_onboarding.send_rules_screen(msg, AsyncMock(), 'ru', with_menu_button=True)
    assert msg.answer.await_count >= 2
    last_kwargs = msg.answer.await_args_list[-1].kwargs
    assert last_kwargs['reply_markup'].inline_keyboard[0][0].callback_data == 'back_to_menu'
    assert 'reply_markup' not in msg.answer.await_args_list[0].kwargs


@pytest.mark.asyncio
async def test_send_rules_screen_without_menu_button_has_no_markup(monkeypatch):
    from app.utils import fork_onboarding

    monkeypatch.setattr(fork_onboarding, 'get_current_rules_content', AsyncMock(return_value='<p>Правила</p>'))
    msg = _message()
    await fork_onboarding.send_rules_screen(msg, AsyncMock(), 'ru', with_menu_button=False)
    msg.answer.assert_awaited_once()
    assert 'reply_markup' not in msg.answer.await_args.kwargs


def test_rules_url(monkeypatch):
    from app.utils import fork_onboarding

    monkeypatch.setattr(settings, 'BOT_USERNAME', '@zanity_vpn_bot')
    assert fork_onboarding.rules_url() == 'https://t.me/zanity_vpn_bot?start=rules'
    monkeypatch.setattr(settings, 'BOT_USERNAME', None)
    assert fork_onboarding.rules_url() == ''


@pytest.mark.asyncio
async def test_welcome_offer_prefers_fork_screen(monkeypatch):
    from types import SimpleNamespace

    from app.utils import fork_onboarding

    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    monkeypatch.setattr(fork_onboarding, 'build_welcome_screen', lambda user, tg: ('ФОРК', 'KB'))
    db_welcome = AsyncMock(return_value='ИЗ БД')
    monkeypatch.setattr('app.database.crud.welcome_text.get_welcome_text_for_user', db_welcome)
    text, keyboard = await start._welcome_offer(AsyncMock(), SimpleNamespace(language='ru'), SimpleNamespace())
    assert (text, keyboard) == ('ФОРК', 'KB')
    db_welcome.assert_not_awaited()


@pytest.mark.asyncio
async def test_welcome_offer_uses_db_text_when_theme_off(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    monkeypatch.setattr('app.database.crud.welcome_text.get_welcome_text_for_user', AsyncMock(return_value='ИЗ БД'))
    text, keyboard = await start._welcome_offer(AsyncMock(), SimpleNamespace(language='ru'), SimpleNamespace())
    assert text == 'ИЗ БД' and keyboard is None
