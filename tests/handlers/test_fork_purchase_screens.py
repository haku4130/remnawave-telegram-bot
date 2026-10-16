"""Покупка подписки — под темой форка.

Без темы каждый экран обязан остаться байт-в-байт как у апстрима: снимки ниже сняты с кода
до переписывания (1f041705), тема выключена. Под темой тексты собирают FORK_*-ключи и
переопределённые ключи апстрима (app/localization/fork/), клавиатуры — те же функции, что у бота.
Тесты гоняют настоящие обработчики с заглушками на всё, что ходит наружу.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
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
SUB_LINK = 'https://example.com/sub'


def _tag(name: str) -> str:
    from app.localization.emoji_catalog import tag

    return tag(name)


def _tariff(**overrides) -> SimpleNamespace:
    data = {
        'id': 1,
        'name': 'Стандартный',
        'description': None,
        'is_active': True,
        'is_daily': False,
        'is_free': False,
        'is_highlighted': False,
        'traffic_limit_gb': 0,
        'device_limit': 3,
        'max_device_limit': 10,
        'device_price_kopeks': 5500,
        'period_prices': {'30': 19900, '90': 49900, '180': 89900},
        'highlight_period_days': None,
        'allowed_squads': ['squad'],
        'can_purchase_custom_days': lambda: False,
        'can_purchase_custom_traffic': lambda: False,
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
        'end_date': NEW_END_DATE,
        'remnawave_id': 'uuid',
        'subscription_url': SUB_LINK,
        'subscription_crypto_link': None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _user(language: str = 'ru', balance: int = 100_000, promo_group=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        language=language,
        balance_kopeks=balance,
        remnawave_id='uuid',
        telegram_id=42,
        promo_group_id=None,
        promo_group=promo_group,
        get_primary_promo_group=lambda: promo_group,
    )


def _callback(data: str = 'x') -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    callback.bot = MagicMock()
    return callback


def _state(data: dict | None = None) -> MagicMock:
    state = MagicMock()
    state.update_data = AsyncMock()
    state.clear = AsyncMock()
    state.get_data = AsyncMock(return_value=data or {})
    return state


def _sent(mock: AsyncMock) -> tuple[str, object]:
    call = mock.await_args
    text = call.args[0] if call.args else call.kwargs['text']
    return text, call.kwargs.get('reply_markup')


def _labels(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def _alert(callback) -> str:
    return callback.answer.await_args.args[0]


def _no_styles(markup) -> bool:
    return all(button.style is None for button in _buttons(markup))


def _greens(markup) -> int:
    return sum(1 for button in _buttons(theme_markup(markup)) if button.style == 'success')


def _plain_len(text: str) -> int:
    return len(TAG_RE.sub('', text))


@pytest.fixture
def prod_like(monkeypatch):
    monkeypatch.setattr(settings, 'SALES_MODE', 'tariffs')
    monkeypatch.setattr(settings, 'MULTI_TARIFF_ENABLED', False, raising=False)
    monkeypatch.setattr(settings, 'CONNECT_BUTTON_MODE', 'miniapp_subscription')
    monkeypatch.setattr(type(settings), 'is_multi_tariff_enabled', lambda self: False)
    monkeypatch.setattr(type(settings), 'is_auto_purchase_after_topup_enabled', lambda self: True)


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
        'existing': None,
        'tariff': _tariff(),
        'tariffs': [_tariff()],
        'price': SimpleNamespace(
            final_total=19900, original_total=19900, promo_group_discount=0, promo_offer_discount=0
        ),
        'charge_ok': True,
        'create_raises': False,
        'created': _subscription(),
    }

    async def tariff_by_id(db, tariff_id):
        return state['tariff']

    async def sub_by_user(db, user_id):
        return state['existing']

    async def tariffs_for_user(db, promo_group_id):
        return state['tariffs']

    async def calculate(tariff, period, device_limit=None, user=None, **kwargs):
        return state['price']

    async def create(**kwargs):
        if state['create_raises']:
            raise RuntimeError('boom')
        return state['created']

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
    monkeypatch.setattr(tp, 'get_subscription_by_user_id', sub_by_user)
    monkeypatch.setattr('app.database.crud.subscription.get_subscription_by_user_id', sub_by_user)
    monkeypatch.setattr(tp, 'get_tariffs_for_user', tariffs_for_user)
    monkeypatch.setattr(tp, 'create_paid_subscription', create)
    monkeypatch.setattr(tp, 'subtract_user_balance', subtract)
    monkeypatch.setattr(tp, 'create_transaction', AsyncMock())
    monkeypatch.setattr(tp, 'SubscriptionService', _SubscriptionService)
    monkeypatch.setattr(tp, 'AdminNotificationService', _Admin)
    monkeypatch.setattr('app.database.crud.user.lock_user_for_pricing', lock_user)
    monkeypatch.setattr('app.services.pricing_engine.pricing_engine.calculate_tariff_purchase_price', calculate)
    monkeypatch.setattr(tp.user_cart_service, 'save_user_cart', AsyncMock())
    monkeypatch.setattr(tp.user_cart_service, 'delete_user_cart', AsyncMock())
    monkeypatch.setattr(tp.user_cart_service, 'get_user_cart', AsyncMock(return_value=None))
    monkeypatch.setattr(purchase.user_cart_service, 'get_user_cart', AsyncMock(return_value=None))
    monkeypatch.setattr(kb, 'get_payment_methods_keyboard', payment_methods)
    monkeypatch.setattr(
        PricingEngine, 'resolve_promo_group', staticmethod(lambda user: getattr(user, 'promo_group', None))
    )
    state['user'] = _user()
    return state


def _db() -> MagicMock:
    db = MagicMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


# --- запуск обработчиков ------------------------------------------------------------------


async def show_list(env, language: str = 'ru', promo_group=None):
    callback = _callback('menu_buy')
    await tp.show_tariffs_list(callback, _user(language, promo_group=promo_group), _db(), _state())
    return callback


async def show_periods(env, language: str = 'ru', promo_group=None):
    """Единственный тариф: список пропускается, сразу информация и выбор срока."""
    env['tariffs'] = [env['tariff']]
    return await show_list(env, language, promo_group)


async def select_tariff(env, language: str = 'ru', promo_group=None):
    callback = _callback('tariff_select:1')
    await tp.select_tariff(callback, _user(language, promo_group=promo_group), _db(), _state())
    return callback


async def pick_period(env, language: str = 'ru', balance: int = 100_000, period: int = 30):
    callback = _callback(f'tariff_period:1:{period}')
    await tp.select_tariff_period(callback, _user(language, balance=balance), _db(), _state())
    return callback


async def confirm(env, language: str = 'ru', period: int = 30):
    callback = _callback(f'tariff_confirm:1:{period}')
    user = _user(language)
    env['user'] = user
    await tp.confirm_tariff_purchase(callback, user, _db(), _state())
    return callback


async def restore_cart(env, language: str = 'ru', balance: int = 100_000, discount_percent: int = 0):
    callback = _callback('return_to_saved_cart')
    cart = {
        'cart_mode': 'tariff_purchase',
        'tariff_id': 1,
        'period_days': 30,
        'total_price': 19900,
        'device_limit': 4,
        'discount_percent': discount_percent,
    }
    await tp.return_to_saved_tariff_cart(callback, _state(), _user(language, balance=balance), _db(), cart)
    return callback


async def switch_period(env, language: str = 'ru', balance: int = 5000):
    callback = _callback('tariff_sw_period:1:30')
    state = _state({'current_tariff_id': 1})
    env['existing'] = _subscription()
    await tp.select_tariff_switch_period(callback, _user(language, balance=balance), _db(), state)
    return callback


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

# Снято с кода до переписывания (1f041705), тема выключена.
UPSTREAM_INFO = (
    '📦 <b>Стандартный</b>\n\n<b>Параметры:</b>\n• Трафик: Безлимит\n• Устройств: 3\n\nВыберите период подписки:'
)
UPSTREAM_INFO_DESCRIPTION = (
    '📦 <b>Стандартный</b>\n\n<b>Параметры:</b>\n• Трафик: Безлимит\n• Устройств: 3\n\n'
    '📝 Для дома &amp; семьи\n\nВыберите период подписки:'
)
UPSTREAM_PERIOD_BUTTONS = [['30 дней — 199 ₽'], ['90 дней — 499 ₽'], ['180 дней — 899 ₽'], ['⬅️ Назад']]
UPSTREAM_LIST = (
    '📦 <b>Выберите тариф</b>\n'
    '🎁 <i>Скидки по периодам</i>\n'
    '\n'
    '<b>Стандартный</b> — ∞ / 3 📱 от 179₽🔥\n'
    '<i>Для дома &amp; семьи</i>\n'
    '\n'
    '<b>Про</b> — 500 ГБ / 5 📱 от 359₽🔥\n'
)
UPSTREAM_CONFIRM = (
    '✅ <b>Подтверждение покупки</b>\n\n'
    '📦 Тариф: <b>Стандартный</b>\n'
    '📊 Трафик: Безлимит\n'
    '📱 Устройств: 3\n'
    '📅 Период: 30 дней\n\n'
    '💰 <b>Итого: 199 ₽</b>\n\n'
    '💳 Ваш баланс: 1000 ₽\n'
    'После оплаты: 801 ₽'
)
UPSTREAM_CONFIRM_DISCOUNT = (
    '✅ <b>Подтверждение покупки</b>\n\n'
    '📦 Тариф: <b>Стандартный</b>\n'
    '📊 Трафик: Безлимит\n'
    '📱 Устройств: 3\n'
    '📅 Период: 30 дней\n\n'
    '🎁 Скидка: 10% (-19.90 ₽)\n'
    '💰 <b>Итого: 179.10 ₽</b>\n\n'
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
    '🛒 <i>Корзина сохранена! После пополнения баланса подписка будет оформлена автоматически.</i>'
)
UPSTREAM_SWITCH_INSUFFICIENT = UPSTREAM_INSUFFICIENT.split('\n\n🛒', maxsplit=1)[0]
UPSTREAM_STILL_INSUFFICIENT = (
    '❌ <b>Все еще недостаточно средств</b>\n\n'
    '📦 Тариф: <b>Стандартный</b>\n'
    '📅 Период: 30 дней\n'
    '💰 Стоимость: 199 ₽\n\n'
    '💳 Ваш баланс: 50 ₽\n'
    '⚠️ Не хватает: <b>149 ₽</b>'
)
UPSTREAM_SUCCESS = (
    '🎉 <b>Подписка успешно оформлена!</b>\n\n'
    '📦 Тариф: <b>Стандартный</b>\n'
    '📊 Трафик: Безлимит\n'
    '📱 Устройств: 3\n'
    '📅 Период: 30 дней\n'
    '💰 Списано: 199 ₽\n\n'
    'Перейдите в раздел «Подписка» для подключения.'
)


@pytest.mark.asyncio
async def test_theme_off_tariffs_list_matches_upstream(theme_off, env):
    env['tariffs'] = [
        _tariff(description='Для дома & семьи'),
        _tariff(id=2, name='Про', traffic_limit_gb=500, device_limit=5, period_prices={'30': 39900}),
    ]
    callback = await show_list(env, promo_group=_promo_group())
    text, markup = _sent(callback.message.edit_text)
    assert text == UPSTREAM_LIST
    assert _labels(markup) == [['Стандартный'], ['Про'], ['⬅️ Назад']]
    assert _no_styles(markup)
    env['tariffs'] = []
    callback = await show_list(env)
    assert _sent(callback.message.edit_text)[0] == (
        '😔 <b>Нет доступных тарифов</b>\n\nК сожалению, сейчас нет тарифов для покупки.'
    )


@pytest.mark.asyncio
async def test_theme_off_periods_screen_matches_upstream(theme_off, env):
    callback = await show_periods(env)
    text, markup = _sent(callback.message.edit_text)
    assert text == UPSTREAM_INFO
    assert _labels(markup) == UPSTREAM_PERIOD_BUTTONS
    assert [b.callback_data for b in _buttons(markup)] == [
        'tariff_period:1:30',
        'tariff_period:1:90',
        'tariff_period:1:180',
        'back_to_menu',
    ]
    assert _no_styles(markup)
    env['tariff'].description = 'Для дома & семьи'
    callback = await select_tariff(env)
    assert _sent(callback.message.edit_text)[0] == UPSTREAM_INFO_DESCRIPTION
    callback = await select_tariff(env, promo_group=_promo_group())
    assert _labels(_sent(callback.message.edit_text)[1])[0] == ['30 дней — 179.10 ₽ 🔥−10%']
    env['tariff'].highlight_period_days = 90
    callback = await select_tariff(env)
    assert _labels(_sent(callback.message.edit_text)[1])[1] == ['⭐ выгодно · 90 дней — 499 ₽']


@pytest.mark.asyncio
async def test_theme_off_confirm_matches_upstream(theme_off, env):
    callback = await pick_period(env)
    text, markup = _sent(callback.message.edit_text)
    assert text == UPSTREAM_CONFIRM
    assert _labels(markup) == [['✅ Подтвердить покупку'], ['⬅️ Назад']]
    assert [b.callback_data for b in _buttons(markup)] == ['tariff_confirm:1:30', 'tariff_select:1']
    assert _no_styles(markup)
    _discounted_price(env)
    callback = await pick_period(env)
    assert _sent(callback.message.edit_text)[0] == UPSTREAM_CONFIRM_DISCOUNT


@pytest.mark.asyncio
async def test_theme_off_insufficient_and_switch_match_upstream(theme_off, env):
    callback = await pick_period(env, balance=5000)
    text, markup = _sent(callback.message.edit_text)
    assert text == UPSTREAM_INSUFFICIENT
    assert _labels(markup) == [['💳 RollyPay'], ['⬅️ Назад']]
    assert _no_styles(markup)
    callback = await switch_period(env)
    assert _sent(callback.message.edit_text)[0] == UPSTREAM_SWITCH_INSUFFICIENT


@pytest.mark.asyncio
async def test_theme_off_success_and_errors_match_upstream(theme_off, env):
    callback = await confirm(env)
    text, markup = _sent(callback.message.edit_text)
    assert text == UPSTREAM_SUCCESS
    assert _labels(markup) == [['📱 Моя подписка'], ['⬅️ Назад']]
    assert [b.callback_data for b in _buttons(markup)] == ['menu_subscription', 'back_to_menu']
    assert _no_styles(markup)
    env['charge_ok'] = False
    callback = await confirm(env)
    assert _sent(callback.message.edit_text)[0] == '❌ Ошибка списания баланса'
    env['charge_ok'] = True
    env['create_raises'] = True
    callback = await confirm(env)
    assert _sent(callback.message.edit_text)[0] == '❌ Произошла ошибка при оформлении подписки'


@pytest.mark.asyncio
async def test_theme_off_cart_restore_matches_upstream(theme_off, env):
    callback = await restore_cart(env, discount_percent=10)
    text, markup = _sent(callback.message.edit_text)
    assert text == UPSTREAM_CONFIRM.replace(
        '📅 Период: 30 дней\n\n', '📅 Период: 30 дней\n\n🎁 Скидка: 10% (-22.11 ₽)\n'
    )
    assert _alert(callback) == '✅ Корзина восстановлена!'
    assert _labels(markup) == [['✅ Подтвердить покупку'], ['⬅️ Назад']]
    callback = await restore_cart(env, balance=5000)
    text, markup = _sent(callback.message.edit_text)
    assert text == UPSTREAM_STILL_INSUFFICIENT
    assert _labels(markup) == [['💳 RollyPay'], ['⬅️ Назад']]


@pytest.mark.asyncio
async def test_theme_off_missing_cart_alert_matches_upstream(theme_off, env):
    callback = _callback('return_to_saved_cart')
    await purchase.return_to_saved_cart(callback, _state(), _user(), _db())
    assert _alert(callback) == '❌ Сохраненная корзина не найдена'
    assert callback.answer.await_args.kwargs == {'show_alert': True}


# --- Под темой: список тарифов ------------------------------------------------------------


def _five_tariffs() -> list[SimpleNamespace]:
    long_description = 'Очень подробное описание тарифа с множеством подробностей и условий. ' * 8
    return [
        _tariff(
            id=index,
            name=f'Тариф номер {index} с длинным названием',
            description=long_description,
            traffic_limit_gb=100 * index,
            device_limit=index + 1,
        )
        for index in range(1, 6)
    ]


@pytest.mark.asyncio
async def test_theme_on_tariffs_list_ru(theme_on, env):
    env['tariffs'] = [
        _tariff(description='Для дома & семьи'),
        _tariff(id=2, name='Про', traffic_limit_gb=500, device_limit=5, period_prices={'30': 39900}),
    ]
    callback = await show_list(env, promo_group=_promo_group())
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("gem")} Выберите тариф</b>\n'
        'Скидка зависит от срока.\n'
        '\n'
        '<b>Стандартный</b> — ∞ · 3 устр. · от 179.10 ₽\n'
        '<i>Для дома &amp; семьи</i>\n'
        '\n'
        '<b>Про</b> — 500 ГБ · 5 устр. · от 359.10 ₽'
    )
    assert [b.callback_data for b in _buttons(markup)] == ['tariff_select:1', 'tariff_select:2', 'back_to_menu']
    assert _greens(markup) == 0
    back = _buttons(theme_markup(markup))[-1]
    assert back.style is None and not back.icon_custom_emoji_id


@pytest.mark.asyncio
async def test_theme_on_tariffs_list_en_has_no_russian_units(theme_on, env):
    env['tariffs'] = [
        _tariff(name='Standard', description='Home'),
        _tariff(id=2, name='Pro', traffic_limit_gb=500, device_limit=5, period_prices={'30': 39900}),
    ]
    callback = await show_list(env, 'en')
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("gem")} Choose a plan</b>\n'
        '\n'
        '<b>Standard</b> — ∞ · 3 devices · from 199 ₽\n'
        '<i>Home</i>\n'
        '\n'
        '<b>Pro</b> — 500 GB · 5 devices · from 399 ₽'
    )
    assert not CYRILLIC_RE.search(text.replace('₽', ''))
    assert not CYRILLIC_RE.search(''.join(b.text for b in _buttons(markup)))


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_theme_on_tariffs_list_with_five_long_descriptions_fits_the_caption(theme_on, env, language):
    env['tariffs'] = _five_tariffs()
    callback = await show_list(env, language, _promo_group())
    text, _ = _sent(callback.message.edit_text)
    assert not caption_exceeds_telegram_limit(text)
    assert text.count('<b>') == 6
    # Описания обрезаны, а не выброшены: при пяти тарифах они ещё помещаются.
    assert text.count('<i>') == 5 and '…' in text


@pytest.mark.asyncio
async def test_theme_on_tariffs_list_drops_descriptions_when_they_still_do_not_fit(theme_on, env):
    env['tariffs'] = [
        _tariff(id=i, name=f'Тариф {i}', description='Очень длинное описание ' * 20) for i in range(1, 13)
    ]
    callback = await show_list(env)
    text, _ = _sent(callback.message.edit_text)
    assert not caption_exceeds_telegram_limit(text)
    assert '<i>' not in text
    assert text.count('<b>') == 13


@pytest.mark.asyncio
async def test_theme_on_tariffs_list_highlighted_tariff_keeps_its_badge(theme_on, env):
    env['tariffs'] = [_tariff(is_highlighted=True), _tariff(id=2, name='Про')]
    callback = await show_list(env)
    _, markup = _sent(callback.message.edit_text)
    labels = _labels(markup)
    assert labels[0] == ['⭐ выгодно · Стандартный']
    assert labels[1] == [f'{_tag("gem")} Про']


@pytest.mark.asyncio
async def test_theme_on_tariffs_list_escapes_names(theme_on, env):
    env['tariffs'] = [_tariff(name='<b>x</b>'), _tariff(id=2, name='Про')]
    callback = await show_list(env)
    text, _ = _sent(callback.message.edit_text)
    assert '&lt;b&gt;x&lt;/b&gt;' in text and '<b><b>x' not in text


@pytest.mark.asyncio
async def test_theme_on_no_tariffs(theme_on, env):
    env['tariffs'] = []
    callback = await show_list(env)
    text, markup = _sent(callback.message.edit_text)
    assert text == f'<b>{_tag("hourglass")} Тарифов пока нет</b>\nСейчас нечего купить — загляните позже.'
    assert _greens(markup) == 0


# --- Под темой: информация о тарифе и выбор срока -----------------------------------------


@pytest.mark.asyncio
async def test_theme_on_periods_screen_ru(theme_on, env):
    callback = await show_periods(env)
    text, markup = _sent(callback.message.edit_text)
    assert text == (f'<b>{_tag("gem")} Стандартный</b>\nТрафик: ∞ · Устройств: 3\n\nВыберите срок подписки.')
    assert _labels(markup) == [['30 дней — 199 ₽'], ['90 дней — 499 ₽'], ['180 дней — 899 ₽'], ['⬅️ Назад']]
    assert _greens(markup) == 0
    back = _buttons(theme_markup(markup))[-1]
    assert back.style is None and not back.icon_custom_emoji_id


@pytest.mark.asyncio
async def test_theme_on_periods_screen_en(theme_on, env):
    env['tariff'].name = 'Standard'
    env['tariff'].traffic_limit_gb = 50
    callback = await show_periods(env, 'en')
    text, markup = _sent(callback.message.edit_text)
    assert text == (f'<b>{_tag("gem")} Standard</b>\nTraffic: 50 GB · Devices: 3\n\nChoose a subscription period.')
    assert _labels(markup)[0] == ['30 days — 199 ₽']
    assert not CYRILLIC_RE.search(text.replace('₽', ''))
    assert not CYRILLIC_RE.search(''.join(label for row in _labels(markup) for label in row).replace('₽', ''))


@pytest.mark.asyncio
async def test_theme_on_periods_screen_description_is_italic_and_escaped(theme_on, env):
    env['tariff'].description = 'Для дома & <семьи>'
    callback = await select_tariff(env)
    text, _ = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("gem")} Стандартный</b>\n'
        'Трафик: ∞ · Устройств: 3\n'
        '<i>Для дома &amp; &lt;семьи&gt;</i>\n'
        '\n'
        'Выберите срок подписки.'
    )


@pytest.mark.asyncio
async def test_theme_on_periods_screen_long_description_fits_the_caption(theme_on, env):
    env['tariff'].description = 'Очень длинное описание & ' * 200
    callback = await select_tariff(env)
    text, _ = _sent(callback.message.edit_text)
    assert not caption_exceeds_telegram_limit(text)
    assert '…' in text and text.rstrip().endswith('Выберите срок подписки.')


@pytest.mark.asyncio
async def test_theme_on_periods_screen_discount_prices_and_badge(theme_on, env):
    env['tariff'].highlight_period_days = 90
    callback = await select_tariff(env, promo_group=_promo_group())
    _, markup = _sent(callback.message.edit_text)
    labels = _labels(markup)
    assert labels[0] == ['30 дней — 179.10 ₽ · −10%']
    assert labels[1] == ['⭐ выгодно · 90 дней — 499 ₽']
    callback = await select_tariff(env, 'en', promo_group=_promo_group())
    assert _labels(_sent(callback.message.edit_text)[1])[0] == ['30 days — 179.10 ₽ · −10%']


@pytest.mark.asyncio
async def test_theme_on_periods_screen_with_traffic_setup_hint(theme_on, env):
    env['tariff'].can_purchase_custom_traffic = lambda: True
    callback = await select_tariff(env)
    text, markup = _sent(callback.message.edit_text)
    assert text.endswith('Выберите срок подписки.\nТрафик настроите после выбора срока.')
    assert _buttons(markup)[0].callback_data == 'tariff_period_traffic:1:30'


# --- Под темой: подтверждение -------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_confirm_ru(theme_on, env):
    callback = await pick_period(env)
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("gem")} Оформить подписку?</b>\n'
        'Тариф: <b>Стандартный</b>\n'
        'Трафик: ∞ · Устройств: 3\n'
        'Срок: 30 дней\n'
        'К оплате: <b>199 ₽</b>\n'
        'Баланс: 1000 ₽ → останется 801 ₽'
    )
    assert not caption_exceeds_telegram_limit(text)
    assert _labels(markup) == [[f'{_tag("check")} Подтвердить'], ['⬅️ Назад']]
    assert _greens(markup) == 1
    themed = _buttons(theme_markup(markup))
    assert themed[0].style == 'success' and themed[0].icon_custom_emoji_id
    assert themed[1].style is None and not themed[1].icon_custom_emoji_id


@pytest.mark.asyncio
async def test_theme_on_confirm_en(theme_on, env):
    env['tariff'].name = 'Standard'
    env['tariff'].traffic_limit_gb = 50
    callback = await pick_period(env, 'en')
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("gem")} Buy the subscription?</b>\n'
        'Plan: <b>Standard</b>\n'
        'Traffic: 50 GB · Devices: 3\n'
        'Period: 30 days\n'
        'To pay: <b>199 ₽</b>\n'
        'Balance: 1000 ₽ → 801 ₽ left'
    )
    assert not CYRILLIC_RE.search(text.replace('₽', ''))
    assert _labels(markup)[0] == [f'{_tag("check")} Confirm']


@pytest.mark.asyncio
async def test_theme_on_confirm_shows_the_discount_line_and_escapes_the_name(theme_on, env):
    _discounted_price(env)
    env['tariff'].name = 'A & B'
    callback = await pick_period(env)
    text, _ = _sent(callback.message.edit_text)
    assert 'Тариф: <b>A &amp; B</b>' in text
    assert 'Срок: 30 дней\nСкидка: 10% (−19.90 ₽)\nК оплате: <b>179.10 ₽</b>' in text


@pytest.mark.asyncio
async def test_theme_on_confirm_counts_the_existing_subscription_devices(theme_on, env):
    env['existing'] = _subscription(device_limit=5)
    callback = await pick_period(env)
    assert 'Устройств: 5' in _sent(callback.message.edit_text)[0]


# --- Под темой: недостаточно средств ------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_insufficient_ru_en_without_the_old_cart_hint(theme_on, env):
    callback = await pick_period(env, balance=5000)
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("warning")} Недостаточно средств</b>\n'
        'Стандартный на 30 дней: <b>199 ₽</b>\n'
        'На балансе: 50 ₽\n'
        'Не хватает: <b>149 ₽</b>\n'
        'Пополните баланс — оформим автоматически.'
    )
    assert '🛒' not in text and 'Корзина сохранена' not in text
    assert not caption_exceeds_telegram_limit(text)
    env['tariff'].name = 'Standard'
    callback = await pick_period(env, 'en', balance=5000)
    text, _ = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("warning")} Not enough funds</b>\n'
        'Standard for 30 days: <b>199 ₽</b>\n'
        'Balance: 50 ₽\n'
        'Missing: <b>149 ₽</b>\n'
        'Top up your balance — we will activate it automatically.'
    )
    assert not CYRILLIC_RE.search(text.replace('₽', ''))


@pytest.mark.asyncio
async def test_theme_on_insufficient_has_one_green_direct_payment_button(theme_on, env):
    callback = await pick_period(env, balance=5000)
    _, markup = _sent(callback.message.edit_text)
    pay, back = _buttons(theme_markup(markup))
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


@pytest.mark.asyncio
async def test_theme_on_insufficient_still_saves_the_cart(theme_on, env):
    await pick_period(env, balance=5000)
    saved = tp.user_cart_service.save_user_cart.await_args.args[1]
    assert saved['cart_mode'] == 'tariff_purchase' and saved['missing_amount'] == 14900


@pytest.mark.asyncio
async def test_theme_on_switch_insufficient_uses_the_period_label(theme_on, env):
    callback = await switch_period(env)
    text, _ = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("warning")} Недостаточно средств</b>\n'
        'Стандартный на 30 дней: <b>199 ₽</b>\n'
        'На балансе: 50 ₽\n'
        'Не хватает: <b>149 ₽</b>'
    )
    env['tariff'].name = 'Standard'
    callback = await switch_period(env, 'en')
    text, _ = _sent(callback.message.edit_text)
    assert 'Standard for 30 days' in text
    assert not CYRILLIC_RE.search(text.replace('₽', ''))


# --- Под темой: успех и ошибки ------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_success_ru_en_with_end_date_and_green_connect(theme_on, env):
    callback = await confirm(env)
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("party")} Подписка оформлена до 25.11.2026</b>\n'
        'Тариф: <b>Стандартный</b>\n'
        'Трафик: ∞ · Устройств: 3\n'
        'Списано <b>199 ₽</b> за 30 дней'
    )
    assert not caption_exceeds_telegram_limit(text)
    connect, back = _buttons(markup)
    assert connect.text == f'{_tag("rocket")} Подключиться'
    assert connect.web_app is not None and connect.web_app.url == SUB_LINK
    assert connect.style == 'success'
    assert back.callback_data == 'back_to_menu'
    assert _greens(markup) == 1
    themed_back = _buttons(theme_markup(markup))[-1]
    assert themed_back.style is None and not themed_back.icon_custom_emoji_id
    env['tariff'].name = 'Standard'
    callback = await confirm(env, 'en')
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("party")} Subscription active until 25.11.2026</b>\n'
        'Plan: <b>Standard</b>\n'
        'Traffic: ∞ · Devices: 3\n'
        'Charged <b>199 ₽</b> for 30 days'
    )
    assert not CYRILLIC_RE.search(text.replace('₽', ''))
    assert _buttons(markup)[0].text == f'{_tag("rocket")} Connect'


@pytest.mark.asyncio
async def test_theme_on_success_without_a_link_offers_my_subscription(theme_on, env):
    env['created'] = _subscription(subscription_url=None)
    callback = await confirm(env)
    _, markup = _sent(callback.message.edit_text)
    mine, back = _buttons(markup)
    assert mine.callback_data == 'menu_subscription' and mine.style == 'success'
    assert mine.text == '📱 Моя подписка'
    assert back.callback_data == 'back_to_menu'
    assert _greens(markup) == 1


@pytest.mark.asyncio
async def test_theme_on_success_connect_follows_the_connect_mode(theme_on, env, monkeypatch):
    monkeypatch.setattr(settings, 'CONNECT_BUTTON_MODE', 'link')
    callback = await confirm(env)
    connect = _buttons(_sent(callback.message.edit_text)[1])[0]
    assert connect.url == SUB_LINK and connect.style == 'success'


@pytest.mark.asyncio
async def test_theme_on_success_escapes_the_tariff_name(theme_on, env):
    env['tariff'].name = 'A & <B>'
    callback = await confirm(env)
    assert 'Тариф: <b>A &amp; &lt;B&gt;</b>' in _sent(callback.message.edit_text)[0]


@pytest.mark.asyncio
async def test_theme_on_charge_and_purchase_errors(theme_on, env):
    env['charge_ok'] = False
    callback = await confirm(env)
    assert _sent(callback.message.edit_text)[0] == (
        f'<b>{_tag("cross")} Не удалось списать оплату</b>\nПопробуйте ещё раз чуть позже.'
    )
    env['charge_ok'] = True
    env['create_raises'] = True
    callback = await confirm(env)
    assert _sent(callback.message.edit_text)[0] == (
        f'<b>{_tag("cross")} Не удалось оформить подписку</b>\n'
        'Если деньги списались, а подписки нет — напишите в поддержку.'
    )
    callback = await confirm(env, 'en')
    text = _sent(callback.message.edit_text)[0]
    assert text == (
        f'<b>{_tag("cross")} Could not set up the subscription</b>\n'
        'If you were charged but have no subscription, contact support.'
    )


# --- Под темой: возврат к корзине ---------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_cart_restore_confirm_uses_the_cart_device_limit(theme_on, env):
    callback = await restore_cart(env, discount_percent=10)
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("gem")} Оформить подписку?</b>\n'
        'Тариф: <b>Стандартный</b>\n'
        'Трафик: ∞ · Устройств: 4\n'
        'Срок: 30 дней\n'
        'Скидка: 10% (−22.11 ₽)\n'
        'К оплате: <b>199 ₽</b>\n'
        'Баланс: 1000 ₽ → останется 801 ₽'
    )
    assert _alert(callback) == 'Корзина восстановлена'
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
    assert not CYRILLIC_RE.search(_sent(callback.message.edit_text)[0].replace('₽', ''))


@pytest.mark.asyncio
async def test_theme_on_missing_cart_alert_is_plain(theme_on, env):
    callback = _callback('return_to_saved_cart')
    await purchase.return_to_saved_cart(callback, _state(), _user(), _db())
    assert _alert(callback) == 'Корзина уже обработана или устарела'
    callback = _callback('return_to_saved_cart')
    await purchase.return_to_saved_cart(callback, _state(), _user('en'), _db())
    assert _alert(callback) == 'The cart was already processed or has expired'


@pytest.mark.asyncio
async def test_theme_on_cart_corrupted_and_unavailable_alerts_are_plain(theme_on, env):
    callback = _callback()
    await tp.return_to_saved_tariff_cart(callback, _state(), _user(), _db(), {'cart_mode': 'tariff_purchase'})
    assert _alert(callback) == 'Данные корзины повреждены'
    env['tariff'] = None
    callback = _callback()
    await tp.return_to_saved_tariff_cart(
        callback, _state(), _user(), _db(), {'cart_mode': 'tariff_purchase', 'tariff_id': 1}
    )
    assert _alert(callback) == 'Тариф больше недоступен'


# --- Все экраны под темой: общие правила --------------------------------------------------


async def _every_screen(env, language: str) -> list[tuple[str, str, object]]:
    screens: list[tuple[str, str, object]] = []

    def take(name, callback):
        text, markup = _sent(callback.message.edit_text)
        screens.append((name, text, markup))

    env['tariff'].name = 'Standard'
    env['tariff'].description = 'Home'
    env['tariffs'] = [env['tariff'], _tariff(id=2, name='Pro', traffic_limit_gb=500)]
    take('list', await show_list(env, language, _promo_group()))
    take('periods', await select_tariff(env, language, _promo_group()))
    take('confirm', await pick_period(env, language))
    take('insufficient', await pick_period(env, language, balance=5000))
    take('success', await confirm(env, language))
    take('restore', await restore_cart(env, language))
    take('restore-short', await restore_cart(env, language, balance=5000))
    take('switch-insufficient', await switch_period(env, language))
    env['tariffs'] = []
    take('no-tariffs', await show_list(env, language))
    return screens


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_theme_on_every_screen_follows_the_visual_language(theme_on, env, language):
    for name, text, markup in await _every_screen(env, language):
        assert text.startswith('<b>'), name
        assert text.splitlines()[0].count('<tg-emoji') == 1, name
        assert text.count('<tg-emoji') <= 2, name
        assert not caption_exceeds_telegram_limit(text), name
        for banned in ('<blockquote', '<h1', '▰', '▓', '━', '🛒', '📦', '📊', '📱', '📅', '💰', '💳'):
            assert banned not in text, (name, banned)
        assert _greens(markup) <= 1, name
        if language == 'en':
            assert not CYRILLIC_RE.search(text.replace('₽', '')), (name, text)
            for button in _buttons(theme_markup(markup)):
                assert not CYRILLIC_RE.search(button.text.replace('₽', '')), (name, button.text)


@pytest.mark.asyncio
async def test_theme_on_decision_screens_have_one_green_each(theme_on, env):
    greens = {name: _greens(markup) for name, _, markup in await _every_screen(env, 'ru')}
    assert greens['confirm'] == greens['insufficient'] == greens['success'] == 1
    assert greens['restore'] == greens['restore-short'] == 1


# --- Шаблоны форка форматируются настоящими kwargs вызова --------------------------------

REAL_KWARGS = {
    'TARIFF_PURCHASE_INFO': {'name': 'x', 'traffic': '∞', 'devices': 3},
    'TARIFF_PURCHASE_YOUR_DISCOUNT': {'percent': 10},
    'TARIFF_PURCHASE_CONFIRM': {
        'name': 'x',
        'traffic': '∞',
        'devices': 3,
        'period': '30 days',
        'discount': '',
        'total': '1 ₽',
        'balance': '2 ₽',
        'after': '1 ₽',
    },
    'FORK_PURCHASE_SUCCESS': {
        'name': 'x',
        'traffic': '∞',
        'devices': 3,
        'period': '30 days',
        'price': '1 ₽',
        'date': '25.11.2026',
    },
    'FORK_TARIFF_LIST_ROW': {'name': 'x', 'traffic': '∞', 'devices': 3, 'price': 'from 1 ₽'},
    'TARIFF_PURCHASE_PRICE_FROM': {'price': '1 ₽', 'icon': ''},
}
PLAIN_KEYS = (
    'TARIFF_PURCHASE_LIST_TITLE',
    'TARIFF_PURCHASE_PERIOD_DISCOUNTS_HINT',
    'TARIFF_PURCHASE_CHOOSE_PERIOD',
    'TARIFF_PURCHASE_TRAFFIC_SETUP_HINT',
    'TARIFF_PURCHASE_CONFIRM_BUTTON',
    'TARIFF_PURCHASE_CART_SAVED_HINT',
    'TARIFF_PURCHASE_NO_TARIFFS',
    'TARIFF_PURCHASE_ERROR',
    'TARIFF_PURCHASE_ALREADY_ACTIVE_SUB',
    'TARIFF_PURCHASE_CART_CORRUPTED',
    'TARIFF_PURCHASE_NO_LONGER_AVAILABLE',
    'FORK_CART_NOT_FOUND',
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


@pytest.mark.parametrize('language', ['ru', 'en'])
def test_success_template_keeps_a_single_date_placeholder(theme_on, language):
    value = get_texts(language).get('FORK_PURCHASE_SUCCESS')
    assert value.count('{date}') == 1
