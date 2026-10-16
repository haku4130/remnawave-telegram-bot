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
