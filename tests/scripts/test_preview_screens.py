from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import InlineKeyboardMarkup

from app.config import settings
from app.localization import loader
from app.localization.emoji_catalog import CATALOG
from app.utils import fork_onboarding, rich_menu
from scripts import preview_screens


MENU_TITLES = ('Меню — пробный', 'Меню — подключиться', 'Меню — продлить', 'Меню — тариф')


@pytest.fixture
def theme_on(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    loader.clear_locale_cache()
    yield
    loader.clear_locale_cache()


@pytest.fixture
def db_sources_forbidden(monkeypatch):
    """Источники rich-меню, которым нужна БД: если предпросмотр до них доберётся — вызов запишется."""
    touched = []

    def forbidden(name):
        async def call(*args, **kwargs):
            touched.append(name)
            raise AssertionError(f'{name}: предпросмотр не должен ходить в БД')

        return call

    for name in (
        'build_promo_offer_hint',
        'build_test_access_hint',
        'get_random_active_message',
        'get_all_subscriptions_by_user_id',
        'get_tariff_by_id',
    ):
        monkeypatch.setattr(rich_menu, name, forbidden(name))
    return touched


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_build_screens_contains_catalog_sheets_and_menus(theme_on, language):
    screens = await preview_screens.build_screens(language)
    titles = [screen.title for screen in screens]
    assert 'Каталог эмодзи' in titles[0]
    assert all(title in titles for title in MENU_TITLES)

    catalog_screens = [screen for screen in screens if screen.title.startswith('Каталог эмодзи')]
    assert len(catalog_screens) >= 2
    assert sum(screen.text.count('<tg-emoji') for screen in catalog_screens) >= 60
    assert sum(screen.text.count('<tg-emoji') for screen in catalog_screens) == len(CATALOG)
    for screen in screens:
        if not screen.rich:
            assert len(screen.text) <= 4096, screen.title
        assert screen.markup is None or isinstance(screen.markup, InlineKeyboardMarkup)


@pytest.mark.asyncio
@pytest.mark.parametrize('multi_tariff', [False, True])
async def test_rich_menus_built_without_db(theme_on, db_sources_forbidden, monkeypatch, multi_tariff):
    monkeypatch.setattr(type(settings), 'is_multi_tariff_enabled', lambda self: multi_tariff)
    screens = {screen.title: screen for screen in await preview_screens.build_screens('ru')}
    assert db_sources_forbidden == []
    for title in MENU_TITLES:
        screen = screens[title]
        assert screen.rich, title
        assert '<hr/>' in screen.text and '<footer>' in screen.text, title
        assert '<h4>' not in screen.text and '<h6>' not in screen.text, title
        assert screen.markup is not None and screen.markup.inline_keyboard, title
    # Состояния различаются по-настоящему: у подписчика — подписка, у новичка — нет.
    assert 'Алексей' in screens['Меню — подключиться'].text
    assert 'example.com/sub' in screens['Меню — подключиться'].text
    assert 'example.com/sub' not in screens['Меню — пробный'].text


@pytest.mark.asyncio
async def test_plain_screens_are_not_rich(theme_on):
    screens = {screen.title: screen for screen in await preview_screens.build_screens('ru')}
    assert not screens['Пробный включён'].rich
    assert not any(screen.rich for title, screen in screens.items() if title.startswith('Каталог эмодзи'))


@pytest.mark.asyncio
async def test_send_screens_enables_theme_and_sends_everything_to_one_chat(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    monkeypatch.setattr(settings, 'MAIN_MENU_RICH_EFFECT_ID', 'effect-123')
    monkeypatch.setattr(settings, 'MAIN_MENU_RICH_INLINE_BUTTONS', False, raising=False)
    loader.clear_locale_cache()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_rich_message = AsyncMock()
    bot.session.close = AsyncMock()
    monkeypatch.setattr(preview_screens, 'create_bot', lambda *args, **kwargs: bot)
    try:
        sent = await preview_screens.send_screens(42, 'ru')
        assert settings.PREMIUM_THEME_ENABLED is True
        screens = await preview_screens.build_screens('ru')
    finally:
        loader.clear_locale_cache()

    rich = [screen for screen in screens if screen.rich]
    plain = [screen for screen in screens if not screen.rich]
    assert sent == len(screens)
    assert len(rich) == len(MENU_TITLES)

    # Подписи всех экранов + тела обычных экранов — sendMessage без превью ссылок.
    assert bot.send_message.await_count == len(screens) + len(plain)
    for call in bot.send_message.await_args_list:
        assert call.args[0] == 42
        assert call.kwargs['link_preview_options'].is_disabled is True

    # Меню — через rich-путь бота, без эффекта, с клавиатурой раскладки форка.
    assert bot.send_rich_message.await_count == len(rich)
    for call, screen in zip(bot.send_rich_message.await_args_list, rich, strict=True):
        assert call.kwargs['chat_id'] == 42
        assert call.kwargs['message_effect_id'] is None
        assert '<hr/>' in call.kwargs['rich_message'].html
        assert call.kwargs['reply_markup'] == screen.markup
    bot.session.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_stage_two_screens_present(theme_on, monkeypatch, language):
    monkeypatch.setattr(settings, 'BOT_USERNAME', 'zanity_vpn_bot')
    monkeypatch.setattr(fork_onboarding, 'is_trial_available', lambda user: True)
    screens = await preview_screens.build_screens(language)
    titles = [screen.title for screen in screens]
    for expected in ('Приветствие — пробный', 'Пробный включён', *MENU_TITLES):
        assert expected in titles, expected
    for screen in screens:
        if not screen.rich:
            assert len(screen.text) <= 4096, screen.title


@pytest.mark.asyncio
async def test_stage_two_screens_absent_without_theme(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    try:
        titles = [screen.title for screen in await preview_screens.build_screens('ru')]
    finally:
        loader.clear_locale_cache()
    assert not any(title.startswith('Приветствие') for title in titles)


@pytest.mark.asyncio
async def test_stage_two_menus_have_one_green_button(theme_on, monkeypatch):
    monkeypatch.setattr(settings, 'BOT_USERNAME', 'zanity_vpn_bot')
    screens = {screen.title: screen for screen in await preview_screens.build_screens('ru')}
    for title in MENU_TITLES:
        buttons = [button for row in screens[title].markup.inline_keyboard for button in row]
        assert sum(1 for button in buttons if button.style == 'success') == 1, title


@pytest.mark.asyncio
async def test_welcome_title_follows_actual_variant(theme_on, monkeypatch):
    monkeypatch.setattr(settings, 'BOT_USERNAME', 'zanity_vpn_bot')
    monkeypatch.setattr(fork_onboarding, 'is_trial_available', lambda user: False)
    screens = {screen.title: screen for screen in await preview_screens.build_screens('ru')}
    assert 'Приветствие — пробный' not in screens
    welcome = screens['Приветствие — без пробного']
    assert welcome.markup.inline_keyboard[0][0].callback_data == 'menu_buy'


@pytest.mark.asyncio
async def test_more_submenu_screen(theme_on):
    screens = {screen.title: screen for screen in await preview_screens.build_screens('ru')}
    more = screens['Меню — Ещё']
    assert not more.rich
    buttons = [button for row in more.markup.inline_keyboard for button in row]
    assert not any(button.style == 'success' for button in buttons)
    assert buttons[-1].callback_data == 'back_to_menu'
    assert {'menu_promocode', 'menu_info'} <= {button.callback_data for button in buttons}
