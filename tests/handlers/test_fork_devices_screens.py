"""Настройки подписки, изменение и управление устройствами, перевыпуск — под темой форка.

Без темы каждый экран обязан остаться байт-в-байт как у апстрима: снимки ниже сняты с кода
до переписывания (6683ecfa), тема выключена. Под темой тексты собирают FORK_*-ключи и
переопределённые ключи апстрима (app/localization/fork/), клавиатуры — те же функции, что у бота.
Тесты гоняют настоящие обработчики с заглушками на всё, что ходит наружу.
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
from app.handlers.subscription import devices, purchase, revoke
from app.localization import loader
from app.localization.texts import get_texts
from app.middlewares.premium_theme import theme_markup
from app.services.pricing_engine import PricingEngine
from app.utils.message_patch import caption_exceeds_telegram_limit


TAG_RE = re.compile(r'<[^>]+>')
CYRILLIC_RE = re.compile('[а-яА-ЯёЁ]')
END_DATE = datetime.now(UTC) + timedelta(days=7, hours=12)  # осталось 8 суток (округление вверх)
PANEL_USER_ID = 4242


def _tag(name: str) -> str:
    from app.localization.emoji_catalog import tag

    return tag(name)


def _tariff(**overrides) -> SimpleNamespace:
    data = {
        'id': 1,
        'name': 'Стандартный',
        'is_daily': False,
        'device_limit': 3,
        'max_device_limit': 10,
        'device_price_kopeks': 5500,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _subscription(**overrides) -> SimpleNamespace:
    data = {
        'id': 7,
        'status': 'active',
        'is_trial': False,
        'tariff_id': 1,
        'device_limit': 4,
        'end_date': END_DATE,
        'traffic_used_gb': 12.34,
        'traffic_limit_gb': 0,
        'connected_squads': ['squad-a', 'squad-b'],
        'remnawave_id': PANEL_USER_ID,
        'updated_at': None,
        'balance_kopeks': 0,
        'is_active': True,
        'last_revoke_at': None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _user(subscription, language: str = 'ru', balance: int = 100_000) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        language=language,
        subscription=subscription,
        balance_kopeks=balance,
        remnawave_id=PANEL_USER_ID,
        telegram_id=42,
        promo_group_id=None,
    )


def _callback(data: str = 'x') -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    callback.bot = MagicMock()
    return callback


def _sent(mock: AsyncMock) -> tuple[str, object]:
    call = mock.await_args
    text = call.args[0] if call.args else call.kwargs['text']
    return text, call.kwargs.get('reply_markup')


def _plain(text: str) -> str:
    return TAG_RE.sub('', text)


def _labels(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


@pytest.fixture
def prod_like(monkeypatch):
    monkeypatch.setattr(settings, 'SALES_MODE', 'tariffs')
    monkeypatch.setattr(settings, 'MULTI_TARIFF_ENABLED', False, raising=False)
    monkeypatch.setattr(type(settings), 'is_multi_tariff_enabled', lambda self: False)
    monkeypatch.setattr(settings, 'DEVICES_SELECTION_ENABLED', True)
    monkeypatch.setattr(settings, 'SUBSCRIPTION_REVOKE_ENABLED', True)
    monkeypatch.setattr(settings, 'ALLOW_DEVICES_BELOW_TARIFF_LIMIT', False)
    monkeypatch.setattr(settings, 'MAX_DEVICES_LIMIT', 10)


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
def env(monkeypatch):
    """Всё, куда обработчики ходят за данными, — заглушки."""
    state = {
        'subscription': _subscription(),
        'tariff': _tariff(),
        'panel_devices': [
            {'platform': 'macOS', 'deviceModel': 'Mac', 'hwid': 'h1', 'createdAt': '2026-10-01'},
            {'platform': 'iOS', 'deviceModel': 'iPhone 15', 'hwid': 'h2', 'createdAt': '2026-10-02'},
            {'platform': 'Android', 'deviceModel': 'Pixel', 'hwid': 'h3', 'createdAt': '2026-10-03'},
        ],
        'discount': 0,
    }

    async def resolve(callback, db_user, db, st=None):
        return state['subscription'], state['subscription'].id

    async def tariff_by_id(db, tariff_id):
        return state['tariff']

    class _Api:
        async def get_user_devices(self, panel_user_id):
            return {'total': len(state['panel_devices']), 'devices': list(state['panel_devices'])}

        async def remove_device(self, panel_user_id, hwid):
            state['panel_devices'] = [d for d in state['panel_devices'] if d['hwid'] != hwid]
            return True

    class _Service:
        def get_api_client(self):
            @asynccontextmanager
            async def client():
                yield _Api()

            return client()

    class _SubscriptionService:
        update_remnawave_user = AsyncMock()
        enable_remnawave_user = AsyncMock()

    async def devices_count(db_user, subscription=None):
        return str(len(state['panel_devices']))

    async def show_countries(db_user):
        return False

    monkeypatch.setattr(devices, '_resolve_subscription', resolve)
    monkeypatch.setattr(tariff_crud, 'get_tariff_by_id', tariff_by_id)
    monkeypatch.setattr(devices, 'RemnaWaveService', _Service)
    monkeypatch.setattr('app.services.remnawave_service.RemnaWaveService', _Service)
    monkeypatch.setattr(devices, 'SubscriptionService', _SubscriptionService)
    monkeypatch.setattr(purchase, 'get_current_devices_count', devices_count)
    monkeypatch.setattr(purchase, '_should_show_countries_management', show_countries)
    monkeypatch.setattr(PricingEngine, 'get_addon_discount_percent', staticmethod(lambda *a, **k: state['discount']))
    monkeypatch.setattr(devices.user_cart_service, 'save_user_cart', AsyncMock())
    monkeypatch.setattr(devices, 'lock_user_for_pricing', AsyncMock(side_effect=lambda db, user_id: state['user']))
    monkeypatch.setattr(devices, 'subtract_user_balance', AsyncMock(return_value=True))
    monkeypatch.setattr(devices, 'create_transaction', AsyncMock())
    monkeypatch.setattr(subscription_crud, 'reactivate_subscription', AsyncMock())
    monkeypatch.setattr(
        'app.services.admin_notification_service.AdminNotificationService',
        lambda bot: SimpleNamespace(send_subscription_update_notification=AsyncMock()),
    )
    monkeypatch.setattr(devices, '_enrich_devices_with_aliases', AsyncMock(side_effect=lambda lst, uid: lst))
    state['user'] = _user(state['subscription'])
    return state


def _db(subscription=None) -> MagicMock:
    db = MagicMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()
    result = MagicMock()
    result.scalar_one.return_value = subscription
    db.execute = AsyncMock(return_value=result)
    return db


# --- запуск обработчиков ------------------------------------------------------------------


async def show_settings(env, language: str = 'ru'):
    callback = _callback('subscription_settings')
    user = _user(env['subscription'], language)
    await purchase.handle_subscription_settings(callback, user, _db())
    return _sent(callback.message.edit_text)


async def show_prompt(env, language: str = 'ru'):
    callback = _callback('subscription_change_devices')
    await devices.handle_change_devices(callback, _user(env['subscription'], language), _db())
    return _sent(callback.message.edit_text)


async def show_confirm(env, new_count: int, language: str = 'ru', balance: int = 100_000):
    callback = _callback(f'change_devices_{new_count}')
    user = _user(env['subscription'], language, balance=balance)
    await devices.confirm_change_devices(callback, user, _db())
    return callback


async def run_execute(env, new_count: int, price: int, language: str = 'ru', relocked=None):
    callback = _callback(f'confirm_change_devices_{new_count}_{price}')
    user = _user(env['subscription'], language)
    env['user'] = user
    await devices.execute_change_devices(callback, user, _db(relocked or env['subscription']))
    return callback


async def show_list(env, language: str = 'ru', page: int = 1):
    callback = _callback()
    await devices.show_devices_page(callback, _user(env['subscription'], language), env['panel_devices'], page=page)
    return _sent(callback.message.edit_text)


async def start_revoke(env, language: str = 'ru'):
    callback = _callback('subscription_revoke')
    await revoke.start_subscription_revoke(callback, _user(env['subscription'], language), _db())
    return _sent(callback.message.edit_text)


async def confirm_revoke(env, monkeypatch, language: str = 'ru', new_url: str | None = 'https://sub.example/new'):
    callback = _callback('subscription_revoke_confirm')
    service = MagicMock()
    service.revoke_subscription = AsyncMock(return_value=new_url)
    monkeypatch.setattr(revoke, 'SubscriptionService', lambda: service)
    await revoke.confirm_subscription_revoke(callback, _user(env['subscription'], language), _db())
    return _sent(callback.message.edit_text)


# --- Без темы: как у апстрима -------------------------------------------------------------

# Снято с кода до переписывания (6683ecfa), тема выключена.
UPSTREAM_SETTINGS = (
    '⚙️ <b>Настройки подписки</b>\n\n'
    '📊 <b>Текущие параметры:</b>\n'
    '🌐 Стран: 2\n'
    '📈 Трафик: 12 ГБ / ∞ (безлимит)\n'
    '📱 Устройства: 3 / 4\n\n'
    'Выберите что хотите изменить:'
)
UPSTREAM_SETTINGS_BUTTONS = [
    ['📱 Изменить устройства'],
    ['🔧 Управление устройствами'],
    ['🔄 Перевыпустить подписку'],
    ['⬅️ Назад'],
]
UPSTREAM_PROMPT = (
    '📱 <b>Изменение количества устройств</b>\n\n'
    'Текущий лимит: 4 устройств\n'
    'Цена за доп. устройство: 55 ₽/мес\n'
    'Выберите новое количество устройств:\n\n'
    '💡 <b>Важно:</b>\n'
    '• При увеличении - доплата пропорционально оставшемуся времени\n'
    '• При уменьшении - возврат средств не производится'
)
UPSTREAM_PROMPT_BUTTONS = [
    ['➖ 3 устр. (без возврата)'],
    ['✅ 4 устр. (текущее)'],
    ['➕ 5 устр. (+14₽ (за 8 дн.))'],
    ['➕ 6 устр. (+29₽ (за 8 дн.))'],
    ['➕ 7 устр. (+44₽ (за 8 дн.))'],
    ['⬅️ Назад'],
]
UPSTREAM_CONFIRM = (
    '📱 <b>Подтверждение изменения</b>\n\n'
    'Текущее количество: 4 устройств\n'
    'Новое количество: 5 устройств\n\n'
    'Действие: увеличить до 5\n'
    '💰 Доплата: 15 ₽ (за 8 дн.)\n\n'
    'Подтвердить изменение?'
)
UPSTREAM_CONFIRM_DECREASE = (
    '📱 <b>Подтверждение изменения</b>\n\n'
    'Текущее количество: 4 устройств\n'
    'Новое количество: 3 устройств\n\n'
    'Действие: уменьшить до 3\n'
    '💰 Возврат средств не производится\n\n'
    'Подтвердить изменение?\n'
    '⚠️ <b>Внимание!</b>\n'
    'У вас подключено 4 устройств.\n'
    'При уменьшении лимита до 3 все устройства будут сброшены.\n'
    'Вам нужно будет заново подключить нужные устройства.\n'
)
UPSTREAM_INSUFFICIENT = (
    '⚠️ <b>Недостаточно средств</b>\n\n'
    'Стоимость услуги: 15 ₽ (за 8 дн.)\n'
    'На балансе: 1 ₽\n'
    'Не хватает: 14 ₽\n\n'
    'Выберите способ пополнения. Сумма подставится автоматически.'
)
UPSTREAM_SUCCESS_INCREASE = '✅ Количество устройств увеличено!\n\n📱 Было: 4 → Стало: 5\n💰 Списано: 15 ₽'
UPSTREAM_SUCCESS_DECREASE = (
    '✅ Количество устройств уменьшено!\n\n'
    '📱 Было: 4 → Стало: 3\n\n'
    '🗑 Удалено устройств: 1\n'
    'ℹ️ Возврат средств не производится'
)
UPSTREAM_LIST = (
    '🔄 <b>Управление устройствами</b>\n\n'
    '📊 Всего подключено: 3 устройств\n'
    '📄 Страница 1 из 1\n\n'
    '<b>Подключенные устройства:</b>\n'
    '• macOS - Mac\n'
    '• iOS - iPhone 15\n'
    '• Android - Pixel\n'
    '\n💡 <b>Действия:</b>\n'
    '• Выберите устройство для сброса\n'
    '• Или сбросьте все устройства сразу'
)
UPSTREAM_LIST_BUTTONS = [
    ['✏️', '🔄 macOS - Mac'],
    ['✏️', '🔄 iOS - iPhone 15'],
    ['✏️', '🔄 Android - Pixel'],
    ['🔄 Сбросить все устройства'],
    ['⬅️ Назад'],
]
UPSTREAM_REVOKE_WARNING = (
    '⚠️ <b>Перевыпуск подписки</b>\n\n'
    'Это действие:\n'
    '• Сгенерирует новую ссылку подключения\n'
    '• Сбросит все подключённые устройства\n'
    '• Старая ссылка перестанет работать\n\n'
    'Продолжить?'
)
UPSTREAM_REVOKE_SUCCESS = (
    '✅ <b>Подписка перевыпущена!</b>\n\n'
    'Новая ссылка подключения готова. Старая ссылка больше не действительна.\n\n'
    'Все устройства были отключены.'
)


def _add_fourth_device(env) -> None:
    env['panel_devices'].append({'platform': 'Windows', 'deviceModel': 'PC', 'hwid': 'h4', 'createdAt': '2026-10-04'})


def _no_styles(markup) -> bool:
    return all(button.style is None for button in _buttons(markup))


@pytest.mark.asyncio
async def test_theme_off_settings_matches_upstream(theme_off, env):
    text, markup = await show_settings(env)
    assert text == UPSTREAM_SETTINGS
    assert _labels(markup) == UPSTREAM_SETTINGS_BUTTONS
    assert [b.callback_data for b in _buttons(markup)][-1] == 'menu_subscription'
    assert _no_styles(markup)


@pytest.mark.asyncio
async def test_theme_off_settings_without_devices_selection(theme_off, env, monkeypatch):
    monkeypatch.setattr(settings, 'DEVICES_SELECTION_ENABLED', False)
    text, _ = await show_settings(env)
    assert text == UPSTREAM_SETTINGS.replace('📱 Устройства: 3 / 4\n', '')


@pytest.mark.asyncio
async def test_theme_off_prompt_matches_upstream(theme_off, env):
    text, markup = await show_prompt(env)
    assert text == UPSTREAM_PROMPT
    assert _labels(markup) == UPSTREAM_PROMPT_BUTTONS
    assert _no_styles(markup)


@pytest.mark.asyncio
async def test_theme_off_confirm_matches_upstream(theme_off, env):
    callback = await show_confirm(env, 5)
    text, markup = _sent(callback.message.edit_text)
    assert text == UPSTREAM_CONFIRM
    assert _labels(markup) == [['✅ Подтвердить изменение'], ['❌ Отмена']]
    assert _no_styles(markup)


@pytest.mark.asyncio
async def test_theme_off_confirm_decrease_matches_upstream(theme_off, env):
    _add_fourth_device(env)
    callback = await show_confirm(env, 3)
    assert _sent(callback.message.edit_text)[0] == UPSTREAM_CONFIRM_DECREASE


@pytest.mark.asyncio
async def test_theme_off_insufficient_matches_upstream(theme_off, env):
    callback = await show_confirm(env, 5, balance=100)
    assert _sent(callback.message.answer)[0] == UPSTREAM_INSUFFICIENT


@pytest.mark.asyncio
async def test_theme_off_success_matches_upstream(theme_off, env):
    callback = await run_execute(env, 5, 1466)
    assert _sent(callback.message.edit_text)[0] == UPSTREAM_SUCCESS_INCREASE
    env['subscription'] = _subscription()
    _add_fourth_device(env)
    callback = await run_execute(env, 3, 0)
    assert _sent(callback.message.edit_text)[0] == UPSTREAM_SUCCESS_DECREASE


@pytest.mark.asyncio
async def test_theme_off_devices_list_matches_upstream(theme_off, env):
    text, markup = await show_list(env)
    assert text == UPSTREAM_LIST
    assert _labels(markup) == UPSTREAM_LIST_BUTTONS
    assert _no_styles(markup)


@pytest.mark.asyncio
async def test_theme_off_no_devices_screen_unchanged(theme_off, env):
    env['panel_devices'] = []
    callback = _callback('subscription_manage_devices')
    await devices.handle_device_management(callback, _user(env['subscription']), _db())
    text, markup = _sent(callback.message.edit_text)
    assert text == 'ℹ️ У вас нет подключенных устройств'
    assert [b.callback_data for b in _buttons(markup)] == ['back_to_menu']


@pytest.mark.asyncio
async def test_theme_off_revoke_matches_upstream(theme_off, env, monkeypatch):
    text, markup = await start_revoke(env)
    assert text == UPSTREAM_REVOKE_WARNING
    assert _labels(markup) == [['✅ Подтвердить'], ['⬅️ Назад']]
    assert _no_styles(markup)
    text, markup = await confirm_revoke(env, monkeypatch)
    assert text == UPSTREAM_REVOKE_SUCCESS
    assert _labels(markup) == [['🔗 Подключиться'], ['⬅️ Назад']]
    assert [b.callback_data for b in _buttons(markup)] == ['subscription_connect', 'menu_subscription']
    assert _no_styles(markup)


@pytest.mark.asyncio
async def test_theme_off_hardcoded_refund_alerts_unchanged(theme_off, env):
    # Баланс возвращается, если параллельный запрос уже применил то же изменение / лимит исчерпан.
    callback = await run_execute(env, 5, 1466, relocked=_subscription(device_limit=5))
    assert callback.answer.await_args_list[0].args[0] == '⚠️ Изменение уже применено. Баланс возвращён.'
    env['tariff'] = _tariff(max_device_limit=4)
    callback = await run_execute(env, 5, 1466)
    assert callback.answer.await_args_list[0].args[0] == '⚠️ Лимит устройств (4) превышен. Баланс возвращён.'


# --- Под темой: настройки ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_settings_ru(theme_on, env):
    text, markup = await show_settings(env)
    assert text == (
        f'<b>{_tag("gear")} Настройки подписки</b>\n\n'
        f'{_tag("chart")} Трафик: <b>12.3 ГБ</b> из ∞\n'
        f'{_tag("phone")} Устройства: <b>3 из 4</b>'
    )
    assert '🌐' not in text and 'Стран' not in text and 'Выберите' not in text
    assert not caption_exceeds_telegram_limit(text)
    assert _labels(markup) == [
        [f'{_tag("phone")} Количество устройств'],
        [f'{_tag("laptop")} Мои устройства'],
        [f'{_tag("refresh")} Перевыпустить подписку'],
        ['⬅️ Назад'],
    ]


@pytest.mark.asyncio
async def test_theme_on_settings_en_has_no_russian(theme_on, env):
    text, markup = await show_settings(env, 'en')
    assert text == (
        f'<b>{_tag("gear")} Subscription settings</b>\n\n'
        f'{_tag("chart")} Traffic: <b>12.3 GB</b> of ∞\n'
        f'{_tag("phone")} Devices: <b>3 of 4</b>'
    )
    for button in _buttons(theme_markup(markup)):
        assert not CYRILLIC_RE.search(button.text), button.text


@pytest.mark.asyncio
async def test_theme_on_settings_without_devices_selection_drops_devices(theme_on, env, monkeypatch):
    # Апстрим вырезает строку устройств подстрокой из шаблона; под темой её просто не собираем.
    monkeypatch.setattr(settings, 'DEVICES_SELECTION_ENABLED', False)
    text, _ = await show_settings(env)
    assert text == f'<b>{_tag("gear")} Настройки подписки</b>\n\n{_tag("chart")} Трафик: <b>12.3 ГБ</b> из ∞'


@pytest.mark.asyncio
async def test_theme_on_settings_devices_unknown_shows_limit_only(theme_on, env, monkeypatch):
    async def unknown(db_user, subscription=None):
        return '—'

    monkeypatch.setattr(purchase, 'get_current_devices_count', unknown)
    text, _ = await show_settings(env)
    assert text.endswith(f'{_tag("phone")} Устройства: <b>4</b>')


@pytest.mark.asyncio
async def test_theme_on_settings_keyboard_back_has_no_colour_or_icon(theme_on, env):
    _, markup = await show_settings(env)
    themed = _buttons(theme_markup(markup))
    back = themed[-1]
    assert back.callback_data == 'back_to_subscription'
    assert back.style is None and not back.icon_custom_emoji_id
    # Экран-меню: главного действия нет, зелёных кнопок тоже.
    assert all(button.style != 'success' for button in themed)
    for button in themed[:-1]:
        assert button.icon_custom_emoji_id, button.text
        assert '<tg-emoji' not in button.text


def test_back_to_subscription_opens_the_subscription_screen():
    dispatcher = MagicMock()
    purchase.register_handlers(dispatcher)
    filters = [
        call.args[1]
        for call in dispatcher.callback_query.register.call_args_list
        if call.args[0] is purchase.show_subscription_info
    ]
    for data in ('menu_subscription', 'back_to_subscription'):
        assert any(flt.resolve(SimpleNamespace(data=data)) for flt in filters), data


# --- Под темой: изменение количества устройств -------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_prompt_ru(theme_on, env):
    text, markup = await show_prompt(env)
    assert text == (
        f'<b>{_tag("phone")} Количество устройств</b>\n\n'
        f'{_tag("check")} Сейчас в подписке: <b>4</b>\n'
        f'{_tag("money_bag")} Доп. устройство: <b>55 ₽</b> в месяц\n\n'
        'Доплата считается за оставшиеся дни, при уменьшении деньги не возвращаются.'
    )
    assert not caption_exceeds_telegram_limit(text)
    assert _labels(markup) == [
        ['➖ 3'],
        [f'{_tag("check")} 4 · сейчас'],
        ['➕ 5 · +15 ₽'],
        ['➕ 6 · +29 ₽'],
        ['➕ 7 · +44 ₽'],
        ['⬅️ Назад'],
    ]
    assert [b.callback_data for b in _buttons(markup)][:5] == [f'change_devices_{n}' for n in (3, 4, 5, 6, 7)]
    assert _no_styles(markup)


@pytest.mark.asyncio
async def test_theme_on_prompt_en_has_no_russian(theme_on, env):
    text, markup = await show_prompt(env, 'en')
    assert text == (
        f'<b>{_tag("phone")} Device limit</b>\n\n'
        f'{_tag("check")} Currently in your subscription: <b>4</b>\n'
        f'{_tag("money_bag")} Extra device: <b>55 ₽</b> per month\n\n'
        'Raising the limit is charged for the days left; lowering it is not refunded.'
    )
    for button in _buttons(theme_markup(markup)):
        assert not CYRILLIC_RE.search(button.text.replace('₽', '')), button.text
    assert f'{_tag("check")} 4 · current' in [button.text for button in _buttons(markup)]


@pytest.mark.asyncio
async def test_theme_on_prompt_button_prices_match_the_confirmation(theme_on, env):
    _, markup = await show_prompt(env)
    callback = await show_confirm(env, 5)
    confirm_text, _ = _sent(callback.message.edit_text)
    assert '+15 ₽' in markup.inline_keyboard[2][0].text
    assert '<b>15 ₽</b>' in confirm_text


@pytest.mark.asyncio
async def test_theme_on_prompt_free_devices_inside_tariff(theme_on, env):
    # Устройства в пределах тарифа бесплатны: у подписки лимит ниже тарифного — кнопка «бесплатно».
    env['subscription'] = _subscription(device_limit=2)
    _, markup = await show_prompt(env)
    texts = [button.text for button in _buttons(markup)]
    assert texts[0] == f'{_tag("check")} 2 · сейчас'
    assert '➕ 3 · бесплатно' in texts


def test_theme_on_prompt_current_out_of_range_still_listed(theme_on):
    markup = kb.get_change_devices_keyboard(current_devices=60, language='ru', tariff=_tariff(max_device_limit=50))
    assert markup.inline_keyboard[0][0].text == f'{_tag("check")} 60 · сейчас'
    assert markup.inline_keyboard[0][0].callback_data == 'change_devices_60'


@pytest.mark.asyncio
async def test_theme_on_confirm_increase_ru(theme_on, env):
    callback = await show_confirm(env, 5)
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("phone")} Изменить количество устройств?</b>\n\n'
        f'{_tag("refresh")} Было <b>4</b> → станет <b>5</b>\n'
        f'{_tag("money_bag")} Доплата: <b>15 ₽</b> за 8 дней'
    )
    assert not caption_exceeds_telegram_limit(text)
    assert _labels(markup) == [[f'{_tag("check")} Подтвердить'], ['Отмена']]
    assert [b.callback_data for b in _buttons(markup)] == ['confirm_change_devices_5_1466', 'subscription_settings']


@pytest.mark.asyncio
async def test_theme_on_confirm_increase_en(theme_on, env):
    callback = await show_confirm(env, 5, 'en')
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("phone")} Change the device limit?</b>\n\n'
        f'{_tag("refresh")} From <b>4</b> to <b>5</b>\n'
        f'{_tag("money_bag")} Extra payment: <b>15 ₽</b> for 8 days'
    )
    assert not CYRILLIC_RE.search(text.replace('₽', ''))
    assert [button.text for button in _buttons(markup)][1] == 'Cancel'


@pytest.mark.asyncio
async def test_theme_on_confirm_shows_discount(theme_on, env):
    env['discount'] = 10
    callback = await show_confirm(env, 5)
    text, _ = _sent(callback.message.edit_text)
    cost = text.splitlines()[-1]
    assert re.fullmatch(_tag('money_bag') + r' Доплата: <b>\d+ ₽</b> за 8 дней \(скидка 10%, −\d+ ₽\)', cost), cost


@pytest.mark.asyncio
async def test_theme_on_confirm_free_increase(theme_on, env):
    env['subscription'] = _subscription(device_limit=2)
    callback = await show_confirm(env, 3)
    text, _ = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("phone")} Изменить количество устройств?</b>\n\n'
        f'{_tag("refresh")} Было <b>2</b> → станет <b>3</b>\n'
        f'{_tag("gift")} Доплата не нужна'
    )


@pytest.mark.asyncio
async def test_theme_on_confirm_decrease_warns_about_connected_devices(theme_on, env):
    _add_fourth_device(env)
    callback = await show_confirm(env, 3)
    text, _ = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("phone")} Изменить количество устройств?</b>\n\n'
        f'{_tag("refresh")} Было <b>4</b> → станет <b>3</b>\n'
        f'{_tag("info")} Деньги за уменьшение не возвращаются\n\n'
        f'{_tag("warning")} Подключено: <b>4</b>. При лимите <b>3</b> последние устройства отключатся'
    )
    # Пять анимированных эмодзи на сообщение — предел образца.
    assert text.count('<tg-emoji') == 4
    assert not caption_exceeds_telegram_limit(text)


@pytest.mark.asyncio
async def test_theme_on_confirm_decrease_without_extra_devices_has_no_warning(theme_on, env):
    callback = await show_confirm(env, 3)
    text, _ = _sent(callback.message.edit_text)
    assert text.splitlines()[-1] == f'{_tag("info")} Деньги за уменьшение не возвращаются'


@pytest.mark.asyncio
async def test_theme_on_confirm_keyboard_has_one_green_and_a_plain_cancel(theme_on, env):
    callback = await show_confirm(env, 5)
    _, markup = _sent(callback.message.edit_text)
    themed = _buttons(theme_markup(markup))
    confirm, cancel = themed
    assert confirm.style == 'success' and confirm.icon_custom_emoji_id
    assert cancel.style is None and not cancel.icon_custom_emoji_id
    assert sum(1 for button in themed if button.style == 'success') == 1


@pytest.mark.asyncio
async def test_theme_on_insufficient_funds_ru_en(theme_on, env):
    callback = await show_confirm(env, 5, balance=100)
    text, _ = _sent(callback.message.answer)
    assert text == (
        f'<b>{_tag("warning")} Недостаточно средств</b>\n\n'
        f'{_tag("money_bag")} Нужно: <b>15 ₽</b> за 8 дней\n'
        f'{_tag("coin")} На балансе: <b>1 ₽</b>\n'
        f'{_tag("cross")} Не хватает: <b>14 ₽</b>\n\n'
        'Пополните баланс — сумма подставится сама.'
    )
    assert not caption_exceeds_telegram_limit(text)
    callback = await show_confirm(env, 5, 'en', balance=100)
    text, _ = _sent(callback.message.answer)
    assert text == (
        f'<b>{_tag("warning")} Not enough funds</b>\n\n'
        f'{_tag("money_bag")} Needed: <b>15 ₽</b> for 8 days\n'
        f'{_tag("coin")} Balance: <b>1 ₽</b>\n'
        f'{_tag("cross")} Missing: <b>14 ₽</b>\n\n'
        'Top up your balance — the amount fills in automatically.'
    )


@pytest.mark.asyncio
async def test_theme_on_success_increase_ru_en(theme_on, env):
    callback = await run_execute(env, 5, 1466)
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("check")} Лимит устройств увеличен</b>\n\n'
        f'{_tag("phone")} Было <b>4</b> → стало <b>5</b>\n'
        f'{_tag("money_bag")} Списано: <b>15 ₽</b>'
    )
    assert [b.callback_data for b in _buttons(markup)] == ['back_to_menu']
    env['subscription'] = _subscription()
    callback = await run_execute(env, 5, 1466, 'en')
    text, _ = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("check")} Device limit increased</b>\n\n'
        f'{_tag("phone")} Was <b>4</b> → now <b>5</b>\n'
        f'{_tag("money_bag")} Charged: <b>15 ₽</b>'
    )


@pytest.mark.asyncio
async def test_theme_on_success_decrease_ru_en(theme_on, env):
    _add_fourth_device(env)
    callback = await run_execute(env, 3, 0)
    text, _ = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("check")} Лимит устройств уменьшен</b>\n\n'
        f'{_tag("phone")} Было <b>4</b> → стало <b>3</b>\n'
        f'{_tag("laptop")} Отключено лишних устройств: <b>1</b>\n\n'
        'Деньги за уменьшение не возвращаются'
    )
    env['subscription'] = _subscription()
    callback = await run_execute(env, 3, 0, 'en')
    text, _ = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("check")} Device limit decreased</b>\n\n'
        f'{_tag("phone")} Was <b>4</b> → now <b>3</b>\n\n'
        'No refund for lowering the limit'
    )


@pytest.mark.asyncio
async def test_theme_on_refund_alerts_are_localized(theme_on, env):
    relocked = _subscription(device_limit=5)
    callback = await run_execute(env, 5, 1466, relocked=relocked)
    assert callback.answer.await_args_list[0].args[0] == '⚠️ Изменение уже применено. Деньги возвращены на баланс.'
    callback = await run_execute(env, 5, 1466, 'en', relocked=relocked)
    alert = callback.answer.await_args_list[0].args[0]
    assert alert == '⚠️ The change was already applied. The money is back on your balance.'
    env['tariff'] = _tariff(max_device_limit=4)
    callback = await run_execute(env, 5, 1466, 'en')
    alert = callback.answer.await_args_list[0].args[0]
    assert alert == '⚠️ The device limit (4) was exceeded. The money is back on your balance.'
    assert not CYRILLIC_RE.search(alert)


# --- Под темой: управление устройствами --------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_devices_list_ru_en(theme_on, env):
    text, markup = await show_list(env)
    assert text == (
        f'<b>{_tag("laptop")} Мои устройства</b>\n\n'
        f'{_tag("phone")} Подключено: <b>3</b>\n\n'
        'Нажмите на устройство, чтобы сбросить его, на карандаш — чтобы переименовать.'
    )
    assert 'macOS' not in text  # имена устройств — на кнопках, в тексте не дублируются
    assert not caption_exceeds_telegram_limit(text)
    assert _labels(markup) == [
        ['✏️', f'{_tag("refresh")} macOS - Mac'],
        ['✏️', f'{_tag("refresh")} iOS - iPhone 15'],
        ['✏️', f'{_tag("refresh")} Android - Pixel'],
        [f'{_tag("refresh")} Сбросить все устройства'],
        ['⬅️ Назад'],
    ]
    text, markup = await show_list(env, 'en')
    assert text == (
        f'<b>{_tag("laptop")} My devices</b>\n\n'
        f'{_tag("phone")} Connected: <b>3</b>\n\n'
        'Tap a device to reset it, or the pencil to rename it.'
    )
    assert not CYRILLIC_RE.search(text)
    for button in _buttons(theme_markup(markup)):
        assert not CYRILLIC_RE.search(button.text), button.text


@pytest.mark.asyncio
async def test_theme_on_devices_list_pagination_mentions_page(theme_on, env):
    env['panel_devices'] = [{'platform': 'P', 'deviceModel': f'M{i}', 'hwid': f'h{i}'} for i in range(7)]
    text, markup = await show_list(env, page=2)
    assert text.splitlines()[2] == f'{_tag("phone")} Подключено: <b>7</b> · страница 2 из 2'
    assert 'current_page' in [button.callback_data for button in _buttons(markup)]
    text, _ = await show_list(env, 'en')
    assert text.splitlines()[2] == f'{_tag("phone")} Connected: <b>7</b> · page 1 of 2'


@pytest.mark.asyncio
async def test_theme_on_devices_list_keyboard_colours(theme_on, env):
    _, markup = await show_list(env)
    themed = _buttons(theme_markup(markup))
    by_data = {button.callback_data: button for button in themed}
    assert by_data['reset_all_devices'].style == 'danger'
    assert by_data['reset_all_devices'].icon_custom_emoji_id
    assert by_data['reset_device_0_1'].style is None and by_data['reset_device_0_1'].icon_custom_emoji_id
    # Список открывается с экрана подписки («📱 Устройства») — «Назад» ведёт туда, без цвета и иконки.
    assert 'subscription_settings' not in by_data
    assert by_data['menu_subscription'].style is None
    assert not by_data['menu_subscription'].icon_custom_emoji_id
    assert all(button.style != 'success' for button in themed)


async def _open_devices(env, language: str = 'ru', data: str = 'subscription_manage_devices'):
    callback = _callback(data)
    handler = devices.handle_devices_page if data.startswith('devices_page_') else devices.handle_device_management
    await handler(callback, _user(env['subscription'], language), _db())
    return _sent(callback.message.edit_text)


@pytest.mark.asyncio
@pytest.mark.parametrize('data', ['subscription_manage_devices', 'devices_page_1'])
async def test_theme_on_devices_list_offers_add_device(theme_on, env, data):
    _, markup = await _open_devices(env, data=data)
    texts = get_texts('ru')
    rows = [[(button.text, button.callback_data) for button in row] for row in markup.inline_keyboard]
    # «Добавить устройство» — синяя, прямо над «Назад».
    assert rows[-2:] == [
        [(texts.FORK_BUTTON_ADD_DEVICE, 'subscription_change_devices')],
        [(texts.BACK, 'menu_subscription')],
    ]
    themed = {button.callback_data: button for button in _buttons(theme_markup(markup))}
    assert themed['subscription_change_devices'].style == 'primary'
    assert themed['subscription_change_devices'].icon_custom_emoji_id
    assert all(button.style != 'success' for button in themed.values())
    _, markup = await _open_devices(env, 'en')
    assert not CYRILLIC_RE.search(_buttons(theme_markup(markup))[-2].text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('tariff', 'subscription'),
    [
        ({'device_price_kopeks': 0}, {}),
        ({'device_price_kopeks': None}, {}),
        ({}, {'status': 'expired', 'actual_status': 'expired'}),
    ],
)
async def test_theme_on_devices_list_without_add_device(theme_on, env, tariff, subscription):
    env['tariff'] = _tariff(**tariff)
    env['subscription'] = _subscription(**subscription)
    _, markup = await _open_devices(env)
    assert 'subscription_change_devices' not in [button.callback_data for button in _buttons(markup)]


@pytest.mark.asyncio
@pytest.mark.parametrize(('selection', 'shown'), [(True, True), (False, False)])
async def test_theme_on_devices_list_add_device_without_tariff(theme_on, env, monkeypatch, selection, shown):
    monkeypatch.setattr(settings, 'DEVICES_SELECTION_ENABLED', selection)
    env['subscription'] = _subscription(tariff_id=None)
    _, markup = await _open_devices(env)
    assert ('subscription_change_devices' in [button.callback_data for button in _buttons(markup)]) is shown


@pytest.mark.asyncio
async def test_theme_on_devices_list_back_in_multi_tariff_keeps_subscription_view(theme_on, env, monkeypatch):
    monkeypatch.setattr(type(settings), 'is_multi_tariff_enabled', lambda self: True)
    callback = _callback()
    await devices.show_devices_page(callback, _user(env['subscription']), env['panel_devices'], page=1, sub_id=7)
    _, markup = _sent(callback.message.edit_text)
    assert _buttons(markup)[-1].callback_data == 'sm:7'


@pytest.mark.asyncio
async def test_theme_off_devices_list_back_goes_to_settings(theme_off, env):
    _, markup = await _open_devices(env)
    assert _buttons(markup)[-1].callback_data == 'subscription_settings'


@pytest.mark.asyncio
async def test_theme_off_devices_list_has_no_add_device(theme_off, env):
    _, markup = await _open_devices(env)
    assert 'subscription_change_devices' not in [button.callback_data for button in _buttons(markup)]


@pytest.mark.asyncio
async def test_theme_on_no_devices_screen(theme_on, env):
    env['panel_devices'] = []
    callback = _callback('subscription_manage_devices')
    await devices.handle_device_management(callback, _user(env['subscription']), _db())
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("laptop")} Устройств нет</b>\n\nПодключите первое кнопкой «Подключиться» на экране подписки.'
    )
    assert [b.callback_data for b in _buttons(markup)] == ['menu_subscription']
    callback = _callback('subscription_manage_devices')
    await devices.handle_device_management(callback, _user(env['subscription'], 'en'), _db())
    assert not CYRILLIC_RE.search(_sent(callback.message.edit_text)[0])


@pytest.mark.asyncio
async def test_theme_on_reset_all_results(theme_on, env):
    callback = _callback('reset_all_devices')
    await devices.handle_all_devices_reset_from_management(callback, _user(env['subscription']), _db())
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("check")} Устройства сброшены</b>\n\n'
        f'{_tag("phone")} Сброшено: <b>3</b>\n\n'
        'Подключите нужные заново — кнопка «Подключиться» на экране подписки.'
    )
    assert [b.callback_data for b in _buttons(markup)] == ['back_to_menu']
    assert env['panel_devices'] == []


@pytest.mark.asyncio
async def test_theme_on_reset_all_partial_and_failed(theme_on, env, monkeypatch):
    class _Api:
        def __init__(self, results):
            self.results = list(results)

        async def get_user_devices(self, panel_user_id):
            return {'total': 2, 'devices': [{'hwid': 'a'}, {'hwid': 'b'}]}

        async def remove_device(self, panel_user_id, hwid):
            return self.results.pop(0)

    def install(results):
        api = _Api(results)

        class _Service:
            def get_api_client(self):
                @asynccontextmanager
                async def client():
                    yield api

                return client()

        monkeypatch.setattr('app.services.remnawave_service.RemnaWaveService', _Service)

    install([True, False])
    callback = _callback('reset_all_devices')
    await devices.handle_all_devices_reset_from_management(callback, _user(env['subscription'], 'en'), _db())
    text, _ = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("warning")} Not all devices were reset</b>\n\n'
        f'{_tag("check")} Reset: <b>1</b>\n'
        f'{_tag("cross")} Failed: <b>1</b>\n\n'
        'Please try again or contact support.'
    )
    install([False, False])
    callback = _callback('reset_all_devices')
    await devices.handle_all_devices_reset_from_management(callback, _user(env['subscription']), _db())
    text, _ = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("cross")} Не удалось сбросить устройства</b>\n\n'
        f'{_tag("phone")} Устройств в списке: <b>2</b>\n\n'
        'Попробуйте позже или напишите в поддержку.'
    )


@pytest.mark.asyncio
async def test_theme_on_reset_last_device_screen(theme_on, env):
    env['panel_devices'] = [{'platform': 'iOS', 'deviceModel': 'iPhone', 'hwid': 'only'}]
    callback = _callback('reset_device_0_1')
    await devices.handle_single_device_reset(callback, _user(env['subscription']), _db())
    assert callback.answer.await_args_list[0].args[0] == 'Устройство iOS - iPhone сброшено'
    text, _ = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("check")} Все устройства сброшены</b>\n\n'
        'Подключите нужные заново — кнопка «Подключиться» на экране подписки.'
    )


@pytest.mark.asyncio
async def test_theme_on_rename_prompt_escapes_current_and_fits(theme_on, env, monkeypatch):
    monkeypatch.setattr(devices, 'get_alias', AsyncMock(return_value='<i>Мой</i> & телефон'))
    callback = _callback('device_rename_0_1')
    state = MagicMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    await devices.start_device_rename(callback, _user(env['subscription']), _db(), state)
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("pencil")} Новое имя устройства</b>\n\n'
        f'{_tag("phone")} Сейчас: <code>&lt;i&gt;Мой&lt;/i&gt; &amp; телефон</code>\n\n'
        f'Отправьте новое имя — до {devices.ALIAS_MAX_LENGTH} символов. '
        'Чтобы убрать имя, отправьте <code>-</code>, отмена — /cancel.'
    )
    assert not caption_exceeds_telegram_limit(text)
    assert [button.text for button in _buttons(markup)] == ['Отмена']


@pytest.mark.asyncio
async def test_theme_on_rename_replies(theme_on, env, monkeypatch):
    monkeypatch.setattr(devices, 'upsert_alias', AsyncMock(side_effect=lambda db, uid, hwid, alias: alias))
    monkeypatch.setattr(devices, 'delete_alias', AsyncMock())

    async def replies(raw, language='ru'):
        message = MagicMock()
        message.text = raw
        message.answer = AsyncMock()
        state = MagicMock()
        state.get_data = AsyncMock(return_value={'rename_hwid': 'h1', 'rename_page': 1})
        state.clear = AsyncMock()
        await devices.process_device_rename(message, _user(env['subscription'], language), _db(), state)
        return [call.args[0] for call in message.answer.await_args_list]

    assert (await replies('Рабочий <ноут>'))[0] == 'Имя сохранено: <code>Рабочий &lt;ноут&gt;</code>'
    assert (await replies('-'))[0] == 'Имя сброшено к стандартному'
    sent = await replies('/cancel', 'en')
    assert sent == ['Renaming cancelled', 'Your devices are on the buttons below']


# --- Под темой: перевыпуск ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_revoke_warning_ru_en(theme_on, env):
    text, markup = await start_revoke(env)
    assert text == (
        f'<b>{_tag("warning")} Перевыпустить подписку?</b>\n\n'
        f'{_tag("link")} Создадим новую ссылку, старая перестанет работать\n'
        f'{_tag("phone")} Все устройства отключатся — подключить придётся заново'
    )
    assert not caption_exceeds_telegram_limit(text)
    confirm, back = _buttons(theme_markup(markup))
    assert confirm.style == 'danger' and confirm.icon_custom_emoji_id
    assert back.style is None and not back.icon_custom_emoji_id and back.callback_data == 'subscription_settings'
    text, markup = await start_revoke(env, 'en')
    assert text.startswith(f'<b>{_tag("warning")} Reissue your subscription?</b>')
    assert not CYRILLIC_RE.search(text)
    assert not any(CYRILLIC_RE.search(button.text) for button in _buttons(theme_markup(markup)))


@pytest.mark.asyncio
async def test_theme_on_revoke_success_and_error(theme_on, env, monkeypatch):
    text, markup = await confirm_revoke(env, monkeypatch)
    assert text == (
        f'<b>{_tag("check")} Подписка перевыпущена</b>\n\n'
        f'{_tag("link")} Новая ссылка готова, старая больше не работает\n\n'
        'Подключите устройства заново.'
    )
    themed = _buttons(theme_markup(markup))
    assert [button.callback_data for button in themed] == ['subscription_connect', 'back_to_subscription']
    assert [button.style for button in themed] == ['success', None]
    assert themed[0].icon_custom_emoji_id and not themed[1].icon_custom_emoji_id
    env['subscription'] = _subscription()
    text, markup = await confirm_revoke(env, monkeypatch, new_url=None)
    assert text == f'<b>{_tag("cross")} Не удалось перевыпустить подписку</b>\n\nПопробуйте чуть позже.'
    assert [button.callback_data for button in _buttons(markup)] == ['back_to_subscription']
    env['subscription'] = _subscription()
    text, _ = await confirm_revoke(env, monkeypatch, 'en')
    assert text.startswith(f'<b>{_tag("check")} Subscription reissued</b>')
    assert not CYRILLIC_RE.search(text)


# --- Шаблоны форка форматируются настоящими kwargs вызова --------------------------------

REAL_KWARGS = {
    'CHANGE_DEVICES_PROMPT_TARIFF': {'current_devices': 3, 'price': '55 ₽'},
    'CHANGE_DEVICES_PROMPT': {'current_devices': 3},
    'DEVICE_CHANGE_EXTRA_COST': {'amount': '14 ₽', 'period': '8 days', 'months': '8 days'},
    'DEVICE_CHANGE_DISCOUNT_INFO': {'percent': 10, 'amount': '1 ₽'},
    'DEVICE_CHANGE_RESET_WARNING': {'connected': 3, 'new': 2},
    'DEVICE_CHANGE_RESULT_LINE': {'old': 3, 'new': 4},
    'DEVICE_CHANGE_CHARGED': {'amount': '14 ₽'},
    'DEVICE_CHANGE_DEVICES_REMOVED': {'count': 1},
    'DEVICE_RESET_SUCCESS': {'device': 'iOS - iPhone'},
    'DEVICE_RESET_ALL_SUCCESS_MESSAGE': {'count': 3},
    'DEVICE_RESET_PARTIAL_MESSAGE': {'success': 1, 'failed': 1},
    'DEVICE_RESET_ALL_FAILED_MESSAGE': {'total': 2},
    'DEVICE_RENAME_PROMPT': {'current': 'x', 'max_len': 32},
    'DEVICE_RENAME_TOO_LONG': {'max_len': 32},
    'DEVICE_RENAME_SAVED': {'name': 'x'},
    'FORK_DEVICES_CONFIRM': {'current': 3, 'new': 4, 'cost': 'c'},
    'FORK_DEVICES_COST_FOR': {'amount': '14 ₽', 'period': '8 days'},
    'FORK_DEVICES_INSUFFICIENT': {'required': 'r', 'balance': 'b', 'missing': 'm'},
    'FORK_DEVICES_OPTION_CURRENT': {'count': 3},
    'FORK_DEVICES_OPTION_MORE': {'count': 4, 'price': '14 ₽'},
    'FORK_DEVICES_OPTION_FREE': {'count': 4},
    'FORK_DEVICES_OPTION_LESS': {'count': 2},
    'FORK_DEVICES_LIST_COUNT': {'total': 3},
    'FORK_DEVICES_LIST_PAGE': {'page': 1, 'pages': 2},
    'FORK_BUTTON_DEVICE_RESET': {'device': 'iOS - iPhone'},
    'FORK_DEVICES_LIMIT_REFUNDED': {'limit': 10},
}
# Тексты без плейсхолдеров: тоже проходят проверку на анимированные эмодзи и мусор.
PLAIN_KEYS = (
    'FORK_SUB_SETTINGS_TITLE',
    'FORK_DEVICES_NONE',
    'FORK_DEVICES_LIST_TITLE',
    'FORK_DEVICES_LIST_HINT',
    'FORK_DEVICES_ALREADY_APPLIED',
    'DEVICE_CHANGE_INCREASE_SUCCESS',
    'DEVICE_CHANGE_DECREASE_SUCCESS',
    'DEVICE_CHANGE_FREE',
    'DEVICE_CHANGE_NO_REFUND',
    'DEVICE_CHANGE_NO_REFUND_INFO',
    'DEVICE_RESET_ALL_DONE',
    'SUBSCRIPTION_REVOKE_WARNING',
    'SUBSCRIPTION_REVOKE_SUCCESS',
    'SUBSCRIPTION_REVOKE_ERROR',
)


@pytest.mark.parametrize('language', ['ru', 'en'])
@pytest.mark.parametrize('key', sorted(REAL_KWARGS))
def test_templates_format_with_real_call_kwargs(theme_on, key, language):
    template = get_texts(language).get(key)
    assert template is not None, key
    rendered = template.format(**REAL_KWARGS[key])
    assert '{' not in rendered and '}' not in rendered
    if language == 'en':
        assert not CYRILLIC_RE.search(rendered.replace('₽', '')), rendered


@pytest.mark.parametrize('language', ['ru', 'en'])
@pytest.mark.parametrize('key', [*sorted(REAL_KWARGS), *PLAIN_KEYS])
def test_rewritten_texts_follow_the_visual_language(theme_on, key, language):
    value = get_texts(language).get(key)
    assert value.count('<tg-emoji') <= 5, key
    for banned in ('<blockquote', '<h1', '<h2', '<h3', '▰', '▓', '━'):
        assert banned not in value, (key, banned)
    assert '\n\n\n' not in value, key
    if value.startswith('<b>'):
        # Заголовок: не больше одного эмодзи в начале первой строки, дальше — пустая строка и блоки.
        first, _, rest = value.partition('\n')
        assert first.count('<tg-emoji') <= 1, key
        assert not rest or rest.startswith('\n'), key


def _assert_screen_pattern(name: str, text: str) -> None:
    """Образец экрана подписки: <b>заголовок</b>, пустая строка, блоки через пустую строку, у строк данных — иконка."""
    assert text.startswith('<b>') and text.splitlines()[0].endswith('</b>'), name
    assert text.splitlines()[0].count('<tg-emoji') == 1, name
    assert text.count('<tg-emoji') <= 5, name
    assert '\n\n\n' not in text and not text.endswith('\n'), name
    assert not caption_exceeds_telegram_limit(text), name
    for banned in ('<blockquote', '<h1', '<p>', '▰', '▓', '━'):
        assert banned not in text, (name, banned)
    blocks = text.split('\n\n')
    assert len(blocks) >= 2, name
    for block in blocks[1:]:
        lines = block.split('\n')
        if all(line.startswith('<tg-emoji') for line in lines):
            continue
        # Блок без иконок — одна короткая строка-пояснение; смешанных блоков быть не должно.
        assert not any(line.startswith('<tg-emoji') for line in lines), (name, block)
        assert len(lines) == 1, (name, block)


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_theme_on_screens_follow_the_approved_pattern(theme_on, env, monkeypatch, language):
    screens = {'settings': (await show_settings(env, language))[0], 'prompt': (await show_prompt(env, language))[0]}
    screens['confirm'] = _sent((await show_confirm(env, 5, language)).message.edit_text)[0]
    screens['insufficient'] = _sent((await show_confirm(env, 5, language, balance=100)).message.answer)[0]
    _add_fourth_device(env)
    screens['confirm-decrease'] = _sent((await show_confirm(env, 3, language)).message.edit_text)[0]
    screens['success-decrease'] = _sent((await run_execute(env, 3, 0, language)).message.edit_text)[0]
    env['subscription'] = _subscription()
    screens['success-increase'] = _sent((await run_execute(env, 5, 1466, language)).message.edit_text)[0]
    screens['list'] = (await show_list(env, language))[0]
    screens['revoke'] = (await start_revoke(env, language))[0]
    screens['revoke-done'] = (await confirm_revoke(env, monkeypatch, language))[0]
    env['subscription'] = _subscription()
    screens['revoke-error'] = (await confirm_revoke(env, monkeypatch, language, new_url=None))[0]
    for name, text in screens.items():
        _assert_screen_pattern(name, text)
        if language == 'en':
            assert not CYRILLIC_RE.search(text.replace('₽', '')), (name, text)
