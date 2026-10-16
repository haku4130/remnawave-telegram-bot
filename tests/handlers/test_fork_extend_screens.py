"""Продление и смена тарифа — под темой форка.

Без темы каждый экран обязан остаться байт-в-байт как у апстрима: снимки ниже сняты с кода
до переписывания (2c1076ae), тема выключена. Под темой тексты собирают FORK_*-ключи и
переопределённые ключи апстрима (app/localization/fork/), клавиатуры — те же функции, что у бота.
Тесты гоняют настоящие обработчики с заглушками на всё, что ходит наружу.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.keyboards.inline as kb
from app.config import settings
from app.handlers.subscription import purchase, tariff_purchase as tp
from app.localization import loader
from app.localization.texts import get_texts
from app.middlewares.premium_theme import theme_markup
from app.services.pricing_engine import PricingEngine
from app.utils.message_patch import caption_exceeds_telegram_limit


TAG_RE = re.compile(r'<[^>]+>')
CYRILLIC_RE = re.compile('[а-яА-ЯёЁ]')
NEW_END_DATE = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def _tag(name: str) -> str:
    from app.localization.emoji_catalog import tag

    return tag(name)


def _tariff(**overrides) -> SimpleNamespace:
    data = {
        'id': 1,
        'name': 'Стандартный',
        'is_active': True,
        'is_daily': False,
        'is_free': False,
        'traffic_limit_gb': 0,
        'device_limit': 3,
        'max_device_limit': 10,
        'device_price_kopeks': 5500,
        'period_prices': {'30': 19900, '90': 49900, '180': 89900},
        'highlight_period_days': None,
        'allowed_squads': [],
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _subscription(**overrides) -> SimpleNamespace:
    data = {
        'id': 7,
        'status': 'active',
        'is_trial': False,
        'tariff_id': 1,
        'device_limit': 3,
        'end_date': datetime.now(UTC) + timedelta(days=5),
        'remnawave_id': 'uuid',
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _user(language: str = 'ru', balance: int = 100_000, subscription=None, promo_group=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        language=language,
        subscription=subscription,
        balance_kopeks=balance,
        remnawave_id='uuid',
        telegram_id=42,
        promo_group_id=None,
        promo_group=promo_group,
    )


def _callback(data: str = 'x') -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    callback.bot = MagicMock()
    return callback


def _state() -> MagicMock:
    state = MagicMock()
    state.update_data = AsyncMock()
    state.clear = AsyncMock()
    return state


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


def _alert(callback) -> str:
    return callback.answer.await_args.args[0]


@pytest.fixture
def prod_like(monkeypatch):
    monkeypatch.setattr(settings, 'SALES_MODE', 'tariffs')
    monkeypatch.setattr(settings, 'MULTI_TARIFF_ENABLED', False, raising=False)
    monkeypatch.setattr(type(settings), 'is_multi_tariff_enabled', lambda self: False)
    monkeypatch.setattr(type(settings), 'is_auto_purchase_after_topup_enabled', lambda self: True)
    monkeypatch.setattr(settings, 'TARIFF_SWITCH_UPGRADE_ENABLED', True)
    monkeypatch.setattr(settings, 'TARIFF_SWITCH_DOWNGRADE_ENABLED', True)


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
        'tariffs': [_tariff()],
        'price': SimpleNamespace(
            final_total=19900, original_total=19900, promo_group_discount=0, promo_offer_discount=0
        ),
        'charge_ok': True,
        'extend_raises': False,
        'carts': [],
    }

    async def tariff_by_id(db, tariff_id):
        return state['tariff']

    async def sub_for_user(db, sub_id, user_id):
        return state['subscription']

    async def sub_by_user(db, user_id):
        return state['subscription']

    async def tariffs_for_user(db, promo_group_id):
        return state['tariffs']

    async def calculate(tariff, period, device_limit=None, user=None, **kwargs):
        return state['price']

    async def extend(db, subscription, days, **kwargs):
        if state['extend_raises']:
            raise RuntimeError('boom')
        subscription.end_date = NEW_END_DATE
        return subscription

    async def lock_user(db, user_id):
        return state['user']

    async def subtract(*args, **kwargs):
        return state['charge_ok']

    class _SubscriptionService:
        create_remnawave_user = AsyncMock()
        update_remnawave_user = AsyncMock()

    class _Admin:
        def __init__(self, bot):
            self.send_subscription_purchase_notification = AsyncMock()

    def payment_methods(amount_kopeks, language='ru'):
        return SimpleNamespace(
            inline_keyboard=[
                [kb.InlineKeyboardButton(text='💳 RollyPay', callback_data=f'topup_amount|rollypay|{amount_kopeks}')],
                [kb.InlineKeyboardButton(text='⬅️ Назад', callback_data='balance_topup')],
            ]
        )

    monkeypatch.setattr(tp, 'get_tariff_by_id', tariff_by_id)
    monkeypatch.setattr(tp, 'get_subscription_by_id_for_user', sub_for_user)
    monkeypatch.setattr(tp, 'get_subscription_by_user_id', sub_by_user)
    monkeypatch.setattr('app.database.crud.subscription.get_subscription_by_user_id', sub_by_user)
    monkeypatch.setattr(tp, 'get_tariffs_for_user', tariffs_for_user)
    monkeypatch.setattr(tp, 'extend_subscription', extend)
    monkeypatch.setattr(tp, 'subtract_user_balance', subtract)
    monkeypatch.setattr(tp, 'create_transaction', AsyncMock())
    monkeypatch.setattr(tp, 'SubscriptionService', _SubscriptionService)
    monkeypatch.setattr(tp, 'AdminNotificationService', _Admin)
    monkeypatch.setattr('app.database.crud.user.lock_user_for_pricing', lock_user)
    monkeypatch.setattr('app.services.pricing_engine.pricing_engine.calculate_tariff_purchase_price', calculate)
    monkeypatch.setattr(tp.user_cart_service, 'save_user_cart', AsyncMock())
    monkeypatch.setattr(tp.user_cart_service, 'delete_user_cart', AsyncMock())
    monkeypatch.setattr(kb, 'get_payment_methods_keyboard', payment_methods)
    monkeypatch.setattr(
        PricingEngine, 'resolve_promo_group', staticmethod(lambda user: getattr(user, 'promo_group', None))
    )
    state['user'] = _user(subscription=state['subscription'])
    return state


def _db() -> MagicMock:
    db = MagicMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()
    return db


# --- запуск обработчиков ------------------------------------------------------------------


async def show_periods(env, language: str = 'ru', promo_group=None):
    callback = _callback('subscription_extend')
    await tp.show_tariff_extend(
        callback, _user(language, subscription=env['subscription'], promo_group=promo_group), _db()
    )
    return callback


async def pick_period(env, language: str = 'ru', balance: int = 100_000, period: int = 30):
    callback = _callback(f'tariff_extend:7:1:{period}')
    user = _user(language, balance=balance, subscription=env['subscription'])
    await tp.select_tariff_extend_period(callback, user, _db(), _state())
    return callback


async def confirm(env, language: str = 'ru', period: int = 30):
    callback = _callback(f'tariff_ext_confirm:7:1:{period}')
    user = _user(language, subscription=env['subscription'])
    env['user'] = user
    state = _state()
    await tp.confirm_tariff_extend(callback, user, _db(), state)
    return callback


async def restore_cart(env, language: str = 'ru', balance: int = 100_000, discount_percent: int = 0):
    callback = _callback('return_to_saved_cart')
    user = _user(language, balance=balance, subscription=env['subscription'])
    cart = {
        'cart_mode': 'extend',
        'tariff_id': 1,
        'subscription_id': 7,
        'period_days': 30,
        'total_price': 19900,
        'device_limit': 4,
        'discount_percent': discount_percent,
    }
    await tp.return_to_saved_tariff_cart(callback, _state(), user, _db(), cart)
    return callback


async def show_switch(env, language: str = 'ru'):
    callback = _callback('instant_switch')
    user = _user(language, subscription=env['subscription'])
    await tp.show_instant_switch_list(callback, user, _db(), _state())
    return callback


async def show_legacy_switch(env, language: str = 'ru'):
    callback = _callback('tariff_switch')
    user = _user(language, subscription=env['subscription'])
    await tp.show_tariff_switch_list(callback, user, _db(), _state())
    return callback


async def extend_entry(env, language: str = 'ru'):
    callback = _callback('subscription_extend')
    user = _user(language, subscription=env['subscription'])
    await purchase.handle_extend_subscription(callback, user, _db(), _state())
    return callback


def _no_styles(markup) -> bool:
    return all(button.style is None for button in _buttons(markup))


def _greens(markup) -> int:
    return sum(1 for button in _buttons(theme_markup(markup)) if button.style == 'success')


def _promo_group(percent: int = 10):
    return SimpleNamespace(
        period_discounts={'30': percent},
        get_discount_percent=lambda category, period: percent if category == 'period' and period == 30 else 0,
    )


def _discounted_price(env) -> None:
    env['price'] = SimpleNamespace(
        final_total=17910, original_total=19900, promo_group_discount=1990, promo_offer_discount=0
    )


# --- Без темы: как у апстрима -------------------------------------------------------------

# Снято с кода до переписывания (2c1076ae), тема выключена.
UPSTREAM_PERIODS = (
    '🔄 <b>Продление подписки</b>\n\n'
    '📦 Тариф: <b>Стандартный</b>\n'
    '📊 Трафик: Безлимит\n'
    '📱 Устройств: 3\n\n'
    'Выберите период продления:'
)
UPSTREAM_PERIODS_WITH_HINT = UPSTREAM_PERIODS.replace(
    '</b>\n\n📦', '</b>\n🎁 <i>Скидки зависят от выбранного периода</i>\n\n📦', 1
)
UPSTREAM_PERIOD_BUTTONS = [['30 дней — 199 ₽'], ['90 дней — 499 ₽'], ['180 дней — 899 ₽'], ['⬅️ Назад']]
UPSTREAM_CONFIRM = (
    '✅ <b>Подтверждение продления</b>\n\n'
    '📦 Тариф: <b>Стандартный</b>\n'
    '📊 Трафик: Безлимит\n'
    '📱 Устройств: 3\n'
    '📅 Период: 30 дней\n\n'
    '💰 <b>К оплате: 199 ₽</b>\n\n'
    '💳 Ваш баланс: 1000 ₽\n'
    'После оплаты: 801 ₽'
)
UPSTREAM_CONFIRM_DISCOUNT = (
    '✅ <b>Подтверждение продления</b>\n\n'
    '📦 Тариф: <b>Стандартный</b>\n'
    '📊 Трафик: Безлимит\n'
    '📱 Устройств: 3\n'
    '📅 Период: 30 дней\n\n'
    '🎁 Скидка: 10% (-19.90 ₽)\n'
    '💰 <b>К оплате: 179.10 ₽</b>\n\n'
    '💳 Ваш баланс: 1000 ₽\n'
    'После оплаты: 820.90 ₽'
)
UPSTREAM_INSUFFICIENT = (
    '❌ <b>Недостаточно средств</b>\n\n'
    '📦 Тариф: <b>Стандартный</b>\n'
    '📅 Период: 30 дней\n'
    '💰 Стоимость: 199 ₽\n\n'
    '💳 Ваш баланс: 50 ₽\n'
    '⚠️ Не хватает: <b>149 ₽</b>\n\n'
    '🛒 <i>Корзина сохранена! После пополнения баланса подписка будет продлена автоматически.</i>'
)
UPSTREAM_SUCCESS = (
    '🎉 <b>Подписка успешно продлена!</b>\n\n'
    '📦 Тариф: <b>Стандартный</b>\n'
    '📊 Трафик: Безлимит\n'
    '📱 Устройств: 3\n'
    '📅 Добавлено: 30 дней\n'
    '💰 Списано: 199 ₽'
)
UPSTREAM_STILL_INSUFFICIENT = (
    '❌ <b>Все еще недостаточно средств</b>\n\n'
    '📦 Тариф: <b>Стандартный</b>\n'
    '📅 Период: 30 дней\n'
    '💰 Стоимость: 199 ₽\n\n'
    '💳 Ваш баланс: 50 ₽\n'
    '⚠️ Не хватает: <b>149 ₽</b>'
)
UPSTREAM_RESTORED_DISCOUNT = UPSTREAM_CONFIRM.replace(
    '📅 Период: 30 дней\n\n', '📅 Период: 30 дней\n\n🎁 Скидка: 10% (-22.11 ₽)\n'
)
UPSTREAM_SWITCH_NONE = (
    '😔 <b>Нет доступных тарифов для переключения</b>\n\nВы уже используете единственный доступный тариф.'
)
UPSTREAM_SWITCH_EXPIRED = (
    '❌ <b>Переключение недоступно</b>\n\nУ вашей подписки не осталось активных дней.\nОформите новый тариф с нуля.'
)
UPSTREAM_SWITCH_DISABLED = '🚫 <b>Смена тарифа недоступна</b>\n\nАдминистратор отключил возможность смены тарифа.'
UPSTREAM_CHOOSE_TARIFF = (
    '🔄 <b>Выберите тариф для продления</b>\n\n'
    'Для продления подписки необходимо выбрать тариф.\n'
    'Подписка будет обновлена с параметрами выбранного тарифа.'
)
UPSTREAM_E0 = '🎯 <b>Пробный период заканчивается</b>\n\nЧтобы продолжить пользоваться VPN, выберите подходящий тариф.'
UPSTREAM_E2 = (
    '📦 <b>Выберите тариф для продления</b>\n\n'
    'Ваша текущая подписка была создана до введения тарифов.\n'
    'Для продления необходимо выбрать один из доступных тарифов.\n\n'
    '⚠️ Ваша текущая подписка продолжит действовать до окончания срока.'
)


@pytest.mark.asyncio
async def test_theme_off_period_screen_matches_upstream(theme_off, env):
    callback = await show_periods(env)
    text, markup = _sent(callback.message.edit_text)
    assert text == UPSTREAM_PERIODS
    assert _labels(markup) == UPSTREAM_PERIOD_BUTTONS
    assert [b.callback_data for b in _buttons(markup)] == [
        'tariff_extend:7:1:30',
        'tariff_extend:7:1:90',
        'tariff_extend:7:1:180',
        'menu_subscription',
    ]
    assert _no_styles(markup)


@pytest.mark.asyncio
async def test_theme_off_period_screen_discount_hint_and_badge(theme_off, env):
    callback = await show_periods(env, promo_group=_promo_group())
    text, markup = _sent(callback.message.edit_text)
    assert text == UPSTREAM_PERIODS_WITH_HINT
    assert _labels(markup)[0] == ['30 дней — 179.10 ₽ 🔥−10%']
    env['tariff'].highlight_period_days = 90
    callback = await show_periods(env)
    assert _labels(_sent(callback.message.edit_text)[1])[1] == ['⭐ выгодно · 90 дней — 499 ₽']


@pytest.mark.asyncio
async def test_theme_off_confirm_matches_upstream(theme_off, env):
    callback = await pick_period(env)
    text, markup = _sent(callback.message.edit_text)
    assert text == UPSTREAM_CONFIRM
    assert _labels(markup) == [['✅ Подтвердить продление'], ['⬅️ Назад']]
    assert [b.callback_data for b in _buttons(markup)] == ['tariff_ext_confirm:7:1:30', 'subscription_extend']
    assert _no_styles(markup)
    _discounted_price(env)
    callback = await pick_period(env)
    assert _sent(callback.message.edit_text)[0] == UPSTREAM_CONFIRM_DISCOUNT


@pytest.mark.asyncio
async def test_theme_off_insufficient_matches_upstream(theme_off, env):
    callback = await pick_period(env, balance=5000)
    text, markup = _sent(callback.message.edit_text)
    assert text == UPSTREAM_INSUFFICIENT
    assert _labels(markup) == [['💳 RollyPay'], ['⬅️ Назад']]
    assert _no_styles(markup)


@pytest.mark.asyncio
async def test_theme_off_success_and_errors_match_upstream(theme_off, env):
    callback = await confirm(env)
    text, markup = _sent(callback.message.edit_text)
    assert text == UPSTREAM_SUCCESS
    assert _labels(markup) == [['📱 Моя подписка'], ['⬅️ Назад']]
    env['charge_ok'] = False
    callback = await confirm(env)
    assert _sent(callback.message.edit_text)[0] == '❌ Ошибка списания баланса'
    env['charge_ok'] = True
    env['extend_raises'] = True
    callback = await confirm(env)
    assert _sent(callback.message.edit_text)[0] == '❌ Произошла ошибка при продлении подписки'


@pytest.mark.asyncio
async def test_theme_off_cart_restore_matches_upstream(theme_off, env):
    callback = await restore_cart(env, discount_percent=10)
    assert _sent(callback.message.edit_text)[0] == UPSTREAM_RESTORED_DISCOUNT
    assert _alert(callback) == '✅ Корзина восстановлена!'
    callback = await restore_cart(env, balance=5000)
    text, markup = _sent(callback.message.edit_text)
    assert text == UPSTREAM_STILL_INSUFFICIENT
    assert _labels(markup) == [['💳 RollyPay'], ['⬅️ Назад']]


@pytest.mark.asyncio
async def test_theme_off_switch_branches_match_upstream(theme_off, env, monkeypatch):
    callback = await show_switch(env)
    text, markup = _sent(callback.message.edit_text)
    assert text == UPSTREAM_SWITCH_NONE
    assert _labels(markup) == [['⬅️ Назад']]
    env['subscription'].end_date = datetime.now(UTC) - timedelta(days=1)
    for runner in (show_switch, show_legacy_switch):
        callback = await runner(env)
        text, markup = _sent(callback.message.edit_text)
        assert text == UPSTREAM_SWITCH_EXPIRED
        assert _labels(markup) == [['📦 Купить тариф'], ['⬅️ Назад']]
        assert _no_styles(markup)
    env['subscription'].end_date = datetime.now(UTC) + timedelta(days=5)
    monkeypatch.setattr(settings, 'TARIFF_SWITCH_UPGRADE_ENABLED', False)
    monkeypatch.setattr(settings, 'TARIFF_SWITCH_DOWNGRADE_ENABLED', False)
    for runner in (show_switch, show_legacy_switch):
        assert _sent((await runner(env)).message.edit_text)[0] == UPSTREAM_SWITCH_DISABLED


@pytest.mark.asyncio
async def test_theme_off_legacy_pick_and_entry_branches_match_upstream(theme_off, env):
    env['subscription'].tariff_id = None
    env['tariffs'] = [_tariff(id=1, name='Стандартный'), _tariff(id=2, name='Про')]
    text, markup = _sent((await show_periods(env)).message.edit_text)
    assert text == UPSTREAM_CHOOSE_TARIFF
    assert _labels(markup) == [['📦 Стандартный'], ['📦 Про'], ['◀️ Назад']]
    env['subscription'] = None
    text, markup = _sent((await extend_entry(env)).message.edit_text)
    assert text == UPSTREAM_E0
    assert _labels(markup) == [['💎 Купить подписку'], ['✖️ Закрыть']]
    assert _no_styles(markup)
    env['subscription'] = _subscription(is_trial=True, tariff_id=None)
    text, markup = _sent((await extend_entry(env)).message.edit_text)
    assert text == UPSTREAM_E0
    env['subscription'] = _subscription(tariff_id=None)
    text, markup = _sent((await extend_entry(env)).message.edit_text)
    assert text == UPSTREAM_E2
    assert _labels(markup) == [['📦 Выбрать тариф'], ['⬅️ Назад']]
    assert _no_styles(markup)


# --- Под темой: выбор периода -------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_period_screen_ru(theme_on, env):
    callback = await show_periods(env)
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("refresh")} Продление подписки</b>\n'
        'Тариф: <b>Стандартный</b>\n'
        'Трафик: ∞ · Устройств: 3\n'
        'Выберите срок продления.'
    )
    assert not caption_exceeds_telegram_limit(text)
    assert _labels(markup) == UPSTREAM_PERIOD_BUTTONS
    themed = _buttons(theme_markup(markup))
    assert all(button.style is None for button in themed)  # выбор срока — не «главное действие»
    assert themed[-1].callback_data == 'menu_subscription' and not themed[-1].icon_custom_emoji_id


@pytest.mark.asyncio
async def test_theme_on_period_screen_en_has_no_russian_units(theme_on, env):
    env['tariff'].name = 'Standard'
    callback = await show_periods(env, 'en')
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("refresh")} Renew subscription</b>\n'
        'Plan: <b>Standard</b>\n'
        'Traffic: ∞ · Devices: 3\n'
        'Choose a renewal period.'
    )
    assert _labels(markup) == [['30 days — 199 ₽'], ['90 days — 499 ₽'], ['180 days — 899 ₽'], ['⬅️ Back']]


@pytest.mark.asyncio
async def test_theme_on_period_screen_discount_hint_and_prices(theme_on, env):
    callback = await show_periods(env, promo_group=_promo_group())
    text, markup = _sent(callback.message.edit_text)
    assert text.endswith('Выберите срок продления.\nСкидка зависит от срока.')
    assert _labels(markup)[0] == ['30 дней — 179.10 ₽ · −10%']
    assert '🔥' not in ''.join(button.text for button in _buttons(markup))
    callback = await show_periods(env, 'en', promo_group=_promo_group())
    assert _sent(callback.message.edit_text)[0].endswith('The discount depends on the period.')
    assert _labels(_sent(callback.message.edit_text)[1])[0] == ['30 days — 179.10 ₽ · −10%']


@pytest.mark.asyncio
async def test_theme_on_period_screen_counts_extra_devices(theme_on, env):
    env['subscription'].device_limit = 4
    callback = await show_periods(env)
    text, markup = _sent(callback.message.edit_text)
    assert 'Трафик: ∞ · Устройств: 4' in text
    assert _labels(markup)[0] == ['30 дней — 254 ₽']


@pytest.mark.asyncio
async def test_theme_on_period_screen_best_value_badge_stays_in_front(theme_on, env):
    env['tariff'].highlight_period_days = 90
    callback = await show_periods(env, 'en')
    assert _labels(_sent(callback.message.edit_text)[1])[1] == ['⭐ best value · 90 days — 499 ₽']


@pytest.mark.asyncio
async def test_theme_on_legacy_pick_screen(theme_on, env):
    env['subscription'].tariff_id = None
    env['tariffs'] = [_tariff(id=1, name='Стандартный'), _tariff(id=2, name='Про <b>')]
    callback = await show_periods(env)
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("refresh")} Выберите тариф для продления</b>\nПодписка обновится по параметрам выбранного тарифа.'
    )
    assert _labels(markup) == [
        [f'{_tag("gem")} Стандартный'],
        [f'{_tag("gem")} Про <b>'],
        ['◀️ Назад'],
    ]
    themed = _buttons(theme_markup(markup))
    assert themed[0].icon_custom_emoji_id and themed[0].style is None
    assert not themed[-1].icon_custom_emoji_id


# --- Под темой: подтверждение -------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_confirm_ru(theme_on, env):
    callback = await pick_period(env)
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("refresh")} Продлить подписку?</b>\n'
        'Тариф: <b>Стандартный</b>\n'
        'Трафик: ∞ · Устройств: 3\n'
        'Срок: 30 дней\n'
        'К оплате: <b>199 ₽</b>\n'
        'Баланс: 1000 ₽ → останется 801 ₽'
    )
    assert not caption_exceeds_telegram_limit(text)
    assert text.count('<tg-emoji') == 1
    assert _labels(markup) == [[f'{_tag("check")} Подтвердить'], ['⬅️ Назад']]
    assert [b.callback_data for b in _buttons(markup)] == ['tariff_ext_confirm:7:1:30', 'subscription_extend']


@pytest.mark.asyncio
async def test_theme_on_confirm_en(theme_on, env):
    env['tariff'].name = 'Standard'
    callback = await pick_period(env, 'en')
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("refresh")} Renew subscription?</b>\n'
        'Plan: <b>Standard</b>\n'
        'Traffic: ∞ · Devices: 3\n'
        'Period: 30 days\n'
        'To pay: <b>199 ₽</b>\n'
        'Balance: 1000 ₽ → 801 ₽ left'
    )
    assert not CYRILLIC_RE.search(text.replace('₽', ''))
    assert _labels(markup)[0] == [f'{_tag("check")} Confirm']


@pytest.mark.asyncio
async def test_theme_on_confirm_shows_the_discount_line(theme_on, env):
    _discounted_price(env)
    callback = await pick_period(env)
    text, _ = _sent(callback.message.edit_text)
    assert 'Срок: 30 дней\nСкидка: 10% (−19.90 ₽)\nК оплате: <b>179.10 ₽</b>' in text
    assert '🎁' not in text


@pytest.mark.asyncio
async def test_theme_on_confirm_has_exactly_one_green_button(theme_on, env):
    callback = await pick_period(env)
    _, markup = _sent(callback.message.edit_text)
    themed = _buttons(theme_markup(markup))
    confirm_button, back = themed
    assert confirm_button.style == 'success' and confirm_button.icon_custom_emoji_id
    assert back.style is None and not back.icon_custom_emoji_id
    assert _greens(markup) == 1


@pytest.mark.asyncio
async def test_theme_on_confirm_escapes_the_tariff_name(theme_on, env):
    env['tariff'].name = '<i>Про</i> & Ко'
    callback = await pick_period(env)
    text, _ = _sent(callback.message.edit_text)
    assert 'Тариф: <b>&lt;i&gt;Про&lt;/i&gt; &amp; Ко</b>' in text


# --- Под темой: не хватает средств --------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_insufficient_ru_en(theme_on, env):
    callback = await pick_period(env, balance=5000)
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("warning")} Недостаточно средств</b>\n'
        'Стандартный на 30 дней: <b>199 ₽</b>\n'
        'На балансе: 50 ₽\n'
        'Не хватает: <b>149 ₽</b>\n'
        'Пополните баланс — продлим автоматически.'
    )
    assert not caption_exceeds_telegram_limit(text)
    env['tariff'].name = 'Standard'
    callback = await pick_period(env, 'en', balance=5000)
    text, _ = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("warning")} Not enough funds</b>\n'
        'Standard for 30 days: <b>199 ₽</b>\n'
        'Balance: 50 ₽\n'
        'Missing: <b>149 ₽</b>\n'
        'Top up your balance — we will renew automatically.'
    )
    assert not CYRILLIC_RE.search(text.replace('₽', ''))


@pytest.mark.asyncio
async def test_theme_on_insufficient_has_exactly_one_green_button(theme_on, env):
    callback = await pick_period(env, balance=5000)
    _, markup = _sent(callback.message.edit_text)
    themed = _buttons(theme_markup(markup))
    pay, back = themed
    assert pay.callback_data == 'topup_amount|rollypay|14900' and pay.style == 'success'
    assert back.style is None and not back.icon_custom_emoji_id
    assert _greens(markup) == 1


@pytest.mark.asyncio
async def test_theme_on_insufficient_without_direct_payment_falls_back_to_topup(theme_on, env, monkeypatch):
    monkeypatch.setattr(type(settings), 'is_auto_purchase_after_topup_enabled', lambda self: False)
    callback = await pick_period(env, balance=5000)
    _, markup = _sent(callback.message.edit_text)
    assert _labels(markup) == [[f'{_tag("coin")} Пополнить баланс'], ['⬅️ Назад']]
    assert _greens(markup) == 1
    assert _buttons(theme_markup(markup))[0].icon_custom_emoji_id


@pytest.mark.asyncio
async def test_theme_on_insufficient_still_saves_the_cart(theme_on, env):
    await pick_period(env, balance=5000)
    saved = tp.user_cart_service.save_user_cart.await_args.args[1]
    assert saved['cart_mode'] == 'extend' and saved['missing_amount'] == 14900


# --- Под темой: успех и ошибки ------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_success_ru_en_with_end_date(theme_on, env):
    callback = await confirm(env)
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("party")} Подписка продлена до 25.11.2026</b>\nДобавлено: 30 дней · списано <b>199 ₽</b>'
    )
    assert not caption_exceeds_telegram_limit(text)
    assert [b.callback_data for b in _buttons(markup)] == ['menu_subscription', 'back_to_menu']
    back = _buttons(theme_markup(markup))[-1]
    assert back.style is None and not back.icon_custom_emoji_id
    callback = await confirm(env, 'en')
    text, _ = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("party")} Subscription renewed until 25.11.2026</b>\nAdded: 30 days · charged <b>199 ₽</b>'
    )


@pytest.mark.asyncio
async def test_theme_on_charge_and_renew_errors(theme_on, env):
    env['charge_ok'] = False
    callback = await confirm(env)
    assert _sent(callback.message.edit_text)[0] == (
        f'<b>{_tag("cross")} Не удалось списать оплату</b>\nПопробуйте ещё раз чуть позже.'
    )
    env['charge_ok'] = True
    env['extend_raises'] = True
    callback = await confirm(env)
    assert _sent(callback.message.edit_text)[0] == (
        f'<b>{_tag("cross")} Не удалось продлить подписку</b>\n'
        'Если деньги списались, а срок не изменился — напишите в поддержку.'
    )
    callback = await confirm(env, 'en')
    assert not CYRILLIC_RE.search(_sent(callback.message.edit_text)[0])


@pytest.mark.asyncio
async def test_theme_on_stale_button_alerts_stay_plain(theme_on, env):
    callback = _callback('tariff_ext_confirm:7')
    await tp.confirm_tariff_extend(callback, _user(subscription=env['subscription']), _db(), _state())
    assert _alert(callback) == 'Сессия устарела, откройте продление заново'
    assert '<' not in _alert(callback)


# --- Под темой: возврат к корзине ---------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_cart_restore_confirm_uses_the_cart_device_limit(theme_on, env):
    callback = await restore_cart(env, discount_percent=10)
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("refresh")} Продлить подписку?</b>\n'
        'Тариф: <b>Стандартный</b>\n'
        'Трафик: ∞ · Устройств: 4\n'
        'Срок: 30 дней\n'
        'Скидка: 10% (−22.11 ₽)\n'
        'К оплате: <b>199 ₽</b>\n'
        'Баланс: 1000 ₽ → останется 801 ₽'
    )
    assert _alert(callback) == 'Корзина восстановлена'
    assert [b.callback_data for b in _buttons(markup)] == ['tariff_ext_confirm:7:1:30', 'tariff_extend:7:1']
    assert _greens(markup) == 1


@pytest.mark.asyncio
async def test_theme_on_cart_restore_still_insufficient(theme_on, env):
    callback = await restore_cart(env, balance=5000)
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("warning")} Всё ещё не хватает</b>\n'
        'Стандартный на 30 дней: <b>199 ₽</b>\n'
        'На балансе: 50 ₽\n'
        'Не хватает: <b>149 ₽</b>'
    )
    assert _greens(markup) == 1
    env['tariff'].name = 'Standard'
    callback = await restore_cart(env, 'en', balance=5000)
    text, _ = _sent(callback.message.edit_text)
    assert text.startswith(f'<b>{_tag("warning")} Still not enough</b>\nStandard for 30 days:')
    assert _alert(await restore_cart(env, 'en')) == 'Cart restored'


# --- Под темой: смена тарифа --------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_switch_has_nothing_to_switch_to(theme_on, env):
    for runner in (show_switch, show_legacy_switch):
        text, markup = _sent((await runner(env)).message.edit_text)
        assert text == f'<b>{_tag("gem")} Сменить тариф пока не на что</b>\nДругих доступных тарифов нет.'
        assert [b.callback_data for b in _buttons(markup)] == ['menu_subscription']
        back = _buttons(theme_markup(markup))[0]
        assert back.style is None and not back.icon_custom_emoji_id
    text, _ = _sent((await show_switch(env, 'en')).message.edit_text)
    assert text == f'<b>{_tag("gem")} Nothing to switch to</b>\nThere are no other plans available.'


@pytest.mark.asyncio
async def test_theme_on_switch_expired_offers_one_green_buy_button(theme_on, env):
    env['subscription'].end_date = datetime.now(UTC) - timedelta(days=1)
    for runner in (show_switch, show_legacy_switch):
        text, markup = _sent((await runner(env)).message.edit_text)
        assert text == f'<b>{_tag("hourglass")} Подписка закончилась</b>\nСменить тариф уже нельзя — оформите новый.'
        buy, back = _buttons(theme_markup(markup))
        assert buy.callback_data == 'menu_buy' and buy.style == 'success' and buy.icon_custom_emoji_id
        assert back.style is None and not back.icon_custom_emoji_id
        assert _labels(markup)[0] == [f'{_tag("gem")} Купить тариф']
    text, markup = _sent((await show_switch(env, 'en')).message.edit_text)
    assert text.startswith(f'<b>{_tag("hourglass")} Subscription has ended</b>')
    assert _labels(markup)[0] == [f'{_tag("gem")} Buy a plan']


@pytest.mark.asyncio
async def test_theme_on_switch_disabled(theme_on, env, monkeypatch):
    monkeypatch.setattr(settings, 'TARIFF_SWITCH_UPGRADE_ENABLED', False)
    monkeypatch.setattr(settings, 'TARIFF_SWITCH_DOWNGRADE_ENABLED', False)
    for runner in (show_switch, show_legacy_switch):
        text, _ = _sent((await runner(env)).message.edit_text)
        assert text == f'<b>{_tag("no_entry")} Смена тарифа отключена</b>\nАдминистратор её выключил.'
    text, _ = _sent((await show_switch(env, 'en')).message.edit_text)
    assert text == f'<b>{_tag("no_entry")} Plan switching is off</b>\nThe administrator has disabled it.'


# --- Под темой: ветки E0 / E0b / E2 в purchase.py -----------------------------------------


@pytest.mark.asyncio
async def test_theme_on_extend_without_subscription_or_tariff(theme_on, env):
    env['subscription'] = None
    for subscription in (None, _subscription(is_trial=True, tariff_id=None)):
        env['subscription'] = subscription
        callback = await extend_entry(env)
        text, markup = _sent(callback.message.edit_text)
        assert text == (f'<b>{_tag("gem")} Выберите тариф</b>\nЧтобы пользоваться VPN дальше, оформите подписку.')
        assert _labels(markup) == [[f'{_tag("gem")} Купить подписку'], ['Закрыть']]
        buy, close = _buttons(theme_markup(markup))
        assert [buy.callback_data, close.callback_data] == ['menu_buy', 'webhook:close']
        assert buy.style == 'success' and buy.icon_custom_emoji_id
        assert close.style is None and not close.icon_custom_emoji_id
    env['subscription'] = None
    text, markup = _sent((await extend_entry(env, 'en')).message.edit_text)
    assert text == f'<b>{_tag("gem")} Choose a plan</b>\nTo keep using the VPN, get a subscription.'
    assert _labels(markup)[1] == ['Close']


@pytest.mark.asyncio
async def test_theme_on_extend_legacy_subscription_without_tariff(theme_on, env):
    env['subscription'] = _subscription(tariff_id=None)
    callback = await extend_entry(env)
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("gem")} Выберите тариф для продления</b>\n'
        'Подписка оформлена до появления тарифов. Текущий срок сохранится.'
    )
    choose, back = _buttons(theme_markup(markup))
    assert choose.callback_data == 'tariff_switch' and choose.style == 'success' and choose.icon_custom_emoji_id
    assert back.style is None and not back.icon_custom_emoji_id
    assert _labels(markup)[0] == [f'{_tag("gem")} Выбрать тариф']
    text, _ = _sent((await extend_entry(env, 'en')).message.edit_text)
    assert not CYRILLIC_RE.search(text)


# --- Все экраны под темой: общие правила --------------------------------------------------


async def _every_screen(env, monkeypatch, language: str) -> list[tuple[str, str, object]]:
    screens: list[tuple[str, str, object]] = []

    def take(name, callback, mock='edit_text'):
        text, markup = _sent(getattr(callback.message, mock))
        screens.append((name, text, markup))

    env['tariff'].name = 'Standard'
    take('periods', await show_periods(env, language, _promo_group()))
    take('confirm', await pick_period(env, language))
    take('insufficient', await pick_period(env, language, balance=5000))
    take('success', await confirm(env, language))
    take('restore', await restore_cart(env, language))
    take('restore-short', await restore_cart(env, language, balance=5000))
    take('switch-none', await show_switch(env, language))
    env['subscription'].end_date = datetime.now(UTC) - timedelta(days=1)
    take('switch-expired', await show_switch(env, language))
    env['subscription'].end_date = datetime.now(UTC) + timedelta(days=5)
    monkeypatch.setattr(settings, 'TARIFF_SWITCH_UPGRADE_ENABLED', False)
    monkeypatch.setattr(settings, 'TARIFF_SWITCH_DOWNGRADE_ENABLED', False)
    take('switch-disabled', await show_switch(env, language))
    monkeypatch.setattr(settings, 'TARIFF_SWITCH_UPGRADE_ENABLED', True)
    monkeypatch.setattr(settings, 'TARIFF_SWITCH_DOWNGRADE_ENABLED', True)
    env['subscription'].tariff_id = None
    env['tariffs'] = [_tariff(id=1, name='Standard'), _tariff(id=2, name='Pro')]
    take('legacy-pick', await show_periods(env, language))
    env['subscription'] = None
    take('no-subscription', await extend_entry(env, language))
    env['subscription'] = _subscription(tariff_id=None)
    take('legacy', await extend_entry(env, language))
    return screens


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_theme_on_every_screen_follows_the_visual_language(theme_on, env, monkeypatch, language):
    for name, text, markup in await _every_screen(env, monkeypatch, language):
        assert text.startswith('<b>'), name
        assert text.splitlines()[0].count('<tg-emoji') == 1, name
        assert text.count('<tg-emoji') <= 2, name
        assert not caption_exceeds_telegram_limit(text), name
        for banned in ('<blockquote', '<h1', '▰', '▓', '━'):
            assert banned not in text, (name, banned)
        assert _greens(markup) <= 1, name
        if language == 'en':
            assert not CYRILLIC_RE.search(text.replace('₽', '')), (name, text)
            for button in _buttons(theme_markup(markup)):
                assert not CYRILLIC_RE.search(button.text.replace('₽', '')), (name, button.text)


@pytest.mark.asyncio
async def test_theme_on_decision_screens_have_one_green_each(theme_on, env, monkeypatch):
    greens = {name: _greens(markup) for name, _, markup in await _every_screen(env, monkeypatch, 'ru')}
    assert greens['confirm'] == greens['insufficient'] == greens['restore'] == greens['restore-short'] == 1
    assert greens['switch-expired'] == greens['no-subscription'] == greens['legacy'] == 1


# --- Шаблоны форка форматируются настоящими kwargs вызова --------------------------------

REAL_KWARGS = {
    'TARIFF_RENEW_TITLE': {'discount_hint': '', 'name': 'x', 'traffic': '∞', 'devices': 3},
    'TARIFF_RENEW_CONFIRM': {
        'name': 'x',
        'traffic': '∞',
        'devices': 3,
        'period': '30 days',
        'discount': '',
        'total': '1 ₽',
        'balance': '2 ₽',
        'after': '1 ₽',
    },
    'TARIFF_PURCHASE_INSUFFICIENT': {
        'name': 'x',
        'period': '30 days',
        'price': '1 ₽',
        'balance': '0 ₽',
        'missing': '1 ₽',
    },
    'TARIFF_PURCHASE_STILL_INSUFFICIENT': {
        'name': 'x',
        'period': '30 days',
        'price': '1 ₽',
        'balance': '0 ₽',
        'missing': '1 ₽',
    },
    'TARIFF_PURCHASE_DISCOUNT_SHORT_LINE': {'percent': 10, 'amount': '1 ₽'},
    'FORK_RENEW_SUCCESS': {
        'name': 'x',
        'traffic': '∞',
        'devices': 3,
        'period': '30 days',
        'price': '1 ₽',
        'date': '25.11.2026',
    },
    'FORK_PRICE_DISCOUNTED': {'price': '1 ₽', 'percent': 10},
    'FORK_BUTTON_TARIFF_NAME': {'name': 'x'},
}
PLAIN_KEYS = (
    'TARIFF_RENEW_CART_SAVED_HINT',
    'TARIFF_RENEW_PERIOD_DISCOUNTS_HINT',
    'TARIFF_RENEW_CHOOSE_TARIFF',
    'TARIFF_RENEW_CONFIRM_BUTTON',
    'TARIFF_RENEW_ERROR',
    'TARIFF_PURCHASE_CHARGE_ERROR',
    'TARIFF_PURCHASE_CART_RESTORED',
    'TARIFF_SWITCH_NO_TARIFFS',
    'TARIFF_SWITCH_EXPIRED',
    'TARIFF_SWITCH_DISABLED',
    'BUY_TARIFF_BUTTON',
    'FORK_BUTTON_TOPUP',
    'FORK_BUTTON_CLOSE',
    'FORK_EXTEND_CHOOSE_TARIFF',
    'FORK_EXTEND_LEGACY',
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
    assert value is not None, key
    assert value.count('<tg-emoji') <= 2, key
    for banned in ('<blockquote', '<h1', '<h2', '<h3', '▰', '▓', '━'):
        assert banned not in value, (key, banned)
    if value.startswith('<b>'):
        assert value.split('\n')[0].count('<tg-emoji') <= 1, key
