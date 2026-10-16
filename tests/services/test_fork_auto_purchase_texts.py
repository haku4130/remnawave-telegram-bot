"""Уведомления после пополнения (автопокупка, автопродление, докупка) — под темой форка.

Каждое сообщение уходит обычным ``bot.send_message(..., parse_mode='HTML')``. Тесты гоняют настоящие
функции сервиса: докупка устройств и трафика — на настоящей базе, покупка и продление подписки — с заглушками
на всё, что ходит наружу (панель, цены, списание), так что в шаблон попадают реальные kwargs вызова.
Без темы текст и кнопки остаются байт-в-байт как у апстрима.
"""

from __future__ import annotations

import fnmatch
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.database.models import Base, Subscription, User
from app.localization import loader
from app.localization.emoji_catalog import tag
from app.localization.texts import get_texts
from app.middlewares.premium_theme import theme_markup
from app.services import subscription_auto_purchase_service as auto_service
from app.services.user_cart_service import UserCartService
from app.utils.timezone import format_local_datetime
from tests.fixtures.sqlite_memory import memory_session


USER_ID = 7
SUB_ID = 42
LINK = 'https://sub.example.com/abc123'
TRAFFIC_PRICE = 5000
DEVICE_PRICE = 1000
BALANCE = 100_000
END_DATE = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)

TAG_RE = re.compile(r'<[^>]+>')
CYRILLIC_RE = re.compile('[а-яА-ЯёЁ]')


def _plain(text: str) -> str:
    return TAG_RE.sub('', text)


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def _labels(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _greens(markup) -> int:
    return sum(1 for button in _buttons(theme_markup(markup)) if button.style == 'success')


def _sent(bot) -> tuple[str, object]:
    bot.send_message.assert_awaited_once()
    call = bot.send_message.await_args
    assert call.kwargs['parse_mode'] == 'HTML'
    return call.kwargs['text'], call.kwargs['reply_markup']


# --- окружение -------------------------------------------------------------------------------


class _MockRedis:
    def __init__(self):
        self.storage: dict[str, str] = {}

    async def setex(self, key, ttl, value):
        self.storage[key] = value
        return True

    async def get(self, key):
        return self.storage.get(key)

    async def delete(self, key):
        return 1 if self.storage.pop(key, None) is not None else 0

    async def exists(self, key):
        return 1 if key in self.storage else 0

    async def scan(self, cursor=0, match=None, count=50):
        return 0, [k for k in self.storage if match is None or fnmatch.fnmatch(k, match)]


class _PanelStub:
    """Панель Remnawave в тесте недоступна — синхронизация считается успешной."""

    async def update_remnawave_user(self, db, subscription, **kwargs):
        return None

    async def create_remnawave_user(self, db, subscription, **kwargs):
        return None

    async def enable_remnawave_user(self, remnawave_id):
        return None


class _AdminNotifyStub:
    def __init__(self, bot):
        self.bot = bot

    async def send_subscription_update_notification(self, *args, **kwargs):
        return None


@pytest.fixture
def cart_service(monkeypatch):
    service = UserCartService()
    service._redis_client = _MockRedis()
    service._initialized = True
    monkeypatch.setattr(auto_service, 'user_cart_service', service)
    return service


@pytest.fixture(autouse=True)
def _prod_like(monkeypatch):
    monkeypatch.setattr(settings, 'SALES_MODE', 'classic')
    monkeypatch.setattr(settings, 'MULTI_TARIFF_ENABLED', False)
    monkeypatch.setattr(settings, 'CONNECT_BUTTON_MODE', 'miniapp_subscription')
    monkeypatch.setattr(settings, 'PRICE_PER_DEVICE', DEVICE_PRICE)
    monkeypatch.setattr(settings, 'MAX_DEVICES_LIMIT', 0)
    monkeypatch.setattr(settings, 'PRICE_ROUNDING_ENABLED', False)
    monkeypatch.setattr(type(settings), 'get_traffic_topup_price', lambda self, gb: TRAFFIC_PRICE if gb == 100 else 0)
    monkeypatch.setattr(type(settings), 'is_auto_purchase_after_topup_enabled', lambda self: True)
    monkeypatch.setattr(type(settings), 'is_notifications_enabled', lambda self: True)
    monkeypatch.setattr(auto_service, 'SubscriptionService', _PanelStub)
    monkeypatch.setattr(auto_service, 'AdminNotificationService', _AdminNotifyStub)


@pytest.fixture
def theme_on(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    loader.clear_locale_cache()
    yield
    loader.clear_locale_cache()


@pytest.fixture
def theme_off(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    yield
    loader.clear_locale_cache()


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


# --- докупка устройств и трафика: настоящая база ---------------------------------------------


async def _seed(db, language: str, *, link: str | None, traffic_limit: int = 100) -> User:
    now = datetime.now(UTC)
    user = User(
        id=USER_ID,
        telegram_id=555000111,
        first_name='U',
        status='active',
        language=language,
        balance_kopeks=BALANCE,
        created_at=now,
    )
    subscription = Subscription(
        id=SUB_ID,
        user_id=USER_ID,
        status='active',
        is_trial=False,
        start_date=now - timedelta(days=15),
        end_date=now + timedelta(days=15),
        traffic_limit_gb=traffic_limit,
        traffic_used_gb=0.0,
        device_limit=1,
        subscription_url=link,
    )
    db.add_all([user, subscription])
    await db.commit()
    return user


def _addon_cart(mode: str, *, manual_ready: bool = True) -> dict:
    cart = {'subscription_id': SUB_ID, 'source': 'bot', 'return_to_cart': True, 'cart_mode': mode}
    if mode == 'add_devices':
        cart.update(devices_to_add=2, price_kopeks=DEVICE_PRICE * 2)
    else:
        cart.update(traffic_gb=100, price_kopeks=TRAFFIC_PRICE)
    return cart


async def run_addon(monkeypatch, cart_service, mode: str, *, language='ru', link=LINK, manual=False, traffic_limit=100):
    bot = _bot()
    async with memory_session(monkeypatch, list(Base.metadata.sorted_tables)) as db:
        user = await _seed(db, language, link=link, traffic_limit=traffic_limit)
        await cart_service.save_user_cart(USER_ID, _addon_cart(mode))
        if manual:
            succeeded = await auto_service.resume_addon_cart(db, user, _addon_cart(mode), bot=bot)
        else:
            succeeded = await auto_service.auto_purchase_saved_cart_after_topup(db, user, bot=bot)
        assert succeeded is True
    return bot


# --- покупка и продление подписки: заглушки на внешнее ---------------------------------------


def _subscription(language: str = 'ru', link: str | None = LINK) -> SimpleNamespace:
    return SimpleNamespace(
        id=SUB_ID,
        user_id=USER_ID,
        status='active',
        is_trial=False,
        end_date=END_DATE,
        tariff_id=1,
        device_limit=3,
        traffic_limit_gb=0,
        subscription_url=link,
        subscription_crypto_link=None,
    )


def _user(language: str = 'ru') -> SimpleNamespace:
    return SimpleNamespace(
        id=USER_ID, telegram_id=555000111, language=language, balance_kopeks=BALANCE, email=None, remnawave_id=None
    )


@pytest.fixture
def externals(monkeypatch):
    """Всё, что ходит наружу из покупки/продления; возвращает настройки, которые тест может поменять."""
    state = {'subscription': _subscription()}

    async def subtract(*args, **kwargs):
        return True

    async def create_tx(*args, **kwargs):
        return SimpleNamespace(id=1)

    async def nothing(*args, **kwargs):
        return None

    async def extend(db, subscription, *args, **kwargs):
        return state['subscription']

    async def create_paid(*args, **kwargs):
        return state['subscription']

    async def existing_subscription(db, user_id):
        return state.get('existing')

    async def lock_user(db, user_id):
        return state['user']

    async def get_tariff(db, tariff_id):
        return SimpleNamespace(
            id=1,
            name='Стандартный',
            is_active=True,
            is_daily=False,
            period_prices={'30': 19900},
            allowed_squads=['squad'],
            traffic_limit_gb=0,
            device_limit=3,
            can_purchase_custom_days=lambda: False,
            get_shortest_period=lambda: 30,
        )

    async def price(*args, **kwargs):
        return SimpleNamespace(
            final_total=19900, promo_offer_discount=0, original_total=19900, breakdown={}, is_tariff_mode=True
        )

    async def admin(callback):
        return None

    async def prepare(db, user, cart_data):
        return auto_service.AutoExtendContext(
            subscription=state['existing'],
            period_days=30,
            price_kopeks=19900,
            description='Продление',
            tariff_id=1,
            tariff_name='Стандартный',
        )

    monkeypatch.setattr(auto_service, 'subtract_user_balance', subtract)
    monkeypatch.setattr(auto_service, 'create_transaction', create_tx)
    monkeypatch.setattr(auto_service, 'extend_subscription', extend)
    monkeypatch.setattr(auto_service, 'clear_subscription_checkout_draft', nothing)
    monkeypatch.setattr(auto_service, '_delete_cart_for_subscription', nothing)
    monkeypatch.setattr(auto_service, '_apply_extension_updates', lambda prepared: None)
    monkeypatch.setattr(auto_service, '_resolve_extend_traffic_limit_gb', nothing)
    monkeypatch.setattr(auto_service, '_prepare_auto_extend_context', prepare)
    monkeypatch.setattr(auto_service.pricing_engine, 'calculate_tariff_purchase_price', price)
    monkeypatch.setattr(auto_service.pricing_engine, 'calculate_renewal_price', price)
    monkeypatch.setattr('app.database.crud.user.subtract_user_balance', subtract)
    monkeypatch.setattr('app.database.crud.user.lock_user_for_pricing', lock_user)
    monkeypatch.setattr('app.database.crud.tariff.get_tariff_by_id', get_tariff)
    monkeypatch.setattr('app.database.crud.subscription.get_subscription_by_user_id', existing_subscription)
    monkeypatch.setattr('app.database.crud.subscription.create_paid_subscription', create_paid)
    monkeypatch.setattr('app.database.crud.subscription.extend_subscription', extend)
    monkeypatch.setattr('app.database.crud.transaction.create_transaction', create_tx)
    monkeypatch.setattr('app.database.crud.server_squad.get_all_server_squads', AsyncMock(return_value=([], 0)))
    monkeypatch.setattr('app.cabinet.routes.websocket.notify_user_subscription_renewed', nothing)
    monkeypatch.setattr('app.cabinet.routes.websocket.notify_user_subscription_activated', nothing)
    monkeypatch.setattr('app.services.subscription_renewal_service.with_admin_notification_service', admin)
    return state


def _db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    return db


async def run_new_purchase(externals, language='ru', link=LINK):
    user = _user(language)
    externals['user'] = user
    externals['subscription'] = _subscription(language, link)
    externals['existing'] = None
    bot = _bot()
    cart = {'cart_mode': 'tariff', 'tariff_id': 1, 'period_days': 30}
    assert await auto_service._auto_purchase_tariff(_db(), user, cart, bot=bot) is True
    return bot


async def run_cart_extend(externals, language='ru', link=LINK):
    user = _user(language)
    externals['user'] = user
    externals['subscription'] = _subscription(language, link)
    externals['existing'] = _subscription(language, link)
    bot = _bot()
    cart = {'cart_mode': 'extend', 'period_days': 30, 'tariff_id': 1}
    assert await auto_service._auto_extend_subscription(_db(), user, cart, bot=bot) is True
    return bot


async def run_expired_extend(externals, language='ru', link=LINK):
    user = _user(language)
    externals['user'] = user
    expired = _subscription(language, link)
    expired.status = 'expired'
    expired.autopay_enabled = True
    expired.end_date = datetime.now(UTC) - timedelta(days=2)
    expired.updated_at = datetime.now(UTC) - timedelta(days=2)
    expired.tariff = SimpleNamespace(id=1, name='Стандартный', is_active=True, get_shortest_period=lambda: 30)
    externals['subscription'] = _subscription(language, link)
    externals['existing'] = expired
    bot = _bot()
    assert await auto_service.try_auto_extend_expired_after_topup(_db(), user, bot=bot) is True
    return bot


SUBSCRIPTION_RUNNERS = [run_new_purchase, run_cart_extend, run_expired_extend]
RUNNER_IDS = ['purchase', 'cart_extend', 'expired_extend']


# --- без темы: как у апстрима ----------------------------------------------------------------

UPSTREAM_KEYBOARD_RU = [['📱 Моя подписка'], ['⬅️ В главное меню']]
UPSTREAM_KEYBOARD_EN = [['📱 My subscription'], ['⬅️ Back to main menu']]


@pytest.mark.asyncio
async def test_theme_off_new_purchase_matches_upstream(theme_off, externals):
    bot = await run_new_purchase(externals)
    text, markup = _sent(bot)
    assert text == (
        '✅ Подписка на 1 месяц автоматически оформлена после пополнения баланса.\n\n'
        'Перейдите в раздел «Моя подписка», чтобы получить ссылку и инструкции.'
    )
    assert _labels(markup) == UPSTREAM_KEYBOARD_RU
    assert [button.callback_data for button in _buttons(markup)] == ['menu_subscription', 'back_to_menu']
    assert all(button.style is None and button.web_app is None for button in _buttons(markup))


@pytest.mark.asyncio
@pytest.mark.parametrize('runner', [run_cart_extend, run_expired_extend], ids=['cart_extend', 'expired_extend'])
async def test_theme_off_extend_matches_upstream(theme_off, externals, runner):
    bot = await runner(externals)
    text, markup = _sent(bot)
    date = format_local_datetime(END_DATE, '%d.%m.%Y %H:%M')
    assert text == (
        '✅ Подписка автоматически продлена на 1 месяц.\n\n'
        f'⏰ Новая дата окончания: {date}.\n\n'
        'Перейдите в раздел «Моя подписка», чтобы получить ссылку и инструкции.'
    )
    assert _labels(markup) == UPSTREAM_KEYBOARD_RU
    assert all(button.style is None and button.web_app is None for button in _buttons(markup))


@pytest.mark.asyncio
async def test_theme_off_extend_english_matches_upstream(theme_off, externals):
    bot = await run_cart_extend(externals, 'en')
    text, markup = _sent(bot)
    date = format_local_datetime(END_DATE, '%d.%m.%Y %H:%M')
    assert text == (
        '✅ Subscription automatically extended for 1 month.\n\n'
        f'⏰ New expiration date: {date}.\n\n'
        'Open the ‘My subscription’ section to access your link and setup instructions.'
    )
    assert _labels(markup) == UPSTREAM_KEYBOARD_EN


@pytest.mark.asyncio
async def test_theme_off_devices_match_upstream(theme_off, monkeypatch, cart_service):
    bot = await run_addon(monkeypatch, cart_service, 'add_devices')
    text, markup = _sent(bot)
    assert text == (
        '✅ <b>Устройства добавлены автоматически!</b>\n\n'
        '📱 Добавлено: 2 устройств\n📊 Новый лимит: 3 устройств\n💰 Списано: 10 ₽'
    )
    assert _labels(markup) == UPSTREAM_KEYBOARD_RU
    assert all(button.style is None and button.web_app is None for button in _buttons(markup))


@pytest.mark.asyncio
async def test_theme_off_manual_devices_match_upstream(theme_off, monkeypatch, cart_service):
    bot = await run_addon(monkeypatch, cart_service, 'add_devices', manual=True)
    text, _ = _sent(bot)
    assert text == (
        '✅ <b>Устройства добавлены!</b>\n\n📱 Добавлено: 2 устройств\n📊 Новый лимит: 3 устройств\n💰 Списано: 10 ₽'
    )


@pytest.mark.asyncio
async def test_theme_off_traffic_matches_upstream(theme_off, monkeypatch, cart_service):
    bot = await run_addon(monkeypatch, cart_service, 'add_traffic')
    text, markup = _sent(bot)
    assert text.startswith(
        '✅ <b>Трафик добавлен автоматически!</b>\n\n📈 Добавлено: 100 ГБ\n📊 Новый лимит: 200 ГБ\n💰 Списано: '
    )
    assert _labels(markup) == UPSTREAM_KEYBOARD_RU


# --- под темой: тексты -----------------------------------------------------------------------


def _assert_fork_style(text: str, *, language: str) -> None:
    assert text.startswith('<b>'), 'заголовок-строка первой'
    first_line = text.split('\n', 1)[0]
    assert first_line.endswith('</b>'), 'первая строка целиком жирная'
    assert first_line.startswith('<b><tg-emoji') and first_line.count('<tg-emoji') == 1
    assert text.count('<tg-emoji') <= 2
    for forbidden in ('<blockquote', '<h1', '<h2', '<h3', '█', '░', '▓'):
        assert forbidden not in text
    assert len(_plain(text)) <= 1024
    assert '{' not in text and '}' not in text, 'неподставленный плейсхолдер'
    if language == 'en':
        assert not CYRILLIC_RE.search(_plain(text)), _plain(text)


@pytest.mark.asyncio
async def test_theme_on_new_purchase_ru(theme_on, externals):
    bot = await run_new_purchase(externals)
    text, _ = _sent(bot)
    assert text == (
        f'<b>{tag("party")} Подписка оформлена на 30 дней</b>\n'
        'Оформили автоматически после пополнения.\n'
        'Действует до <b>15.11.2026</b>'
    )
    _assert_fork_style(text, language='ru')


@pytest.mark.asyncio
@pytest.mark.parametrize('runner', [run_cart_extend, run_expired_extend], ids=['cart_extend', 'expired_extend'])
async def test_theme_on_extend_ru(theme_on, externals, runner):
    bot = await runner(externals)
    text, _ = _sent(bot)
    assert text == (
        f'<b>{tag("refresh")} Подписка продлена на 30 дней</b>\n'
        'Продлили автоматически после пополнения.\n'
        'Действует до <b>15.11.2026</b>'
    )
    _assert_fork_style(text, language='ru')


@pytest.mark.asyncio
@pytest.mark.parametrize('runner', SUBSCRIPTION_RUNNERS, ids=RUNNER_IDS)
async def test_theme_on_subscription_texts_english(theme_on, externals, runner):
    bot = await runner(externals, 'en')
    text, _ = _sent(bot)
    assert '30 days' in text and '<b>15.11.2026</b>' in text
    assert 'top-up' in text
    _assert_fork_style(text, language='en')


def test_theme_on_period_is_declined_by_fork_formatter(theme_on):
    """«1 день», а не «1 дней»: срок в заголовке идёт через форк-форматтер."""
    ru = auto_service._fork_subscription_notification_text(
        get_texts('ru'), 'AUTO_PURCHASE_SUBSCRIPTION_EXTENDED', '', 1, None
    )
    en = auto_service._fork_subscription_notification_text(
        get_texts('en'), 'AUTO_PURCHASE_SUBSCRIPTION_EXTENDED', '', 1, None
    )
    assert 'на 1 день</b>' in ru
    assert 'by 1 day</b>' in en
    assert '\n' in ru and 'Действует до' not in ru, 'без даты строка «Действует до» не выводится'


@pytest.mark.asyncio
async def test_theme_on_devices_ru(theme_on, monkeypatch, cart_service):
    bot = await run_addon(monkeypatch, cart_service, 'add_devices')
    text, _ = _sent(bot)
    assert text == (
        f'<b>{tag("phone")} Устройства добавлены</b>\n'
        'Докупили автоматически после пополнения.\n'
        'Лимит устройств: <b>3</b> (+2)\n'
        'Списано <b>10 ₽</b>'
    )
    _assert_fork_style(text, language='ru')


@pytest.mark.asyncio
async def test_theme_on_manual_devices_ru(theme_on, monkeypatch, cart_service):
    bot = await run_addon(monkeypatch, cart_service, 'add_devices', manual=True)
    text, _ = _sent(bot)
    assert text == (f'<b>{tag("phone")} Устройства добавлены</b>\nЛимит устройств: <b>3</b> (+2)\nСписано <b>10 ₽</b>')
    assert 'автоматически' not in text


@pytest.mark.asyncio
async def test_theme_on_devices_english(theme_on, monkeypatch, cart_service):
    bot = await run_addon(monkeypatch, cart_service, 'add_devices', language='en')
    text, _ = _sent(bot)
    assert text.startswith(f'<b>{tag("phone")} Devices added</b>\n')
    assert 'Device limit: <b>3</b> (+2)' in text
    _assert_fork_style(text, language='en')


@pytest.mark.asyncio
async def test_theme_on_traffic_ru(theme_on, monkeypatch, cart_service):
    bot = await run_addon(monkeypatch, cart_service, 'add_traffic')
    text, _ = _sent(bot)
    assert text.startswith(f'<b>{tag("chart_up")} Трафик добавлен</b>\nДокупили автоматически после пополнения.\n')
    assert 'Лимит трафика: <b>200 ГБ</b> (+100 ГБ)' in text
    assert re.search(r'Списано <b>[\d.,]+ ₽</b>$', text)
    _assert_fork_style(text, language='ru')


@pytest.mark.asyncio
async def test_theme_on_traffic_english_has_no_russian_units(theme_on, monkeypatch, cart_service):
    bot = await run_addon(monkeypatch, cart_service, 'add_traffic', language='en')
    text, _ = _sent(bot)
    assert 'Traffic limit: <b>200 GB</b> (+100 GB)' in text
    assert 'ГБ' not in text
    _assert_fork_style(text, language='en')


@pytest.mark.asyncio
async def test_theme_on_manual_traffic_ru(theme_on, monkeypatch, cart_service):
    bot = await run_addon(monkeypatch, cart_service, 'add_traffic', manual=True)
    text, _ = _sent(bot)
    assert text.startswith(f'<b>{tag("chart_up")} Трафик добавлен</b>\nЛимит трафика: <b>200 ГБ</b> (+100 ГБ)\n')
    assert 'автоматически' not in text


# --- под темой: кнопки -----------------------------------------------------------------------


def _assert_connect_keyboard(markup, language: str) -> None:
    texts = get_texts(language)
    first, back = markup.inline_keyboard[0][0], markup.inline_keyboard[1][0]
    assert first.text == texts.FORK_BUTTON_CONNECT
    assert first.web_app is not None and first.web_app.url == LINK
    assert first.style == 'success'
    assert back.callback_data == 'back_to_menu' and back.text == texts.BACK
    assert len(_buttons(markup)) == 2
    assert _greens(markup) == 1, 'ровно одна зелёная'
    assert theme_markup(markup).inline_keyboard[1][0].style is None, 'назад без цвета'


def _assert_my_subscription_keyboard(markup, language: str) -> None:
    texts = get_texts(language)
    first, back = markup.inline_keyboard[0][0], markup.inline_keyboard[1][0]
    assert first.text == texts.MY_SUBSCRIPTION_BUTTON
    assert first.callback_data == 'menu_subscription' and first.web_app is None
    assert first.style == 'success'
    assert back.callback_data == 'back_to_menu' and back.text == texts.BACK
    assert len(_buttons(markup)) == 2
    assert _greens(markup) == 1
    assert theme_markup(markup).inline_keyboard[1][0].style is None


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
@pytest.mark.parametrize('runner', SUBSCRIPTION_RUNNERS, ids=RUNNER_IDS)
async def test_theme_on_subscription_keyboard_with_link(theme_on, externals, runner, language):
    bot = await runner(externals, language)
    _assert_connect_keyboard(_sent(bot)[1], language)


@pytest.mark.asyncio
@pytest.mark.parametrize('runner', SUBSCRIPTION_RUNNERS, ids=RUNNER_IDS)
async def test_theme_on_subscription_keyboard_without_link(theme_on, externals, runner):
    bot = await runner(externals, 'ru', None)
    _assert_my_subscription_keyboard(_sent(bot)[1], 'ru')


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['add_devices', 'add_traffic'])
@pytest.mark.parametrize('manual', [False, True])
async def test_theme_on_addon_keyboards(theme_on, monkeypatch, cart_service, mode, manual):
    with_link = await run_addon(monkeypatch, cart_service, mode, manual=manual)
    _assert_connect_keyboard(_sent(with_link)[1], 'ru')
    without_link = await run_addon(monkeypatch, cart_service, mode, manual=manual, link=None)
    _assert_my_subscription_keyboard(_sent(without_link)[1], 'ru')


@pytest.mark.asyncio
async def test_theme_on_connect_button_follows_connect_mode(theme_on, externals, monkeypatch):
    """CONNECT_BUTTON_MODE=link — кнопка-ссылка, как у остальных экранов с «Подключиться»."""
    monkeypatch.setattr(settings, 'CONNECT_BUTTON_MODE', 'link')
    bot = await run_new_purchase(externals)
    first = _sent(bot)[1].inline_keyboard[0][0]
    assert first.url == LINK and first.web_app is None and first.style == 'success'


# --- ключи форка и классический путь ----------------------------------------------------------


@pytest.mark.parametrize('language', ['ru', 'en'])
def test_fork_templates_accept_real_call_kwargs(theme_on, language):
    """Шаблоны форка форматируются теми же kwargs, что передают вызывающие места сервиса."""
    texts = get_texts(language)
    for key in ('AUTO_PURCHASE_SUBSCRIPTION_SUCCESS', 'AUTO_PURCHASE_SUBSCRIPTION_EXTENDED'):
        assert texts.t(key, '').format(period='30')
    assert texts.t('AUTO_PURCHASE_SUBSCRIPTION_EXTENDED_DETAILS', '').format(date='15.11.2026')
    for key in ('AUTO_PURCHASE_DEVICES_SUCCESS', 'ADDON_PURCHASE_DEVICES_SUCCESS'):
        assert texts.t(key, '').format(devices_to_add=2, new_limit=3, price='20 ₽')
    for key in ('AUTO_PURCHASE_TRAFFIC_SUCCESS', 'ADDON_PURCHASE_TRAFFIC_SUCCESS'):
        assert texts.t(key, '').format(traffic_gb='100 ГБ', new_limit='200 ГБ', price='50 ₽')
