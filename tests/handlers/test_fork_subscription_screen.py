"""Экран «Подписка» под темой форка: блоки с иконкой у каждой строки, 4 ряда кнопок и подменю «Ещё».

Без темы экран обязан остаться байт-в-байт как у апстрима — снимок ниже снят с кода
до переписывания. Под темой текст собирает purchase._build_fork_subscription_text,
клавиатуру — get_subscription_keyboard (ветка форка), подменю — subscription.more.
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
    state = {
        'devices': {'total': len(DEVICES), 'devices': DEVICES},
        'tariff': _tariff(),
        'purchases': [],
        'tariffs_count': 1,
    }

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

    async def count_tariffs(db, *, include_inactive=False):
        return state['tariffs_count']

    monkeypatch.setattr(purchase, 'datetime', _FrozenDatetime)
    monkeypatch.setattr(subscription_crud, 'check_and_update_subscription_status', check_status)
    monkeypatch.setattr(purchase, 'SubscriptionService', lambda: service)
    monkeypatch.setattr(remnawave_service, 'RemnaWaveService', _RemnaWave)
    monkeypatch.setattr(tariff_crud, 'get_tariff_by_id', tariff_by_id)
    monkeypatch.setattr(tariff_crud, 'count_tariffs', count_tariffs)
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


def _blocks(text: str) -> list[list[str]]:
    return [block.split('\n') for block in text.split('\n\n')]


@pytest.mark.asyncio
async def test_theme_on_active_screen_ru(theme_on, handler_env):
    text, _ = await _show(_subscription())
    assert text == (
        f'<b>{_tag("check")} Подписка активна</b>\n'
        '\n'
        f'{_tag("hourglass")} До <b>25.10.2026</b> · осталось 8 дней\n'
        f'{_tag("chart")} Трафик: <b>0 ГБ</b> из ∞\n'
        f'{_tag("phone")} Устройства: <b>2 из 3</b>\n'
        '\n'
        f'{_tag("link")} Ссылка для приложения:\n'
        '<code>https://sub.example/abc?x=1&amp;y=2</code>'
    )


@pytest.mark.asyncio
async def test_theme_on_active_screen_en(theme_on, handler_env):
    handler_env['tariff'] = _tariff(name='Standard')
    text, _ = await _show(_subscription(traffic_used_gb=12.34), language='en')
    assert text == (
        f'<b>{_tag("check")} Subscription active</b>\n'
        '\n'
        f'{_tag("hourglass")} Until <b>25.10.2026</b> · 8 days left\n'
        f'{_tag("chart")} Traffic: <b>12.3 GB</b> of ∞\n'
        f'{_tag("phone")} Devices: <b>2 of 3</b>\n'
        '\n'
        f'{_tag("link")} Link for the app:\n'
        '<code>https://sub.example/abc?x=1&amp;y=2</code>'
    )
    assert not re.search('[а-яА-ЯёЁ]', text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('language', 'status', 'period'),
    [
        ('ru', 'Подписка скоро истекает', 'До <b>19.10.2026</b> · осталось 2 дня'),
        ('en', 'Subscription expires soon', 'Until <b>19.10.2026</b> · 2 days left'),
    ],
)
async def test_theme_on_expiring_screen(theme_on, handler_env, language, status, period):
    text, _ = await _show(_subscription(days=2), language=language)
    blocks = _blocks(text)
    assert blocks[0] == [f'<b>{_tag("hourglass")} {status}</b>']
    # ⏳ уже в статусе — у срока будильник, чтобы на экране не было двух песочных часов.
    assert blocks[1][0] == f'{_tag("alarm")} {period}'
    # Отдельных предупреждений апстрима («⚠️ истекает завтра!») больше нет — их заменил статус.
    assert '⚠️' not in text and '🔴' not in text


@pytest.mark.asyncio
async def test_theme_on_last_day(theme_on, handler_env):
    text, _ = await _show(_subscription(days=0, end_date=NOW + timedelta(hours=5)))
    assert _blocks(text)[1][0] == f'{_tag("alarm")} До <b>17.10.2026</b> · осталось меньше суток'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('language', 'days', 'status', 'period'),
    [
        ('ru', 20, 'Пробный доступ', 'До <b>06.11.2026</b> · осталось 20 дней'),
        ('en', 20, 'Free trial', 'Until <b>06.11.2026</b> · 20 days left'),
        # Прод: пробный на 3 дня — не «скоро истекает» с первой минуты.
        ('ru', 3, 'Пробный доступ', 'До <b>20.10.2026</b> · осталось 3 дня'),
        ('ru', 1, 'Пробный доступ', 'До <b>18.10.2026</b> · осталось 1 день'),
    ],
)
async def test_theme_on_trial_screen(theme_on, handler_env, language, days, status, period):
    handler_env['tariff'] = None
    # Пробная в БД — status='active' с is_trial (actual_status модели тоже 'active').
    subscription = _subscription(days=days, is_trial=True, tariff_id=None)
    text, markup = await _show(subscription, language=language)
    blocks = _blocks(text)
    assert blocks[0] == [f'<b>{_tag("gift")} {status}</b>']
    assert blocks[1][0] == f'{_tag("hourglass")} {period}'
    assert blocks[2][1] == '<code>https://sub.example/abc?x=1&amp;y=2</code>'
    assert 'subscription_change_devices' not in _callbacks(markup)


@pytest.mark.asyncio
async def test_theme_on_trial_last_day_is_expiring(theme_on, handler_env):
    handler_env['tariff'] = None
    subscription = _subscription(days=0, end_date=NOW + timedelta(hours=5), is_trial=True, tariff_id=None)
    text, _ = await _show(subscription)
    blocks = _blocks(text)
    assert blocks[0] == [f'<b>{_tag("hourglass")} Подписка скоро истекает</b>']
    assert blocks[1][0] == f'{_tag("alarm")} До <b>17.10.2026</b> · осталось меньше суток'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('language', 'status', 'period'),
    [
        ('ru', 'Подписка истекла', 'Закончилась <b>09.10.2026</b>'),
        ('en', 'Subscription expired', 'Ended <b>09.10.2026</b>'),
    ],
)
async def test_theme_on_expired_screen(theme_on, handler_env, language, status, period):
    subscription = _subscription(days=0, end_date=NOW - timedelta(days=8), status='expired', actual_status='expired')
    text, _ = await _show(subscription, language=language)
    # Ни трафика, ни устройств, ни ссылки — подписка закончилась.
    assert text == f'<b>{_tag("cross")} {status}</b>\n\n{_tag("hourglass")} {period}'


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
    for clutter in (
        '<blockquote',
        'Серверы',
        'Servers',
        'Нидерланды',
        'Баланс',
        'Balance',
        'Алексей',
        '<h',
        # Название тарифа, список устройств и подсказка «нажмите, чтобы скопировать» убраны по ревью владельца.
        'Стандартный',
        'Подключено',
        'Connected',
        'macOS',
        'iPhone',
        'скопировать',
        'tap to copy',
    ):
        assert clutter not in text, clutter
    # Иконка у статуса и у каждой строки данных и ссылки — не больше пяти анимированных эмодзи.
    assert text.count('<tg-emoji') == 5
    # Двух песочных часов (статус «скоро истекает» и срок) на одном экране не бывает.
    assert text.count(_tag('hourglass')) == 1
    assert text.startswith('<b><tg-emoji')


@pytest.mark.asyncio
async def test_theme_on_devices_selection_off_drops_devices(theme_on, handler_env, monkeypatch):
    # Review Focus 2: апстрим вырезает «📱 Устройства: …» точной подстрокой; под темой строки просто нет.
    monkeypatch.setattr(settings, 'DEVICES_SELECTION_ENABLED', False)
    text, _ = await _show(_subscription())
    assert _blocks(text)[1] == [
        f'{_tag("hourglass")} До <b>25.10.2026</b> · осталось 8 дней',
        f'{_tag("chart")} Трафик: <b>0 ГБ</b> из ∞',
    ]
    assert 'Устройства' not in text


@pytest.mark.asyncio
async def test_theme_on_link_hidden_by_setting(theme_on, handler_env, monkeypatch):
    monkeypatch.setattr(settings, 'HIDE_SUBSCRIPTION_LINK', True)
    text, _ = await _show(_subscription())
    assert '<code>' not in text and 'Ссылка' not in text
    assert len(_blocks(text)) == 2


@pytest.mark.asyncio
async def test_theme_on_purchased_traffic_is_one_line(theme_on, handler_env):
    purchases = [
        SimpleNamespace(traffic_gb=10, expires_at=NOW + timedelta(days=5), created_at=NOW - timedelta(days=25)),
        SimpleNamespace(traffic_gb=20, expires_at=NOW + timedelta(days=20), created_at=NOW - timedelta(days=10)),
    ]
    text, _ = await _show(_subscription(traffic_limit_gb=50, traffic_used_gb=7.5), purchases=purchases)
    assert _blocks(text)[1] == [
        f'{_tag("hourglass")} До <b>25.10.2026</b> · осталось 8 дней',
        f'{_tag("chart")} Трафик: <b>7.5 ГБ</b> из 50 ГБ',
        f'{_tag("chart_up")} Докуплено: <b>30 ГБ</b> до 22.10.2026',
        f'{_tag("phone")} Устройства: <b>2 из 3</b>',
    ]
    assert '▰' not in text


@pytest.mark.asyncio
async def test_theme_on_long_values_fit_the_caption(theme_on, handler_env):
    long_devices = [{'platform': f'<Android {i}>', 'deviceModel': 'Very Long Device Model Name ' * 4} for i in range(7)]
    handler_env['devices'] = {'total': 7, 'devices': long_devices}
    handler_env['tariff'] = _tariff(name='Очень длинное название тарифа ' * 3)
    long_link = 'https://sub.example/' + 'x' * 200
    text, _ = await _show(_subscription(days=2, subscription_url=long_link, device_limit=10))
    # Имена устройств больше не печатаются — только счётчик.
    assert 'Android' not in text
    assert f'{_tag("phone")} Устройства: <b>7 из 10</b>' in text
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
    assert text.count('<tg-emoji') <= 5


@pytest.mark.asyncio
async def test_theme_on_devices_unknown_shows_limit_only(theme_on, handler_env):
    handler_env['devices'] = None  # панель не вернула словарь — апстрим оставляет счётчик пустым
    text, _ = await _show(_subscription())
    assert _blocks(text)[1][2] == f'{_tag("phone")} Устройства: <b>3</b>'


# --- Под темой: клавиатура ----------------------------------------------------------------


def _rows(markup):
    return [[(button.text, button.callback_data) for button in row] for row in markup.inline_keyboard]


@pytest.mark.asyncio
async def test_theme_on_keyboard_active(theme_on, handler_env):
    _, markup = await _show(_subscription())
    texts = get_texts('ru')
    assert _rows(markup) == [
        [(texts.FORK_BUTTON_CONNECT, None)],
        [
            (texts.FORK_BUTTON_EXTEND, 'subscription_extend'),
            (texts.FORK_BUTTON_DEVICES, 'subscription_manage_devices'),
        ],
        [(texts.FORK_BUTTON_SUB_MORE, 'sub_more')],
        [(texts.BACK, 'back_to_menu')],
    ]
    assert texts.FORK_BUTTON_SUB_MORE == '➕ Ещё'
    connect = markup.inline_keyboard[0][0]
    assert connect.web_app is not None and connect.web_app.url == LINK
    themed = _buttons(theme_markup(markup))
    assert [button.callback_data for button in themed if button.style == 'success'] == [None]
    assert themed[0].style == 'success'
    styles = {button.callback_data: button.style for button in themed}
    assert styles['subscription_extend'] == 'primary'
    assert styles['subscription_manage_devices'] is None
    assert styles['sub_more'] is None
    assert styles['back_to_menu'] is None
    # Главные кнопки — с анимированной иконкой каталога; «Ещё» (➕ нет в каталоге) и «Назад» — без.
    for button in themed:
        if button.callback_data not in ('back_to_menu', 'sub_more'):
            assert button.icon_custom_emoji_id, button.text
        assert '<tg-emoji' not in button.text


@pytest.mark.asyncio
async def test_theme_on_keyboard_en_has_no_russian(theme_on, handler_env):
    _, markup = await _show(_subscription(), language='en')
    for button in _buttons(theme_markup(markup)):
        assert not re.search('[а-яА-ЯёЁ]', button.text), button.text


@pytest.mark.asyncio
async def test_theme_on_keyboard_ended(theme_on, handler_env):
    subscription = _subscription(days=0, end_date=NOW - timedelta(days=8), status='expired', actual_status='expired')
    _, markup = await _show(subscription)
    texts = get_texts('ru')
    assert _rows(markup) == [
        [(texts.FORK_BUTTON_CONNECT, None)],
        [(texts.FORK_BUTTON_EXTEND, 'subscription_extend')],
        [(texts.FORK_BUTTON_SUB_MORE, 'sub_more')],
        [(texts.BACK, 'back_to_menu')],
    ]
    themed = _buttons(theme_markup(markup))
    # У закончившейся подписки главное — продлить; «Подключиться» синяя, «Устройств» нет.
    assert [button.callback_data for button in themed if button.style == 'success'] == ['subscription_extend']
    assert themed[0].style == 'primary'


@pytest.mark.asyncio
async def test_theme_on_keyboard_trial(theme_on, handler_env):
    handler_env['tariff'] = None
    _, markup = await _show(_subscription(days=20, is_trial=True, tariff_id=None))
    texts = get_texts('ru')
    assert _rows(markup) == [
        [(texts.FORK_BUTTON_CONNECT, None)],
        [(texts.FORK_BUTTON_BUY_SUBSCRIPTION, 'subscription_upgrade')],
        [(texts.FORK_BUTTON_SUB_MORE, 'sub_more')],
        [(texts.BACK, 'back_to_menu')],
    ]
    themed = _buttons(theme_markup(markup))
    assert [button.style for button in themed[:2]] == ['success', 'primary']


@pytest.mark.asyncio
@pytest.mark.parametrize('autopay', [False, True])
@pytest.mark.parametrize('tariffs_count', [1, 2])
async def test_theme_on_main_keyboard_keeps_secondary_actions_out(
    theme_on, handler_env, monkeypatch, autopay, tariffs_count
):
    # Автоплатёж, тариф, докупка трафика и подарок — только в «Ещё», на главном экране их нет никогда.
    monkeypatch.setattr(settings, 'ENABLE_AUTOPAY', autopay)
    handler_env['tariffs_count'] = tariffs_count
    handler_env['tariff'] = _tariff(traffic_limit_gb=50, can_topup_traffic=lambda: True)
    _, markup = await _show(_subscription(traffic_limit_gb=50))
    callbacks = _callbacks(markup)
    for hidden in (
        'subscription_autopay',
        'instant_switch',
        'tariff_switch',
        'buy_traffic',
        'subscription_gift',
        'subscription_settings',
        'subscription_change_devices',
    ):
        assert hidden not in callbacks, hidden
    assert len(markup.inline_keyboard) == 4


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


def test_theme_on_keyboard_without_more_items_has_no_more_row(theme_on, monkeypatch):
    # Пробная, ссылка скрыта настройкой, подарки выключены — в «Ещё» класть нечего, кнопки нет.
    monkeypatch.setattr(settings, 'HIDE_SUBSCRIPTION_LINK', True)
    subscription = _subscription(is_trial=True, tariff=None)
    markup = kb.get_subscription_keyboard(
        'ru', has_subscription=True, is_trial=True, subscription=subscription, gift_enabled=False
    )
    assert 'sub_more' not in _callbacks(markup)


# --- Под темой: подменю «Ещё» -------------------------------------------------------------


def _more_callback():
    callback = MagicMock()
    callback.data = 'sub_more'
    callback.message.edit_text = AsyncMock()
    callback.message.edit_reply_markup = AsyncMock()
    callback.answer = AsyncMock()
    return callback


async def _more(subscription, language: str = 'ru'):
    from app.handlers.subscription import more

    callback = _more_callback()
    await more.show_subscription_more(callback, _user(subscription, language), _db())
    # Меняются только кнопки — текст экрана подписки остаётся.
    callback.message.edit_text.assert_not_awaited()
    callback.message.edit_reply_markup.assert_awaited_once()
    callback.answer.assert_awaited()
    return callback.message.edit_reply_markup.await_args.kwargs['reply_markup']


@pytest.mark.asyncio
async def test_more_submenu_prod_like(theme_on, handler_env):
    markup = await _more(_subscription())
    texts = get_texts('ru')
    assert _rows(markup) == [
        [(texts.FORK_BUTTON_COPY_LINK, None)],
        [(texts.FORK_BUTTON_SETTINGS, 'subscription_settings')],
        [(texts.FORK_BUTTON_GIFT, 'subscription_gift')],
        [(texts.BACK, 'menu_subscription')],
    ]
    copy = markup.inline_keyboard[0][0]
    assert copy.copy_text is not None and copy.copy_text.text == LINK
    themed = _buttons(theme_markup(markup))
    # Подменю — всё второстепенное: ни одной цветной кнопки; «Назад» — без иконки.
    assert all(button.style is None for button in themed)
    assert not themed[-1].icon_custom_emoji_id
    for button in themed[:-1]:
        assert button.icon_custom_emoji_id, button.text


@pytest.mark.asyncio
async def test_more_submenu_with_autopay_tariffs_and_traffic(theme_on, handler_env, monkeypatch):
    monkeypatch.setattr(settings, 'ENABLE_AUTOPAY', True)
    handler_env['tariffs_count'] = 2
    handler_env['tariff'] = _tariff(traffic_limit_gb=50, can_topup_traffic=lambda: True)
    markup = await _more(_subscription(traffic_limit_gb=50))
    assert _callbacks(markup) == [
        'subscription_settings',
        'subscription_gift',
        'instant_switch',
        'subscription_autopay',
        'buy_traffic',
        'menu_subscription',
    ]
    assert all(button.style is None for button in _buttons(theme_markup(markup)))


@pytest.mark.asyncio
async def test_more_submenu_hides_autopay_and_single_tariff(theme_on, handler_env, monkeypatch):
    monkeypatch.setattr(settings, 'ENABLE_AUTOPAY', False)
    handler_env['tariffs_count'] = 1
    callbacks = _callbacks(await _more(_subscription()))
    assert 'subscription_autopay' not in callbacks
    assert 'instant_switch' not in callbacks and 'tariff_switch' not in callbacks


@pytest.mark.asyncio
async def test_more_submenu_trial(theme_on, handler_env):
    handler_env['tariff'] = None
    markup = await _more(_subscription(days=20, is_trial=True, tariff_id=None))
    assert _callbacks(markup) == ['subscription_gift', 'menu_subscription']
    assert markup.inline_keyboard[0][0].copy_text.text == LINK


@pytest.mark.asyncio
async def test_more_submenu_ended_has_no_link_and_no_tariff(theme_on, handler_env):
    handler_env['tariffs_count'] = 2
    subscription = _subscription(days=0, end_date=NOW - timedelta(days=8), status='expired', actual_status='expired')
    markup = await _more(subscription)
    assert all(button.copy_text is None for button in _buttons(markup))
    assert _callbacks(markup) == ['subscription_settings', 'subscription_gift', 'menu_subscription']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('setting', 'url'),
    [(True, LINK), (False, 'https://sub.example/' + 'x' * 300)],
)
async def test_more_submenu_without_copyable_link(theme_on, handler_env, monkeypatch, setting, url):
    # Ссылка скрыта настройкой или длиннее лимита copy_text Telegram (256) — кнопки копирования нет.
    monkeypatch.setattr(settings, 'HIDE_SUBSCRIPTION_LINK', setting)
    markup = await _more(_subscription(subscription_url=url))
    assert all(button.copy_text is None for button in _buttons(markup))
    assert _callbacks(markup)[0] == 'subscription_settings'


@pytest.mark.asyncio
async def test_more_submenu_en_has_no_russian(theme_on, handler_env):
    markup = await _more(_subscription(), language='en')
    for button in _buttons(theme_markup(markup)):
        assert not re.search('[а-яА-ЯёЁ]', button.text), button.text


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['theme_off', 'no_subscription'])
async def test_more_submenu_does_nothing_without_theme_or_subscription(prod_like, handler_env, monkeypatch, case):
    from app.handlers.subscription import more

    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', case != 'theme_off')
    subscription = None if case == 'no_subscription' else _subscription()
    callback = _more_callback()
    await more.show_subscription_more(callback, _user(subscription), _db())
    callback.message.edit_reply_markup.assert_not_awaited()
    callback.answer.assert_awaited_once()


def test_more_submenu_is_registered_and_back_reopens_the_screen():
    from app.handlers.subscription import more

    dispatcher = MagicMock()
    purchase.register_handlers(dispatcher)
    registered = dispatcher.callback_query.register.call_args_list

    def resolves(handler, data):
        return any(call.args[0] is handler and call.args[1].resolve(SimpleNamespace(data=data)) for call in registered)

    assert resolves(more.show_subscription_more, 'sub_more')
    # «Назад» в подменю — menu_subscription: экран подписки рисуется заново.
    assert resolves(purchase.show_subscription_info, 'menu_subscription')
