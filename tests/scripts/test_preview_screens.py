from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import InlineKeyboardMarkup

from app.config import settings
from app.localization import loader
from app.localization.emoji_catalog import CATALOG
from app.utils import fork_onboarding
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


@pytest.mark.parametrize('language', ['ru', 'en'])
def test_stage_two_screens_present(theme_on, monkeypatch, language):
    monkeypatch.setattr(settings, 'BOT_USERNAME', 'zanity_vpn_bot')
    monkeypatch.setattr(fork_onboarding, 'is_trial_available', lambda user: True)
    screens = preview_screens.build_screens(language)
    titles = [screen.title for screen in screens]
    for expected in (
        'Приветствие — пробный',
        'Пробный включён',
        'Меню — пробный',
        'Меню — подключиться',
        'Меню — продлить',
        'Меню — тариф',
    ):
        assert expected in titles, expected
    for screen in screens:
        assert len(screen.text) <= 4096, screen.title


def test_stage_two_screens_absent_without_theme(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    try:
        titles = [screen.title for screen in preview_screens.build_screens('ru')]
    finally:
        loader.clear_locale_cache()
    assert not any(title.startswith('Приветствие') for title in titles)


def test_stage_two_menus_have_one_green_button(theme_on, monkeypatch):
    monkeypatch.setattr(settings, 'BOT_USERNAME', 'zanity_vpn_bot')
    screens = {screen.title: screen for screen in preview_screens.build_screens('ru')}
    for title in ('Меню — пробный', 'Меню — подключиться', 'Меню — продлить', 'Меню — тариф'):
        buttons = [button for row in screens[title].markup.inline_keyboard for button in row]
        assert sum(1 for button in buttons if button.style == 'success') == 1, title


def test_welcome_title_follows_actual_variant(theme_on, monkeypatch):
    monkeypatch.setattr(settings, 'BOT_USERNAME', 'zanity_vpn_bot')
    monkeypatch.setattr(fork_onboarding, 'is_trial_available', lambda user: False)
    screens = {screen.title: screen for screen in preview_screens.build_screens('ru')}
    assert 'Приветствие — пробный' not in screens
    welcome = screens['Приветствие — без пробного']
    assert welcome.markup.inline_keyboard[0][0].callback_data == 'menu_buy'


def test_stage_one_menus_have_one_green_button(theme_on):
    screens = {screen.title: screen for screen in preview_screens.build_screens('ru')}
    expected = {'Главное меню — новичок': 'trial_activate', 'Главное меню — подписчик': None}
    for title, green_callback in expected.items():
        buttons = [button for row in screens[title].markup.inline_keyboard for button in row]
        greens = [button for button in buttons if button.style == 'success']
        assert len(greens) == 1, title
        if green_callback:
            assert greens[0].callback_data == green_callback, title
