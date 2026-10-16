"""Экран «Подписка» под темой форка: короткий текст, кнопка «Добавить устройство».

Без темы экран обязан остаться байт-в-байт как у апстрима — снимок ниже снят с кода
до переписывания. Под темой текст собирает purchase._build_fork_subscription_text,
клавиатуру — get_subscription_keyboard (ветка форка).
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.keyboards.inline as kb
from app.config import settings
from app.database.crud import subscription as subscription_crud, tariff as tariff_crud
from app.handlers.subscription import purchase
from app.localization import loader
from app.localization.texts import get_texts
from app.middlewares.premium_theme import theme_markup
from app.services import remnawave_service
from app.utils.message_patch import caption_exceeds_telegram_limit


NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
LINK = 'https://sub.example/abc?x=1&y=2'
DEVICES = [
    {'platform': 'macOS', 'deviceModel': 'Mac'},
    {'platform': 'iOS', 'deviceModel': 'iPhone 15'},
]
TAG_RE = re.compile(r'<[^>]+>')


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


def _tariff(**overrides) -> SimpleNamespace:
    data = {
        'id': 1,
        'name': 'Стандартный',
        'is_daily': False,
        'is_free': False,
        'traffic_limit_gb': 0,
        'device_limit': 3,
        'device_price_kopeks': 5500,
        'can_topup_traffic': lambda: False,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _subscription(days: int = 8, **overrides) -> SimpleNamespace:
    data = {
        'id': 7,
        'status': 'active',
        'actual_status': 'active',
        'is_trial': False,
        'end_date': NOW + timedelta(days=days, hours=3),
        'days_left': days,
        'traffic_used_gb': 0.0,
        'traffic_limit_gb': 0,
        'connected_squads': ['squad'],
        'tariff_id': 1,
        'tariff': None,
        'device_limit': 3,
        'is_daily_paused': False,
        'subscription_url': LINK,
        'subscription_crypto_link': None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _plain(text: str) -> str:
    return TAG_RE.sub('', text)


@pytest.fixture
def prod_like(monkeypatch):
    monkeypatch.setattr(settings, 'SALES_MODE', 'tariffs')
    monkeypatch.setattr(settings, 'MULTI_TARIFF_ENABLED', False, raising=False)
    monkeypatch.setattr(type(settings), 'is_multi_tariff_enabled', lambda self: False)
    monkeypatch.setattr(settings, 'CONNECT_BUTTON_MODE', 'miniapp_subscription')
    monkeypatch.setattr(settings, 'DEVICES_SELECTION_ENABLED', True)
    monkeypatch.setattr(settings, 'HIDE_SUBSCRIPTION_LINK', False)


@pytest.fixture
def theme_on(monkeypatch, prod_like):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    loader.clear_locale_cache()
    yield
    loader.clear_locale_cache()


@pytest.fixture
def theme_off(monkeypatch, prod_like):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    yield
    loader.clear_locale_cache()


@pytest.fixture
def handler_env(monkeypatch):
    """Всё, куда обработчик ходит за данными, — заглушки; время заморожено."""
    state = {'devices': {'total': len(DEVICES), 'devices': DEVICES}, 'tariff': _tariff(), 'purchases': []}

    async def check_status(db, subscription):
        return subscription

    service = MagicMock()
    service.sync_subscription_usage = AsyncMock()
    service.ensure_subscription_synced = AsyncMock(return_value=(True, None))

    class _Api:
        async def get_user_devices(self, uuid):
            return state['devices']

    class _RemnaWave:
        def get_api_client(self):
            @asynccontextmanager
            async def client():
                yield _Api()

            return client()

    async def tariff_by_id(db, tariff_id):
        return state['tariff']

    async def servers(squads):
        return 'Нидерланды'

    monkeypatch.setattr(purchase, 'datetime', _FrozenDatetime)
    monkeypatch.setattr(subscription_crud, 'check_and_update_subscription_status', check_status)
    monkeypatch.setattr(purchase, 'SubscriptionService', lambda: service)
    monkeypatch.setattr(remnawave_service, 'RemnaWaveService', _RemnaWave)
    monkeypatch.setattr(tariff_crud, 'get_tariff_by_id', tariff_by_id)
    monkeypatch.setattr(purchase, 'get_servers_display_names', servers)
    monkeypatch.setattr(purchase, 'format_local_datetime', lambda value, fmt: value.strftime(fmt))
    return state


def _db(purchases=()):
    db = MagicMock()
    db.refresh = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(purchases)
    db.execute = AsyncMock(return_value=result)
    return db


def _user(subscription, language: str = 'ru') -> SimpleNamespace:
    return SimpleNamespace(
        language=language,
        subscription=subscription,
        full_name='Алексей <b>',
        balance_kopeks=15000,
        remnawave_id='uuid-1',
        telegram_id=42,
    )


def _callback():
    callback = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    return callback


async def _show(subscription, language: str = 'ru', purchases=()):
    callback = _callback()
    await purchase.show_subscription_info(callback, _user(subscription, language), _db(purchases))
    call = callback.message.edit_text.await_args
    return (call.args[0] if call.args else call.kwargs['text']), call.kwargs['reply_markup']


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def _callbacks(markup):
    return [button.callback_data for button in _buttons(markup) if button.callback_data]


# --- Без темы: как у апстрима -------------------------------------------------------------

# Снято с кода до переписывания (7a3f21b3), тема выключена.
UPSTREAM_ACTIVE = (
    '👤 Алексей &lt;b&gt;\n'
    '💰 Баланс: 150 ₽\n'
    '📱 Подписка: 💎 Активна\n'
    '<blockquote expandable><b>📦 Стандартный</b>\n'
    'Тип: 📅 Периодный\n'
    'Трафик: ∞ Безлимит\n'
    'Устройства: 3</blockquote>\n'
    '\n'
    '📱 Информация о подписке\n'
    '🎭 Тип: Платная\n'
    '📅 Действует до: 25.10.2026 15:00\n'
    '⏰ Осталось: 8 дн.\n'
    '📈 Трафик: ∞ (безлимит) | Использовано: 0.0 ГБ\n'
    '🌍 Серверы: Нидерланды\n'
    '📱 Устройства: 2 / 3\n'
    '\n'
    '<blockquote>📱 <b>Подключенные устройства:</b>\n'
    '• macOS - Mac\n'
    '• iOS - iPhone 15\n'
    '</blockquote>\n'
    '\n'
    '🔗 <b>Ссылка для подключения:</b>\n'
    '<code>https://sub.example/abc?x=1&y=2</code>\n'
    '\n'
    '📱 Скопируйте ссылку и добавьте в ваше VPN приложение'
)
UPSTREAM_NONE = '❌ Нет активной подписки'


@pytest.mark.asyncio
async def test_theme_off_screen_matches_upstream_snapshot(theme_off, handler_env):
    text, markup = await _show(_subscription())
    assert text == UPSTREAM_ACTIVE
    assert [[button.text for button in row] for row in markup.inline_keyboard] == [
        ['🔗 Подключиться'],
        ['⏰ Продлить подписку', '💳 Автоплатёж'],
        ['⚙️ Настройки', '📦 Тариф'],
        ['🎁 Подарить подписку'],
        ['⬅️ Назад'],
    ]
    assert all(button.style is None for button in _buttons(markup))


@pytest.mark.asyncio
async def test_theme_off_no_subscription_matches_upstream(theme_off, handler_env):
    text, markup = await _show(None)
    assert text == UPSTREAM_NONE
    assert _callbacks(markup) == ['subscription_gift', 'back_to_menu']


# --- Под темой: текст ---------------------------------------------------------------------


def _tag(name: str) -> str:
    from app.localization.emoji_catalog import tag

    return tag(name)


@pytest.mark.asyncio
async def test_theme_on_active_screen_ru(theme_on, handler_env):
    text, _ = await _show(_subscription())
    assert text == (
        f'<b>{_tag("check")} Подписка активна</b>\n'
        'Стандартный · до 25.10.2026, осталось 8 дней\n'
        'Трафик: 0 ГБ из ∞ · Устройства: 2 из 3\n'
        'Подключено: macOS — Mac, iOS — iPhone 15\n'
        'Ссылка для приложения (нажмите, чтобы скопировать):\n'
        '<code>https://sub.example/abc?x=1&amp;y=2</code>'
    )


@pytest.mark.asyncio
async def test_theme_on_active_screen_en(theme_on, handler_env):
    handler_env['tariff'] = _tariff(name='Standard')
    text, _ = await _show(_subscription(traffic_used_gb=12.34), language='en')
    assert text == (
        f'<b>{_tag("check")} Subscription active</b>\n'
        'Standard · until 25.10.2026, 8 days left\n'
        'Traffic: 12.3 GB of ∞ · Devices: 2 of 3\n'
        'Connected: macOS — Mac, iOS — iPhone 15\n'
        'Link for the app (tap to copy):\n'
        '<code>https://sub.example/abc?x=1&amp;y=2</code>'
    )
    assert not re.search('[а-яА-ЯёЁ]', text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('language', 'status', 'period'),
    [
        ('ru', 'Подписка скоро истекает', 'Стандартный · до 19.10.2026, осталось 2 дня'),
        ('en', 'Subscription expires soon', 'Стандартный · until 19.10.2026, 2 days left'),
    ],
)
async def test_theme_on_expiring_screen(theme_on, handler_env, language, status, period):
    text, _ = await _show(_subscription(days=2), language=language)
    lines = text.split('\n')
    assert lines[0] == f'<b>{_tag("hourglass")} {status}</b>'
    assert lines[1] == period
    # Отдельных предупреждений апстрима («⚠️ истекает завтра!») больше нет — их заменил статус.
    assert '⚠️' not in text and '🔴' not in text


@pytest.mark.asyncio
async def test_theme_on_last_day(theme_on, handler_env):
    text, _ = await _show(_subscription(days=0, end_date=NOW + timedelta(hours=5)))
    assert text.split('\n')[1] == 'Стандартный · до 17.10.2026, осталось меньше суток'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('language', 'status'),
    [('ru', 'Пробный доступ'), ('en', 'Free trial')],
)
async def test_theme_on_trial_screen(theme_on, handler_env, language, status):
    handler_env['tariff'] = None
    # Пробная в БД — status='active' с is_trial (actual_status модели тоже 'active').
    subscription = _subscription(days=20, is_trial=True, tariff_id=None)
    text, markup = await _show(subscription, language=language)
    lines = text.split('\n')
    assert lines[0] == f'<b>{_tag("gift")} {status}</b>'
    # Без тарифа строка срока начинается с большой буквы.
    assert lines[1] == ('До 06.11.2026, осталось 20 дней' if language == 'ru' else 'Until 06.11.2026, 20 days left')
    assert '<code>https://sub.example/abc?x=1&amp;y=2</code>' in text
    assert 'subscription_change_devices' not in _callbacks(markup)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('language', 'status', 'period'),
    [
        ('ru', 'Подписка истекла', 'Стандартный · закончилась 09.10.2026'),
        ('en', 'Subscription expired', 'Стандартный · ended 09.10.2026'),
    ],
)
async def test_theme_on_expired_screen(theme_on, handler_env, language, status, period):
    subscription = _subscription(days=0, end_date=NOW - timedelta(days=8), status='expired', actual_status='expired')
    text, markup = await _show(subscription, language=language)
    # Ни трафика, ни устройств, ни ссылки — подписка закончилась.
    assert text.split('\n') == [f'<b>{_tag("cross")} {status}</b>', period]
    buttons = _buttons(theme_markup(markup))
    green = [button.callback_data for button in buttons if button.style == 'success']
    assert green == ['subscription_extend']
    assert 'subscription_change_devices' not in _callbacks(markup)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('language', 'expected'),
    [
        ('ru', 'Подписки нет</b>\nВыберите тариф — подключение займёт пару минут.'),
        ('en', 'No subscription</b>\nChoose a plan — connecting takes a couple of minutes.'),
    ],
)
async def test_theme_on_no_subscription(theme_on, handler_env, language, expected):
    text, markup = await _show(None, language=language)
    assert text == f'<b>{_tag("cross")} {expected}'
    assert _callbacks(markup) == ['menu_buy', 'subscription_gift', 'back_to_menu']
    buttons = _buttons(theme_markup(markup))
    assert [button.callback_data for button in buttons if button.style == 'success'] == ['menu_buy']


@pytest.mark.asyncio
@pytest.mark.parametrize('days', [8, 2])
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_theme_on_screen_has_no_upstream_clutter(theme_on, handler_env, days, language):
    text, _ = await _show(_subscription(days=days), language=language)
    for clutter in ('<blockquote', 'Серверы', 'Servers', 'Нидерланды', 'Баланс', 'Balance', 'Алексей', '<h'):
        assert clutter not in text, clutter
    # Анимированный эмодзи — один, в заголовке.
    assert text.count('<tg-emoji') == 1
    assert text.startswith('<b><tg-emoji')


@pytest.mark.asyncio
async def test_theme_on_devices_selection_off_drops_devices(theme_on, handler_env, monkeypatch):
    # Review Focus 2: апстрим вырезает «📱 Устройства: …» точной подстрокой; под темой части просто нет.
    monkeypatch.setattr(settings, 'DEVICES_SELECTION_ENABLED', False)
    text, _ = await _show(_subscription())
    assert text.split('\n')[2] == 'Трафик: 0 ГБ из ∞'
    assert 'Устройства' not in text and 'Подключено' not in text


@pytest.mark.asyncio
async def test_theme_on_link_hidden_by_setting(theme_on, handler_env, monkeypatch):
    monkeypatch.setattr(settings, 'HIDE_SUBSCRIPTION_LINK', True)
    text, _ = await _show(_subscription())
    assert '<code>' not in text and 'Ссылка' not in text


@pytest.mark.asyncio
async def test_theme_on_purchased_traffic_is_one_line(theme_on, handler_env):
    purchases = [
        SimpleNamespace(traffic_gb=10, expires_at=NOW + timedelta(days=5), created_at=NOW - timedelta(days=25)),
        SimpleNamespace(traffic_gb=20, expires_at=NOW + timedelta(days=20), created_at=NOW - timedelta(days=10)),
    ]
    text, _ = await _show(_subscription(traffic_limit_gb=50, traffic_used_gb=7.5), purchases=purchases)
    lines = text.split('\n')
    assert lines[2] == 'Трафик: 7.5 ГБ из 50 ГБ · Устройства: 2 из 3'
    assert lines[3] == 'Докуплено: 30 ГБ до 22.10.2026'
    assert '▰' not in text


@pytest.mark.asyncio
async def test_theme_on_device_names_escaped_and_caption_fits(theme_on, handler_env):
    long_devices = [{'platform': f'<Android {i}>', 'deviceModel': 'Very Long Device Model Name ' * 4} for i in range(7)]
    handler_env['devices'] = {'total': 7, 'devices': long_devices}
    handler_env['tariff'] = _tariff(name='Очень длинное название тарифа ' * 3)
    long_link = 'https://sub.example/' + 'x' * 200
    text, _ = await _show(_subscription(days=2, subscription_url=long_link, device_limit=10))
    assert '<Android' not in text and '&lt;Android 0&gt;' in text
    connected = next(line for line in text.split('\n') if line.startswith('Подключено:'))
    assert connected.count('&lt;Android') == 5
    assert not caption_exceeds_telegram_limit(text)
    assert len(_plain(text)) <= 1024


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('subscription', 'days'),
    [
        ({}, 8),
        ({'status': 'limited', 'actual_status': 'limited'}, 8),
        ({'status': 'disabled', 'actual_status': 'disabled'}, 8),
    ],
)
async def test_theme_on_other_statuses_render(theme_on, handler_env, subscription, days):
    text, _ = await _show(_subscription(days=days, **subscription))
    assert text.startswith('<b><tg-emoji')


# --- Под темой: клавиатура ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_keyboard_active(theme_on, handler_env):
    _, markup = await _show(_subscription())
    texts = get_texts('ru')
    rows = [[(button.text, button.callback_data) for button in row] for row in markup.inline_keyboard]
    assert rows == [
        [(texts.FORK_BUTTON_CONNECT, None)],
        [(texts.FORK_BUTTON_EXTEND, 'subscription_extend'), (texts.FORK_BUTTON_AUTOPAY, 'subscription_autopay')],
        [(texts.FORK_BUTTON_ADD_DEVICE, 'subscription_change_devices')],
        [(texts.FORK_BUTTON_SETTINGS, 'subscription_settings'), (texts.FORK_BUTTON_TARIFF, 'instant_switch')],
        [(texts.FORK_BUTTON_GIFT, 'subscription_gift')],
        [(texts.BACK, 'back_to_menu')],
    ]
    connect = markup.inline_keyboard[0][0]
    assert connect.web_app is not None and connect.web_app.url == LINK
    themed = _buttons(theme_markup(markup))
    assert [button.callback_data for button in themed if button.style == 'success'] == [None]
    assert themed[0].style == 'success'
    styles = {button.callback_data: button.style for button in themed}
    assert styles['subscription_extend'] == 'primary'
    assert styles['subscription_change_devices'] == 'primary'
    assert styles['back_to_menu'] is None
    # У каждой подписи, кроме «Назад», ровно один эмодзи каталога в начале (станет иконкой).
    for button in themed:
        if button.callback_data != 'back_to_menu':
            assert button.icon_custom_emoji_id, button.text
            assert '<tg-emoji' not in button.text


@pytest.mark.asyncio
@pytest.mark.parametrize(('price', 'shown'), [(5500, True), (0, False), (None, False)])
async def test_theme_on_add_device_follows_tariff_price(theme_on, handler_env, price, shown):
    handler_env['tariff'] = _tariff(device_price_kopeks=price)
    _, markup = await _show(_subscription())
    assert ('subscription_change_devices' in _callbacks(markup)) is shown


@pytest.mark.parametrize(('selection', 'shown'), [(True, True), (False, False)])
def test_theme_on_add_device_without_tariff_follows_setting(theme_on, monkeypatch, selection, shown):
    monkeypatch.setattr(settings, 'SALES_MODE', 'classic')
    monkeypatch.setattr(settings, 'DEVICES_SELECTION_ENABLED', selection)
    markup = kb.get_subscription_keyboard('ru', has_subscription=True, subscription=_subscription(tariff_id=None))
    assert ('subscription_change_devices' in _callbacks(markup)) is shown


@pytest.mark.parametrize(
    'overrides',
    [
        {},
        {'is_trial': True},
        {'status': 'expired', 'actual_status': 'expired'},
        {'status': 'expired', 'actual_status': 'expired', 'is_trial': True},
        {'subscription_url': None},
    ],
)
def test_theme_on_keyboard_has_exactly_one_green(theme_on, overrides):
    subscription = _subscription(tariff=_tariff(), **overrides)
    markup = kb.get_subscription_keyboard(
        'ru', has_subscription=True, is_trial=subscription.is_trial, subscription=subscription, gift_enabled=True
    )
    themed = _buttons(theme_markup(markup))
    assert sum(1 for button in themed if button.style == 'success') == 1


@pytest.mark.asyncio
async def test_theme_on_devices_unknown_shows_limit_only(theme_on, handler_env):
    handler_env['devices'] = None  # панель не вернула словарь — апстрим оставляет счётчик пустым
    text, _ = await _show(_subscription())
    assert text.split('\n')[2] == 'Трафик: 0 ГБ из ∞ · Устройства: 3'
    assert 'Подключено' not in text
