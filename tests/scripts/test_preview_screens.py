from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import InlineKeyboardMarkup

from app.config import settings
from app.localization import loader
from app.localization.emoji_catalog import CATALOG
from scripts import preview_screens


@pytest.fixture
def theme_on(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    loader.clear_locale_cache()
    yield
    loader.clear_locale_cache()


@pytest.mark.parametrize('language', ['ru', 'en'])
def test_build_screens_contains_catalog_sheets_and_menus(theme_on, language):
    screens = preview_screens.build_screens(language)
    titles = [screen.title for screen in screens]
    assert 'Каталог эмодзи' in titles[0]
    assert any('Главное меню' in title for title in titles)

    catalog_screens = [screen for screen in screens if screen.title.startswith('Каталог эмодзи')]
    assert len(catalog_screens) >= 2
    assert sum(screen.text.count('<tg-emoji') for screen in catalog_screens) >= 60
    assert sum(screen.text.count('<tg-emoji') for screen in catalog_screens) == len(CATALOG)
    for screen in screens:
        assert len(screen.text) <= 4096, screen.title
        assert screen.markup is None or isinstance(screen.markup, InlineKeyboardMarkup)


def test_menu_screens_have_buttons(theme_on):
    menus = [screen for screen in preview_screens.build_screens('ru') if 'Главное меню' in screen.title]
    assert len(menus) >= 2
    for screen in menus:
        assert screen.markup is not None
        assert screen.markup.inline_keyboard, screen.title


@pytest.mark.asyncio
async def test_send_screens_enables_theme_and_sends_everything_to_one_chat(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.session.close = AsyncMock()
    monkeypatch.setattr(preview_screens, 'create_bot', lambda *args, **kwargs: bot)
    try:
        sent = await preview_screens.send_screens(42, 'ru')
        assert settings.PREMIUM_THEME_ENABLED is True
    finally:
        loader.clear_locale_cache()

    assert sent == len(preview_screens.build_screens('ru'))
    assert bot.send_message.await_count == sent * 2
    assert {call.args[0] for call in bot.send_message.await_args_list} == {42}
    bot.session.close.assert_awaited_once()
