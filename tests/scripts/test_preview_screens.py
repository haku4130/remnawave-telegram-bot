import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import InlineKeyboardMarkup

from app.config import settings
from app.localization import loader
from app.localization.emoji_catalog import CATALOG
from app.utils import fork_onboarding, rich_menu
from scripts import preview_screens


MENU_TITLES = ('Меню — пробный', 'Меню — подключиться', 'Меню — продлить', 'Меню — тариф')
RICH_TITLES = (*MENU_TITLES, 'Меню — Ещё')


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
    # Состояния различаются по-настоящему: у подписчика есть блок подписки, у новичка — нет.
    assert 'Алексей' in screens['Меню — подключиться'].text
    if multi_tariff:
        # Мультитарифная ветка не менялась: там по-прежнему таблица подписок.
        assert '<table' in screens['Меню — подключиться'].text
    else:
        for title in MENU_TITLES:
            assert '<table' not in screens[title].text, title
        assert 'Подписка активна' in screens['Меню — подключиться'].text
        assert 'Подписка активна' not in screens['Меню — пробный'].text
        assert 'Подписки пока нет' in screens['Меню — пробный'].text


@pytest.mark.asyncio
async def test_menu_keyboard_has_plain_balance_button(theme_on, db_sources_forbidden):
    screens = {screen.title: screen for screen in await preview_screens.build_screens('ru')}
    for title in ('Меню — подключиться', 'Меню — пробный'):
        labels = [button.text for row in screens[title].markup.inline_keyboard for button in row]
        assert '💰 Баланс' in labels, title
        assert not any('₽' in label for label in labels), title
    assert 'Баланс: <b>150 ₽</b>' in screens['Меню — подключиться'].text


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
    bot.edit_message_reply_markup = AsyncMock()
    bot.session.close = AsyncMock()
    monkeypatch.setattr(preview_screens, 'create_bot', lambda *args, **kwargs: bot)
    try:
        sent = await preview_screens.send_screens(42, 'ru')
        assert settings.PREMIUM_THEME_ENABLED is True
        screens = await preview_screens.build_screens('ru')
    finally:
        loader.clear_locale_cache()

    rich = [screen for screen in screens if screen.rich]
    notify = [screen for screen in screens if screen.notify]
    plain = [screen for screen in screens if not screen.rich and not screen.notify]
    assert sent == len(screens)
    assert len(rich) == len(RICH_TITLES)
    assert notify

    # Подписи всех экранов + тела обычных экранов — sendMessage без превью ссылок.
    assert bot.send_message.await_count == len(screens) + len(plain)
    for call in bot.send_message.await_args_list:
        assert call.args[0] == 42
        assert call.kwargs['link_preview_options'].is_disabled is True

    # Меню — через rich-путь меню (с полем эффекта), уведомления — через rich-путь уведомлений.
    rich_calls = bot.send_rich_message.await_args_list
    menu_calls = [call for call in rich_calls if 'message_effect_id' in call.kwargs]
    notify_calls = [call for call in rich_calls if 'message_effect_id' not in call.kwargs]
    assert len(menu_calls) == len(rich)
    for call, screen in zip(menu_calls, rich, strict=True):
        assert call.kwargs['chat_id'] == 42
        assert call.kwargs['message_effect_id'] is None
        assert '<hr/>' in call.kwargs['rich_message'].html
        assert call.kwargs['reply_markup'] == screen.markup
    for call, screen in zip(notify_calls, notify, strict=True):
        assert call.kwargs['chat_id'] == 42
        assert '<hr/>' not in call.kwargs['rich_message'].html
        assert call.kwargs.get('reply_markup') == screen.markup
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
    # Тело и главная клавиатура — как у «подключиться»; подменю приходит заменой кнопок после отправки.
    assert more.rich
    assert more.text == screens['Меню — подключиться'].text
    assert more.markup == screens['Меню — подключиться'].markup
    buttons = [button for row in more.then_markup.inline_keyboard for button in row]
    assert not any(button.style == 'success' for button in buttons)
    assert buttons[-1].callback_data == 'back_to_menu'
    assert {'menu_promocode', 'menu_info'} <= {button.callback_data for button in buttons}
    with_swap = {title for title, screen in screens.items() if screen.then_markup is not None}
    assert with_swap == {'Меню — Ещё', 'Подписка — Ещё'}


@pytest.mark.asyncio
async def test_send_screens_swaps_buttons_of_more_menu_after_sending(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    monkeypatch.setattr(settings, 'MAIN_MENU_RICH_INLINE_BUTTONS', False, raising=False)
    loader.clear_locale_cache()
    sent_ids = iter(range(100, 200))
    plain_ids = iter(range(1000, 2000))
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=lambda *args, **kwargs: MagicMock(message_id=next(plain_ids)))
    bot.send_rich_message = AsyncMock(side_effect=lambda **kwargs: MagicMock(message_id=next(sent_ids)))
    bot.edit_message_reply_markup = AsyncMock()
    bot.session.close = AsyncMock()
    monkeypatch.setattr(preview_screens, 'create_bot', lambda *args, **kwargs: bot)
    try:
        await preview_screens.send_screens(42, 'ru')
        screens = await preview_screens.build_screens('ru')
    finally:
        loader.clear_locale_cache()

    rich = [screen for screen in screens if screen.rich]
    more_index = next(index for index, screen in enumerate(rich) if screen.title == 'Меню — Ещё')
    assert bot.edit_message_reply_markup.await_count == 2
    swaps = {call.kwargs['message_id']: call.kwargs for call in bot.edit_message_reply_markup.await_args_list}
    # id — именно того сообщения, которое было отправлено как «Меню — Ещё».
    kwargs = swaps[100 + more_index]
    assert kwargs['chat_id'] == 42
    assert bot.send_rich_message.await_args_list[more_index].kwargs['chat_id'] == 42
    assert kwargs['reply_markup'].inline_keyboard[-1][0].callback_data == 'back_to_menu'
    assert kwargs['reply_markup'] == rich[more_index].then_markup

    # «Подписка — Ещё» — обычное сообщение: кнопки меняются у тела экрана (не у подписи-названия).
    bodies = [
        call
        for call in bot.send_message.await_args_list
        if not (len(call.args) > 1 and str(call.args[1]).startswith('<i>'))
    ]
    plain = [screen for screen in screens if not screen.rich and not screen.notify]
    sub_index = next(index for index, screen in enumerate(plain) if screen.title == 'Подписка — Ещё')
    message_ids = [1000 + index for index, call in enumerate(bot.send_message.await_args_list) if call in bodies]
    kwargs = swaps[message_ids[sub_index]]
    assert kwargs['chat_id'] == 42
    assert kwargs['reply_markup'] == plain[sub_index].then_markup
    assert kwargs['reply_markup'].inline_keyboard[-1][0].callback_data == 'menu_subscription'


@pytest.mark.asyncio
async def test_send_screens_fails_loudly_when_telegram_rejects_button_swap(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_rich_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot.edit_message_reply_markup = AsyncMock(side_effect=RuntimeError('rejected'))
    bot.session.close = AsyncMock()
    monkeypatch.setattr(preview_screens, 'create_bot', lambda *args, **kwargs: bot)
    try:
        with pytest.raises(RuntimeError, match='rejected'):
            await preview_screens.send_screens(42, 'ru')
    finally:
        loader.clear_locale_cache()
    bot.session.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('language', 'stale', 'expected'),
    [('ru', 'Выберите действие:', 'Выберите действие'), ('en', 'Choose an option:', 'Choose an option')],
)
async def test_send_screens_ignores_stale_server_locales(monkeypatch, tmp_path, language, stale, expected):
    # На msk3 LOCALES_PATH — снимок апстрима: он перекрывает слой форка, и подвал меню приходил
    # с двоеточием апстрима. Предпросмотр показывает тексты, как после выкатки (снимок → {}).
    (tmp_path / f'{language}.json').write_text(json.dumps({'MAIN_MENU_ACTION_PROMPT': stale}), encoding='utf-8')
    monkeypatch.setattr(settings, 'LOCALES_PATH', str(tmp_path))
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    monkeypatch.setattr(settings, 'MAIN_MENU_RICH_INLINE_BUTTONS', False, raising=False)
    loader.clear_locale_cache()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_rich_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot.edit_message_reply_markup = AsyncMock()
    bot.session.close = AsyncMock()
    monkeypatch.setattr(preview_screens, 'create_bot', lambda *args, **kwargs: bot)
    try:
        await preview_screens.send_screens(42, language)
    finally:
        loader.clear_locale_cache()

    assert str(tmp_path) == settings.LOCALES_PATH
    menu_calls = [call for call in bot.send_rich_message.await_args_list if 'message_effect_id' in call.kwargs]
    assert len(menu_calls) == len(RICH_TITLES)
    for call in menu_calls:
        assert f'<footer>{expected}</footer>' in call.kwargs['rich_message'].html


SUBSCRIPTION_TITLES = (
    'Подписка — активна',
    'Подписка — скоро истекает',
    'Подписка — истекла',
    'Подписка — пробная',
    'Подписка — Ещё',
    'Подписка — нет подписки',
)


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_subscription_screens_present(theme_on, monkeypatch, language):
    import re

    from app.middlewares.premium_theme import theme_markup
    from app.utils.message_patch import caption_exceeds_telegram_limit

    monkeypatch.setattr(settings, 'SALES_MODE', 'tariffs')
    monkeypatch.setattr(settings, 'CONNECT_BUTTON_MODE', 'miniapp_subscription')
    monkeypatch.setattr(settings, 'HIDE_SUBSCRIPTION_LINK', False)
    screens = {screen.title: screen for screen in await preview_screens.build_screens(language)}
    for title in SUBSCRIPTION_TITLES:
        screen = screens[title]
        assert not screen.rich, title
        assert '<blockquote' not in screen.text, title
        assert not caption_exceeds_telegram_limit(screen.text), title
        assert screen.text.count('<tg-emoji') <= 5, title
        buttons = [button for row in theme_markup(screen.markup).inline_keyboard for button in row]
        assert sum(1 for button in buttons if button.style == 'success') == 1, title
        if title != 'Подписка — нет подписки':
            assert len(screen.markup.inline_keyboard) == 4, title
        if language == 'en':
            assert not re.search('[а-яА-ЯёЁ]', screen.text), title
            assert not any(re.search('[а-яА-ЯёЁ]', button.text) for button in buttons), title
    expiring = 'скоро истекает' if language == 'ru' else 'expires soon'
    assert expiring in screens['Подписка — скоро истекает'].text
    assert '<code>' not in screens['Подписка — истекла'].text
    assert '<code>' in screens['Подписка — пробная'].text
    callbacks = [b.callback_data for row in screens['Подписка — активна'].markup.inline_keyboard for b in row]
    # Первая — «Подключиться» (web_app, без callback_data).
    assert callbacks == [None, 'subscription_extend', 'subscription_manage_devices', 'sub_more', 'back_to_menu']
    assert screens['Подписка — нет подписки'].markup.inline_keyboard[0][0].callback_data == 'menu_buy'

    more = screens['Подписка — Ещё']
    assert (more.text, more.markup) == (screens['Подписка — активна'].text, screens['Подписка — активна'].markup)
    more_buttons = [button for row in theme_markup(more.then_markup).inline_keyboard for button in row]
    assert more_buttons[0].copy_text is not None and more_buttons[0].copy_text.text.startswith('https://')
    assert [b.callback_data for b in more_buttons[1:]] == [
        'subscription_settings',
        'subscription_gift',
        'menu_subscription',
    ]
    assert all(button.style is None for button in more_buttons)


@pytest.mark.asyncio
async def test_subscription_screens_absent_without_theme(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    try:
        titles = [screen.title for screen in await preview_screens.build_screens('ru')]
    finally:
        loader.clear_locale_cache()
    assert not any(title.startswith('Подписка —') for title in titles)


DEVICE_TITLES = (
    'Настройки подписки',
    'Устройства — выбор количества',
    'Устройства — подтверждение (увеличить)',
    'Устройства — подтверждение (уменьшить)',
    'Устройства — недостаточно средств',
    'Устройства — готово',
    'Устройства — список',
    'Перевыпуск — вопрос',
    'Перевыпуск — готово',
)


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_device_screens_present(theme_on, monkeypatch, language):
    import re

    from app.middlewares.premium_theme import theme_markup
    from app.utils.message_patch import caption_exceeds_telegram_limit

    monkeypatch.setattr(settings, 'SALES_MODE', 'tariffs')
    screens = {screen.title: screen for screen in await preview_screens.build_screens(language)}
    for title in DEVICE_TITLES:
        screen = screens[title]
        assert not screen.rich, title
        assert '<blockquote' not in screen.text, title
        assert not caption_exceeds_telegram_limit(screen.text), title
        assert screen.text.count('<tg-emoji') <= 2, title
        buttons = [button for row in theme_markup(screen.markup).inline_keyboard for button in row]
        assert sum(1 for button in buttons if button.style == 'success') <= 1, title
        if language == 'en':
            assert not re.search('[а-яА-ЯёЁ]', screen.text.replace('₽', '')), title
            assert not any(re.search('[а-яА-ЯёЁ]', button.text) for button in buttons), title
    for title in ('Устройства — подтверждение (увеличить)', 'Перевыпуск — готово'):
        buttons = [b for row in theme_markup(screens[title].markup).inline_keyboard for b in row]
        assert sum(1 for button in buttons if button.style == 'success') == 1, title
    list_buttons = [b for row in theme_markup(screens['Устройства — список'].markup).inline_keyboard for b in row]
    assert [b.style for b in list_buttons if b.callback_data == 'reset_all_devices'] == ['danger']
    # «Добавить устройство» переехала с экрана подписки на список устройств — синяя, над «Назад».
    assert list_buttons[-2].callback_data == 'subscription_change_devices'
    assert list_buttons[-2].style == 'primary'


@pytest.mark.asyncio
async def test_device_screens_absent_without_theme(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    try:
        titles = [screen.title for screen in await preview_screens.build_screens('ru')]
    finally:
        loader.clear_locale_cache()
    assert not any(title in titles for title in DEVICE_TITLES)


EXTEND_TITLES = (
    'Продление — выбор срока',
    'Продление — подтверждение',
    'Продление — не хватает средств',
    'Продление — готово',
    'Смена тарифа — нет вариантов',
)


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_extend_screens_present(theme_on, monkeypatch, language):
    import re

    from app.middlewares.premium_theme import theme_markup
    from app.utils.message_patch import caption_exceeds_telegram_limit

    monkeypatch.setattr(settings, 'SALES_MODE', 'tariffs')
    screens = {screen.title: screen for screen in await preview_screens.build_screens(language)}
    for title in EXTEND_TITLES:
        screen = screens[title]
        assert not screen.rich, title
        assert screen.text.startswith('<b>'), title
        assert '<blockquote' not in screen.text, title
        assert not caption_exceeds_telegram_limit(screen.text), title
        assert screen.text.count('<tg-emoji') <= 2, title
        buttons = [button for row in theme_markup(screen.markup).inline_keyboard for button in row]
        assert sum(1 for button in buttons if button.style == 'success') <= 1, title
        if language == 'en':
            assert not re.search('[а-яА-ЯёЁ]', screen.text.replace('₽', '')), title
            assert not any(re.search('[а-яА-ЯёЁ]', button.text.replace('₽', '')) for button in buttons), title
    for title in ('Продление — подтверждение', 'Продление — не хватает средств'):
        buttons = [b for row in theme_markup(screens[title].markup).inline_keyboard for b in row]
        assert sum(1 for button in buttons if button.style == 'success') == 1, title
    assert 'tariff_extend:7:1:30' in [
        b.callback_data for row in screens['Продление — выбор срока'].markup.inline_keyboard for b in row
    ]
    assert re.search(r'\d{2}\.\d{2}\.\d{4}', screens['Продление — готово'].text)


@pytest.mark.asyncio
async def test_extend_screens_absent_without_theme(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    try:
        titles = [screen.title for screen in await preview_screens.build_screens('ru')]
    finally:
        loader.clear_locale_cache()
    assert not any(title in titles for title in EXTEND_TITLES)


PURCHASE_TITLES = (
    'Покупка — список тарифов',
    'Покупка — выбор срока',
    'Покупка — подтверждение',
    'Покупка — не хватает средств',
    'Покупка — готово',
)


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_purchase_screens_present(theme_on, monkeypatch, language):
    import re

    from app.middlewares.premium_theme import theme_markup
    from app.utils.message_patch import caption_exceeds_telegram_limit

    monkeypatch.setattr(settings, 'SALES_MODE', 'tariffs')
    monkeypatch.setattr(settings, 'CONNECT_BUTTON_MODE', 'miniapp_subscription')
    screens = {screen.title: screen for screen in await preview_screens.build_screens(language)}
    for title in PURCHASE_TITLES:
        screen = screens[title]
        assert not screen.rich, title
        assert screen.text.startswith('<b>'), title
        assert '<blockquote' not in screen.text, title
        assert not caption_exceeds_telegram_limit(screen.text), title
        assert screen.text.count('<tg-emoji') <= 2, title
        buttons = [button for row in theme_markup(screen.markup).inline_keyboard for button in row]
        assert sum(1 for button in buttons if button.style == 'success') <= 1, title
        if language == 'en':
            assert not re.search('[а-яА-ЯёЁ]', screen.text.replace('₽', '')), title
            assert not any(re.search('[а-яА-ЯёЁ]', button.text.replace('₽', '')) for button in buttons), title
    for title in PURCHASE_TITLES[2:]:
        buttons = [b for row in theme_markup(screens[title].markup).inline_keyboard for b in row]
        assert sum(1 for button in buttons if button.style == 'success') == 1, title
    assert 'tariff_period:1:30' in [
        b.callback_data for row in screens['Покупка — выбор срока'].markup.inline_keyboard for b in row
    ]
    success = screens['Покупка — готово']
    assert re.search(r'\d{2}\.\d{2}\.\d{4}', success.text)
    assert success.markup.inline_keyboard[0][0].web_app is not None


@pytest.mark.asyncio
async def test_purchase_screens_absent_without_theme(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    try:
        titles = [screen.title for screen in await preview_screens.build_screens('ru')]
    finally:
        loader.clear_locale_cache()
    assert not any(title in titles for title in PURCHASE_TITLES)


BALANCE_TITLES = (
    'Баланс',
    'Баланс — история',
    'Баланс — способы пополнения',
    'Пополнение — ввод суммы',
    'Пополнение — ссылка на оплату',
    'Пополнение — оплата прошла',
    'Пополнение — через поддержку',
)


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_balance_screens_present(theme_on, monkeypatch, language):
    import re

    from app.middlewares.premium_theme import theme_markup
    from app.utils.message_patch import caption_exceeds_telegram_limit

    monkeypatch.setattr(settings, 'TELEGRAM_STARS_ENABLED', False)
    screens = {screen.title: screen for screen in await preview_screens.build_screens(language)}
    for title in BALANCE_TITLES:
        screen = screens[title]
        assert not screen.rich, title
        assert screen.text.startswith('<b>'), title
        assert '<blockquote' not in screen.text, title
        assert not caption_exceeds_telegram_limit(screen.text), title
        assert screen.text.count('<tg-emoji') <= 2, title
        buttons = [button for row in theme_markup(screen.markup).inline_keyboard for button in row]
        assert sum(1 for button in buttons if button.style == 'success') <= 1, title
        if language == 'en':
            assert not re.search('[а-яА-ЯёЁ]', screen.text.replace('₽', '')), title
            assert not any(re.search('[а-яА-ЯёЁ]', button.text.replace('₽', '')) for button in buttons), title

    def callbacks(title):
        return [b.callback_data for row in screens[title].markup.inline_keyboard for b in row]

    assert 'balance_topup' in callbacks('Баланс')
    assert callbacks('Баланс — способы пополнения')[:2] == ['topup_rollypay', 'topup_support']
    assert 'topup_amount|rollypay|50000' in callbacks('Пополнение — ввод суммы')
    pay = screens['Пополнение — ссылка на оплату'].markup.inline_keyboard[0][0]
    assert pay.url and pay.style == 'success'
    assert 'К оплате 500 ₽' in screens['Пополнение — ссылка на оплату'].text or 'To pay: 500 ₽' in (
        screens['Пополнение — ссылка на оплату'].text
    )
    for title in (
        'Баланс',
        'Баланс — способы пополнения',
        'Пополнение — ссылка на оплату',
        'Пополнение — оплата прошла',
    ):
        buttons = [b for row in theme_markup(screens[title].markup).inline_keyboard for b in row]
        assert sum(1 for button in buttons if button.style == 'success') == 1, title
    assert 'Транзакц' not in screens['Пополнение — оплата прошла'].text


@pytest.mark.asyncio
async def test_balance_screens_absent_without_theme(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    try:
        titles = [screen.title for screen in await preview_screens.build_screens('ru')]
    finally:
        loader.clear_locale_cache()
    assert not any(title in titles for title in BALANCE_TITLES)


TOPUP_NOTIFICATION_TITLES = (
    'После пополнения — подписка оформлена',
    'После пополнения — подписка продлена',
    'После пополнения — устройства добавлены',
    'После пополнения — трафик добавлен',
)


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_topup_notification_screens_present(theme_on, language):
    import re

    from app.middlewares.premium_theme import theme_markup

    screens = {screen.title: screen for screen in await preview_screens.build_screens(language)}
    for title in TOPUP_NOTIFICATION_TITLES:
        screen = screens[title]
        assert not screen.rich, title
        assert screen.text.startswith('<b>'), title
        assert '<blockquote' not in screen.text, title
        assert screen.text.count('<tg-emoji') == 1, title
        assert len(re.sub('<[^>]+>', '', screen.text)) <= 1024, title
        buttons = [button for row in theme_markup(screen.markup).inline_keyboard for button in row]
        assert sum(1 for button in buttons if button.style == 'success') == 1, title
        assert buttons[0].web_app is not None, title
        if language == 'en':
            assert not re.search('[а-яА-ЯёЁ]', re.sub('<[^>]+>', '', screen.text)), title
            assert not any(re.search('[а-яА-ЯёЁ]', button.text) for button in buttons), title


@pytest.mark.asyncio
async def test_topup_notification_screens_absent_without_theme(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    try:
        titles = [screen.title for screen in await preview_screens.build_screens('ru')]
    finally:
        loader.clear_locale_cache()
    assert not any(title in titles for title in TOPUP_NOTIFICATION_TITLES)


MONITORING_NOTIFICATION_TITLES = (
    'Уведомление — подписка закончилась',
    'Уведомление — пробный закончился',
    'Уведомление — скоро закончится',
    'Уведомление — пробный через 2 часа',
    'Уведомление — день после окончания',
    'Уведомление — скидка, 2-я волна',
    'Уведомление — скидка, 3-я волна',
    'Уведомление — автоплатёж прошёл',
    'Уведомление — автоплатёж не прошёл',
)
WEBHOOK_NOTIFICATION_TITLES = (
    'Вебхук — ещё не подключились',
    'Вебхук — первое подключение',
    'Вебхук — закончится через 3 дня',
    'Вебхук — закончится завтра',
    'Вебхук — подписка закончилась',
    'Вебхук — новое устройство',
    'Вебхук — израсходовано 80% трафика',
    'Вебхук — ссылка обновлена',
)


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_notification_screens_present_and_go_rich(theme_on, language):
    import re

    from app.middlewares.premium_theme import theme_markup
    from app.utils.rich_notify import build_notification_rich_html

    screens = {screen.title: screen for screen in await preview_screens.build_screens(language)}
    for title in (*MONITORING_NOTIFICATION_TITLES, *WEBHOOK_NOTIFICATION_TITLES):
        screen = screens[title]
        assert screen.notify and not screen.rich, title
        assert screen.text.startswith('<b><tg-emoji '), title
        assert screen.text.count('<tg-emoji') == 1, title
        # Тот же сборщик rich-HTML, что у бота: None означало бы отправку классикой.
        assert build_notification_rich_html(screen.text) is not None, title
        if screen.markup is not None:
            buttons = [button for row in theme_markup(screen.markup).inline_keyboard for button in row]
            assert sum(1 for button in buttons if button.style == 'success') == 1, title
            if language == 'en':
                assert not any(re.search('[а-яА-ЯёЁ]', button.text) for button in buttons), title
        if language == 'en':
            assert not re.search('[а-яА-ЯёЁ]', re.sub('<[^>]+>', '', screen.text)), title
    traffic = screens['Уведомление — трафик почти закончился']
    assert not traffic.notify and traffic.text.startswith('<b><tg-emoji ')


@pytest.mark.asyncio
async def test_notification_screens_absent_without_theme(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    try:
        screens = await preview_screens.build_screens('ru')
    finally:
        loader.clear_locale_cache()
    titles = [screen.title for screen in screens]
    assert not any(title in titles for title in (*MONITORING_NOTIFICATION_TITLES, *WEBHOOK_NOTIFICATION_TITLES))
    assert not any(screen.notify for screen in screens)


REFERRAL_TITLES = (
    'Рефералы — главный экран',
    'Рефералы — список',
    'Рефералы — пустой список',
    'Рефералы — аналитика',
    'Рефералы — приглашение',
    'Рефералы — QR (подпись)',
    'Реферал — приглашённому: добро пожаловать',
    'Реферал — пригласившему: новый реферал',
    'Реферал — приглашённому: бонус',
    'Реферал — пригласившему: награда за первое пополнение',
    'Реферал — пригласившему: комиссия',
)


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_referral_screens_present(theme_on, language):
    import re

    from app.middlewares.premium_theme import theme_markup
    from app.utils.message_patch import caption_exceeds_telegram_limit

    screens = {screen.title: screen for screen in await preview_screens.build_screens(language)}
    for title in REFERRAL_TITLES:
        screen = screens[title]
        assert not screen.rich, title
        assert screen.text.startswith('<b>'), title
        assert screen.text.count('<tg-emoji') <= 2, title
        assert not caption_exceeds_telegram_limit(screen.text), title
        buttons = (
            [button for row in theme_markup(screen.markup).inline_keyboard for button in row] if screen.markup else []
        )
        assert sum(1 for button in buttons if button.style == 'success') <= 1, title
        if language == 'en':
            plain = re.sub('<[^>]+>', '', screen.text).replace('₽', '')
            assert not re.search('[а-яА-ЯёЁ]', plain), title
            assert not any(re.search('[а-яА-ЯёЁ]', button.text) for button in buttons), title

    main = [b for row in screens['Рефералы — главный экран'].markup.inline_keyboard for b in row]
    assert main[0].callback_data == 'referral_create_invite' and main[0].style == 'success'
    assert 'ZNT7K2' in screens['Рефералы — главный экран'].text
    assert 'tg-emoji' not in screens['Рефералы — приглашение'].text.split('<blockquote>')[1]


@pytest.mark.asyncio
async def test_referral_screens_absent_without_theme(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    try:
        titles = [screen.title for screen in await preview_screens.build_screens('ru')]
    finally:
        loader.clear_locale_cache()
    assert not any(title in titles for title in REFERRAL_TITLES)


SUPPORT_TITLES = (
    'Поддержка — главный экран',
    'Обращение — тема',
    'Обращение — описание',
    'Обращение — создано',
    'Обращения — список',
    'Обращения — пусто',
    'Обращения — закрытые',
    'Обращение — карточка',
    'Обращение — карточка (закрыто)',
    'Обращение — ответ (ввод)',
    'Обращение — ответ отправлен',
    'Обращение — ответила поддержка (уведомление)',
)


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_support_screens_present(theme_on, language):
    import re

    from app.middlewares.premium_theme import theme_markup
    from app.utils.message_patch import caption_exceeds_telegram_limit

    screens = {screen.title: screen for screen in await preview_screens.build_screens(language)}
    for title in SUPPORT_TITLES:
        screen = screens[title]
        assert not screen.rich, title
        assert screen.text.startswith('<b>'), title
        assert screen.text.count('<tg-emoji') <= 2, title
        assert not caption_exceeds_telegram_limit(screen.text), title
        assert '{' not in screen.text, title
        buttons = (
            [button for row in theme_markup(screen.markup).inline_keyboard for button in row] if screen.markup else []
        )
        assert sum(1 for button in buttons if button.style == 'success') <= 1, title
        if language == 'en':
            assert not re.search('[а-яА-ЯёЁ]', re.sub('<[^>]+>', '', screen.text)), title
            assert not any(re.search('[а-яА-ЯёЁ]', button.text) for button in buttons), title

    support = [b for row in screens['Поддержка — главный экран'].markup.inline_keyboard for b in row]
    assert support[0].callback_data == 'create_ticket' and support[0].style == 'success'
    card = [b for row in screens['Обращение — карточка'].markup.inline_keyboard for b in row]
    assert card[0].callback_data == 'reply_ticket_17' and card[0].style == 'success'
    assert 'reply_ticket_17' not in [
        b.callback_data for row in screens['Обращение — карточка (закрыто)'].markup.inline_keyboard for b in row
    ]


@pytest.mark.asyncio
async def test_support_screens_absent_without_theme(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    try:
        titles = [screen.title for screen in await preview_screens.build_screens('ru')]
    finally:
        loader.clear_locale_cache()
    assert not any(title in titles for title in SUPPORT_TITLES)


MISC_TITLES = (
    'Инфо — меню',
    'Язык — выбор',
    'Язык — при регистрации',
    'Язык — выбран',
    'Промокод — ввод',
    'Промокод — активирован',
    'Промокод — не найден',
    'Промокод — слишком много попыток',
    'Подарок — получен',
    'Подарок — активирован',
    'Подарок — свой собственный',
    'Ошибка — не понял команду',
    'Ошибка — что-то пошло не так',
)
# Подпись выбора языка при регистрации двуязычная, а кнопки выбора языка подписаны родными названиями («Русский»).
BILINGUAL_TITLES = ('Язык — при регистрации',)
NATIVE_NAME_BUTTONS = ('Язык — выбор', 'Язык — при регистрации')


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_misc_screens_present(theme_on, language):
    import re

    from app.middlewares.premium_theme import theme_markup
    from app.utils.message_patch import caption_exceeds_telegram_limit

    screens = {screen.title: screen for screen in await preview_screens.build_screens(language)}
    for title in MISC_TITLES:
        screen = screens[title]
        assert not screen.rich, title
        assert screen.text.count('<tg-emoji') <= 2, title
        assert not caption_exceeds_telegram_limit(screen.text), title
        assert '{' not in screen.text, title
        buttons = (
            [button for row in theme_markup(screen.markup).inline_keyboard for button in row] if screen.markup else []
        )
        assert sum(1 for button in buttons if button.style == 'success') <= 1, title
        if language == 'en' and title not in BILINGUAL_TITLES:
            assert not re.search('[а-яА-ЯёЁ]', re.sub('<[^>]+>', '', screen.text)), title
        if language == 'en' and title not in NATIVE_NAME_BUTTONS:
            assert not any(re.search('[а-яА-ЯёЁ]', button.text) for button in buttons), title

    gift = [b for row in screens['Подарок — получен'].markup.inline_keyboard for b in row]
    assert [b.callback_data for b in gift] == ['gift_activate:9'] and gift[0].style == 'success'
    assert '150 ₽' in screens['Промокод — активирован'].text


@pytest.mark.asyncio
async def test_misc_screens_absent_without_theme(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    try:
        titles = [screen.title for screen in await preview_screens.build_screens('ru')]
    finally:
        loader.clear_locale_cache()
    assert not any(title in titles for title in MISC_TITLES)


# --- Фильтр --only ------------------------------------------------------------------------


def test_parse_only_splits_and_normalizes():
    assert preview_screens.parse_only(None) == []
    assert preview_screens.parse_only('') == []
    assert preview_screens.parse_only(' Подписка —, ,Устройства — СПИСОК ') == ['подписка —', 'устройства — список']


def test_select_screens_matches_substrings_case_insensitively():
    screens = [
        preview_screens.PreviewScreen(title, 'text', None)
        for title in ('Подписка — активна', 'Подписка — Ещё', 'Устройства — список', 'Меню — Ещё')
    ]
    assert preview_screens.select_screens(screens, None) == screens
    assert preview_screens.select_screens(screens, []) == screens
    picked = preview_screens.select_screens(screens, ['ПОДПИСКА', 'устройства — список'])
    assert [screen.title for screen in picked] == ['Подписка — активна', 'Подписка — Ещё', 'Устройства — список']
    assert [screen.title for screen in preview_screens.select_screens(screens, ['ещё'])] == [
        'Подписка — Ещё',
        'Меню — Ещё',
    ]
    assert preview_screens.select_screens(screens, ['нет такого']) == []


@pytest.mark.asyncio
async def test_send_screens_only_sends_matching_screens(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot.send_rich_message = AsyncMock(return_value=MagicMock(message_id=2))
    bot.edit_message_reply_markup = AsyncMock()
    bot.session.close = AsyncMock()
    monkeypatch.setattr(preview_screens, 'create_bot', lambda *args, **kwargs: bot)
    try:
        sent = await preview_screens.send_screens(42, 'ru', ['подписка —', 'Устройства — список'])
    finally:
        loader.clear_locale_cache()

    expected = [*SUBSCRIPTION_TITLES, 'Устройства — список']
    assert sent == len(expected)
    captions = [call.args[1] for call in bot.send_message.await_args_list if str(call.args[1]).startswith('<i>')]
    assert captions == [f'<i>{title}</i>' for title in expected]
    # Все выбранные экраны — обычные сообщения: подпись + тело; rich-меню не ушло.
    assert bot.send_message.await_count == 2 * len(expected)
    bot.send_rich_message.assert_not_awaited()
    # Подменю «Ещё» подписки показано заменой кнопок.
    bot.edit_message_reply_markup.assert_awaited_once()


def test_main_passes_only_filter(monkeypatch):
    seen = {}

    async def fake_send(chat_id, language, only=None):
        seen.update(chat_id=chat_id, language=language, only=only)
        return 0

    monkeypatch.setattr(preview_screens, 'send_screens', fake_send)
    monkeypatch.setattr(
        'sys.argv', ['preview_screens', '--to', '42', '--lang', 'en', '--only', 'Подписка —,Устройства — список']
    )
    preview_screens.main()
    assert seen == {'chat_id': 42, 'language': 'en', 'only': ['подписка —', 'устройства — список']}
    monkeypatch.setattr('sys.argv', ['preview_screens', '--to', '42'])
    preview_screens.main()
    assert seen['only'] == []
