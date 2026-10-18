"""Баланс, пополнение и оплата RollyPay — под темой форка.

Без темы каждый экран обязан остаться байт-в-байт как у апстрима: строки ниже сняты с кода
до переписывания (569549c5), тема выключена. Под темой тексты собирают FORK_*-ключи и
переопределённые ключи апстрима (app/localization/fork/), клавиатуры — те же функции, что у бота.
Обработчики гоняются настоящие, с заглушками на всё, что ходит наружу.
"""

from __future__ import annotations

import re
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import InlineKeyboardMarkup

from app.config import settings
from app.handlers.balance import main as balance_main, rollypay as rollypay_handler
from app.localization import loader
from app.localization.texts import get_texts
from app.middlewares.premium_theme import theme_markup
from app.services import payment_method_config_service
from app.services.payment import rollypay as rollypay_service
from app.services.payment.common import PaymentCommonMixin
from app.services.payment.rollypay import RollyPayPaymentMixin
from app.utils import payment_utils
from app.utils.message_patch import caption_exceeds_telegram_limit
from tests.fixtures.fork_pattern import assert_screen_pattern


TAG_RE = re.compile(r'<[^>]+>')
CYRILLIC_RE = re.compile('[а-яА-ЯёЁ]')
SUPPORT_URL = 'https://t.me/zanity_support'
PAY_URL = 'https://pay.example.com/checkout/42'


def _user(language: str = 'ru', balance: int = 15000, **extra) -> SimpleNamespace:
    data = {
        'id': 1,
        'language': language,
        'balance_kopeks': balance,
        'telegram_id': 42,
        'email': None,
        'restriction_topup': False,
        'restriction_reason': None,
    }
    data.update(extra)
    return SimpleNamespace(**data)


def _callback(data: str = 'x', *, text: str | None = None, caption: str | None = None) -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.message.text = text
    callback.message.caption = caption
    callback.message.edit_text = AsyncMock()
    callback.message.edit_caption = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    return callback


def _state() -> MagicMock:
    state = MagicMock()
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()
    state.clear = AsyncMock()
    state.get_data = AsyncMock(return_value={'payment_method': 'rollypay'})
    return state


def _sent(mock: AsyncMock) -> tuple[str, InlineKeyboardMarkup | None]:
    call = mock.await_args
    text = call.args[0] if call.args else call.kwargs['text']
    return text, call.kwargs.get('reply_markup')


def _labels(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def _greens(markup) -> int:
    if markup is None:
        return 0
    return sum(1 for button in _buttons(theme_markup(markup)) if button.style == 'success')


def _plain(text: str) -> str:
    return TAG_RE.sub('', text)


def _without_emoji(text: str) -> str:
    """Текст без кастомных эмодзи вместе с их запасными символами: остаётся то, что набрано обычными знаками."""
    return re.sub(r'<tg-emoji[^>]*>.*?</tg-emoji>', '', text)


def _tag(name: str) -> str:
    from app.localization.emoji_catalog import tag

    return tag(name)


@pytest.fixture
def prod_like(monkeypatch):
    monkeypatch.setattr(settings, 'TELEGRAM_STARS_ENABLED', False)
    monkeypatch.setattr(settings, 'ROLLYPAY_DISPLAY_NAME', 'RollyPay')
    monkeypatch.setattr(settings, 'ROLLYPAY_MIN_AMOUNT_KOPEKS', 10000)
    monkeypatch.setattr(settings, 'ROLLYPAY_MAX_AMOUNT_KOPEKS', 10000000)
    monkeypatch.setattr(settings, 'PRICE_ROUNDING_ENABLED', False)
    monkeypatch.setattr(type(settings), 'is_rollypay_enabled', lambda self: True)
    monkeypatch.setattr(type(settings), 'is_support_topup_enabled', lambda self: True)
    monkeypatch.setattr(type(settings), 'get_support_contact_url', lambda self: SUPPORT_URL)
    monkeypatch.setattr(type(settings), 'get_support_contact_display_html', lambda self: '@zanity_support')
    monkeypatch.setattr(type(settings), 'is_notifications_enabled', lambda self: True)


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


def _transaction(kind: str, kopeks: int, description: str, day: int = 16) -> SimpleNamespace:
    return SimpleNamespace(
        type=kind,
        amount_kopeks=kopeks,
        description=description,
        created_at=datetime(2026, 10, day, 14, 30 + day % 10),
    )


@pytest.fixture
def history(monkeypatch):
    """Операции пользователя: настоящая сортировка и страницы работают на этих строках."""
    state = {'rows': []}

    async def get_user_transactions(db, user_id, limit=10, offset=0):
        return state['rows'][offset : offset + limit]

    monkeypatch.setattr(balance_main, 'get_user_transactions', get_user_transactions)
    return state


@pytest.fixture
def quick_amounts(monkeypatch):
    async def amounts(*args, **kwargs):
        return [10000, 30000, 50000, 100000]

    monkeypatch.setattr('app.keyboards.topup_amounts._load_quick_amounts', amounts)


@pytest.fixture
def rollypay_api(monkeypatch):
    service = MagicMock()
    service.create_rollypay_payment = AsyncMock(return_value={'payment_url': PAY_URL})
    monkeypatch.setattr(rollypay_handler, 'PaymentService', lambda: service)
    return service


# --- запуск обработчиков ------------------------------------------------------------------


async def show_menu(language: str = 'ru', balance: int = 15000):
    callback = _callback('menu_balance', caption='x')
    await balance_main.show_balance_menu(callback, _user(language, balance), MagicMock())
    return callback


async def show_history(rows, language: str = 'ru', page: int = 1):
    callback = _callback('balance_history')
    await balance_main.show_balance_history(callback, _user(language), MagicMock(), page)
    return callback


async def show_methods(language: str = 'ru', **user_extra):
    callback = _callback('balance_topup')
    await balance_main.show_payment_methods(callback, _user(language, **user_extra), MagicMock(), _state())
    return callback


async def ask_amount(language: str = 'ru', **user_extra):
    callback = _callback('topup_rollypay')
    await rollypay_handler.start_rollypay_topup(callback, _user(language, **user_extra), MagicMock(), _state())
    return callback


async def create_payment(kopeks: int, language: str = 'ru'):
    message = MagicMock()
    message.answer = AsyncMock()
    await rollypay_handler._create_rollypay_payment_and_respond(message, _user(language), MagicMock(), kopeks)
    return message


async def support_topup(language: str = 'ru', **user_extra):
    callback = _callback('topup_support')
    await balance_main.request_support_topup(callback, _user(language, **user_extra))
    return callback


async def type_amount(text: str, language: str = 'ru'):
    message = MagicMock()
    message.text = text
    message.successful_payment = None
    message.answer = AsyncMock()
    await balance_main.process_topup_amount(message, _user(language), _state())
    return message


# --- Без темы: как у апстрима -------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_off_balance_menu_matches_upstream(theme_off):
    callback = await show_menu()
    text, markup = _sent(callback.message.edit_caption)
    assert text == '\n💰 <b>Баланс: 150 ₽</b>\n\nВыберите действие:\n'
    assert _labels(markup) == [['📊 История операций', '💳 Пополнить'], ['⬅️ Назад']]
    assert all(button.style is None for button in _buttons(markup))


@pytest.mark.asyncio
async def test_theme_off_history_matches_upstream(theme_off, history):
    history['rows'] = [
        _transaction('deposit', 15000, 'Пополнение через RollyPay'),
        _transaction('subscription_payment', -19900, 'Покупка тарифа <Стандартный> на 30 дней', day=15),
    ]
    callback = await show_history(history['rows'])
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        '📊 <b>История операций</b>\n\n'
        '💰 +150 ₽\n📝 Пополнение через RollyPay\n📅 16.10.2026 14:36\n\n'
        '💸 -199 ₽\n📝 Покупка тарифа &lt;Стандартный&gt; на 30 дней\n📅 15.10.2026 14:35\n\n'
    )
    assert _labels(markup) == [['⬅️ Назад']]


@pytest.mark.asyncio
async def test_theme_off_empty_history_matches_upstream(theme_off, history):
    callback = await show_history([])
    text, markup = _sent(callback.message.edit_text)
    assert text == '📊 История операций пуста'
    assert _labels(markup) == [['⬅️ Назад']]


@pytest.mark.asyncio
async def test_theme_off_payment_methods_match_upstream(theme_off):
    callback = await show_methods()
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        '💳 <b>Способы пополнения баланса</b>\n\n'
        'Выберите удобный для вас способ оплаты:\n\n'
        '💳 <b>RollyPay</b> - через RollyPay\n'
        '🛠️ <b>Через поддержку</b> - другие способы\n\n'
        'Выберите способ пополнения:'
    )
    assert _labels(markup) == [['💳 RollyPay'], ['🛠️ Через поддержку'], ['⬅️ Назад']]
    assert all(button.style is None for button in _buttons(markup))


@pytest.mark.asyncio
async def test_theme_off_topup_restriction_matches_upstream(theme_off):
    callback = await show_methods(restriction_topup=True, restriction_reason='Спам <b>')
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        '🚫 <b>Пополнение ограничено</b>\n\nСпам &lt;b&gt;\n\n'
        'Если вы считаете это ошибкой, вы можете обжаловать решение.'
    )
    assert _labels(markup) == [['🆘 Обжаловать'], ['⬅️ Назад']]
    assert markup.inline_keyboard[0][0].url == SUPPORT_URL

    callback = await show_methods(restriction_topup=True)
    assert 'Действие ограничено администратором' in _sent(callback.message.edit_text)[0]


@pytest.mark.asyncio
async def test_theme_off_rollypay_restriction_matches_upstream(theme_off):
    callback = await ask_amount(restriction_topup=True, restriction_reason='Спам')
    text, markup = _sent(callback.message.edit_text)
    assert text == '🚫 <b>Пополнение ограничено</b>\n\nСпам'
    assert _labels(markup) == [['🆘 Обжаловать'], ['⬅️ Назад']]


@pytest.mark.asyncio
async def test_theme_off_rollypay_amount_prompt_matches_upstream(theme_off, quick_amounts):
    callback = await ask_amount()
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        '💳 <b>Пополнение через RollyPay</b>\n\nВведите сумму пополнения в рублях.\n\nМинимум: 100₽\nМаксимум: 100 000₽'
    )
    assert _labels(markup) == [['100 ₽', '300 ₽'], ['500 ₽', '1000 ₽'], ['⬅️ Назад']]
    assert all(button.style is None for button in _buttons(markup))


@pytest.mark.asyncio
async def test_theme_off_rollypay_payment_link_matches_upstream(theme_off, rollypay_api):
    message = await create_payment(50000)
    text, markup = _sent(message.answer)
    assert text == (
        '💳 <b>Оплата через RollyPay</b>\n\n'
        'Сумма: <b>500.00₽</b>\n\n'
        'Нажмите кнопку ниже для оплаты.\n'
        'После успешной оплаты баланс будет пополнен автоматически.'
    )
    assert _labels(markup) == [['💳 Оплатить 500₽'], ['◀️ Назад']]
    assert markup.inline_keyboard[0][0].url == PAY_URL
    assert markup.inline_keyboard[0][0].style is None


@pytest.mark.asyncio
async def test_theme_off_rollypay_errors_and_limits_match_upstream(theme_off, rollypay_api):
    rollypay_api.create_rollypay_payment.return_value = None
    message = await create_payment(50000)
    assert message.answer.await_args.args[0] == '❌ Не удалось создать платёж. Попробуйте позже.'

    for kopeks, expected in (
        (5000, 'Минимальная сумма пополнения: 100₽'),
        (20000000, 'Максимальная сумма пополнения: 100000₽'),
    ):
        message = MagicMock()
        message.answer = AsyncMock()
        await rollypay_handler.process_rollypay_payment_amount(message, _user(), MagicMock(), kopeks, _state())
        assert message.answer.await_args.args[0] == expected


@pytest.mark.asyncio
async def test_theme_off_support_topup_matches_upstream(theme_off):
    callback = await support_topup()
    text, markup = _sent(callback.message.edit_text)
    assert (
        text
        == """
🛠️ <b>Пополнение через поддержку</b>

Для пополнения баланса обратитесь в техподдержку:
@zanity_support

Укажите:
• ID: 42
• Сумму пополнения
• Способ оплаты

⏰ Время обработки: 1-24 часа

<b>Доступные способы:</b>
• Криптовалюта
• Переводы между банками
• Другие платежные системы
"""
    )
    assert _labels(markup) == [['💬 Написать в поддержку'], ['⬅️ Назад']]
    assert markup.inline_keyboard[0][0].style is None


@pytest.mark.asyncio
async def test_theme_off_typed_amount_limits_match_upstream(theme_off):
    assert (await type_amount('0.5')).answer.await_args.args[0] == 'Минимальная сумма пополнения: 1 ₽'
    assert (await type_amount('60000')).answer.await_args.args[0] == 'Максимальная сумма пополнения: 50,000 ₽'
    assert (await type_amount('abc')).answer.await_args.args[0] == '❌ Неверная сумма'


def test_theme_off_success_message_matches_upstream(theme_off):
    text = rollypay_service._topup_success_text(_user(), 50000, 'RollyPay <1>', 77)
    assert text == (
        '✅ <b>Пополнение успешно!</b>\n\n'
        '💰 Сумма: 500 ₽\n'
        '💳 Способ: RollyPay <1>\n'
        '🆔 Транзакция: 77\n\n'
        'Баланс пополнен автоматически!'
    )


# --- Под темой: экраны --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_balance_menu_ru(theme_on):
    callback = await show_menu()
    text, markup = _sent(callback.message.edit_caption)
    assert text == f'<b>{_tag("coin")} Баланс</b>\n\n{_tag("money_bag")} На счёте: <b>150 ₽</b>'
    assert _labels(markup)[1] == ['⬅️ Назад']
    styled = theme_markup(markup)
    topup = next(button for button in _buttons(styled) if button.callback_data == 'balance_topup')
    assert topup.style == 'success'
    assert topup.text == 'Пополнить'
    assert topup.icon_custom_emoji_id
    history_button = next(button for button in _buttons(styled) if button.callback_data == 'balance_history')
    assert history_button.text == 'История'
    assert history_button.style is None
    assert _greens(markup) == 1


@pytest.mark.asyncio
async def test_theme_on_balance_menu_en(theme_on):
    text, markup = _sent((await show_menu('en')).message.edit_caption)
    assert _plain(text) == '🪙 Balance\n\n💰 Available: 150 ₽'
    assert [button.text for button in _buttons(theme_markup(markup))][:2] == ['History', 'Top up']


@pytest.mark.asyncio
async def test_theme_on_history_ru(theme_on, history):
    history['rows'] = [
        _transaction('deposit', 15000, 'Пополнение через RollyPay'),
        _transaction('subscription_payment', -19900, 'Покупка тарифа <Стандартный> на 30 дней', day=15),
        _transaction('referral_reward', 2500, '', day=14),
    ]
    callback = await show_history(history['rows'])
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("chart")} История операций</b>\n\n'
        '💰 <b>+150 ₽</b> · Пополнение через RollyPay · 16.10.2026\n'
        '💸 <b>−199 ₽</b> · Покупка тарифа &lt;Стандартный&gt; на 30 дней · 15.10.2026\n'
        '💰 <b>+25 ₽</b> · 14.10.2026'
    )
    assert_screen_pattern('history', text, list_screen=True)
    assert _labels(markup) == [['⬅️ Назад']]
    assert _greens(markup) == 0


@pytest.mark.asyncio
async def test_theme_on_history_en_uses_the_english_title(theme_on, history):
    history['rows'] = [_transaction('deposit', 15000, 'Top-up')]
    text = _sent((await show_history(history['rows'], 'en')).message.edit_text)[0]
    assert _plain(text).splitlines()[0] == '📊 Transaction history'
    assert not CYRILLIC_RE.search(text.replace('₽', ''))


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_theme_on_history_of_ten_long_descriptions_fits_the_caption(theme_on, history, language):
    long = 'Покупка тарифа «Очень длинное название тарифа» на 180 дней с применением всех скидок и промокодов'
    history['rows'] = [
        _transaction('subscription_payment' if day % 2 else 'deposit', -199900 if day % 2 else 1234500, long, day=day)
        for day in range(1, 11)
    ]
    text = _sent((await show_history(history['rows'], language)).message.edit_text)[0]
    assert text.count('\n') == 11
    assert not caption_exceeds_telegram_limit(text)
    assert '…' in text


@pytest.mark.asyncio
async def test_theme_on_history_paginates(theme_on, history):
    history['rows'] = [_transaction('deposit', 1000 + n, f'Пополнение {n}', day=1 + n % 28) for n in range(25)]
    callback = await show_history(history['rows'], page=1)
    _, markup = _sent(callback.message.edit_text)
    callbacks = [button.callback_data for button in _buttons(markup)]
    assert 'balance_history_page_2' in callbacks
    assert callbacks[-1] == 'menu_balance'


@pytest.mark.asyncio
async def test_theme_on_empty_history(theme_on, history):
    text, markup = _sent((await show_history([])).message.edit_text)
    assert text == f'<b>{_tag("chart")} Операций пока нет</b>\n\nЗдесь появятся пополнения и списания.'
    assert _labels(markup) == [['⬅️ Назад']]


@pytest.mark.asyncio
async def test_theme_on_payment_methods_ru(theme_on):
    callback = await show_methods()
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("coin")} Пополнение баланса</b>\n\nВыберите способ оплаты — баланс пополнится сразу после платежа.'
    )
    assert 'Выберите способ пополнения' not in text
    styled = theme_markup(markup)
    assert [button.text for button in _buttons(styled)] == ['RollyPay', 'Через поддержку', '⬅️ Назад']
    assert [button.style for button in _buttons(styled)] == ['success', None, None]
    assert [bool(button.icon_custom_emoji_id) for button in _buttons(styled)] == [True, True, False]


@pytest.mark.asyncio
async def test_theme_on_cabinet_name_replaces_the_label_but_keeps_the_icon(theme_on, monkeypatch):
    # Имя способа из кабинета (PaymentMethodConfig.display_name) — одно на бот и кабинет.
    monkeypatch.setattr(
        payment_method_config_service, '_display_name_overrides', {'rollypay': ' СБП, карта, криптовалюта '}
    )
    markup = _sent((await show_methods()).message.edit_text)[1]
    pay = _buttons(theme_markup(markup))[0]
    assert (pay.text, pay.style) == ('СБП, карта, криптовалюта', 'success')
    assert pay.icon_custom_emoji_id


@pytest.mark.asyncio
async def test_theme_off_cabinet_name_replaces_the_whole_label(theme_off, monkeypatch):
    monkeypatch.setattr(payment_method_config_service, '_display_name_overrides', {'rollypay': 'СБП, карта'})
    markup = _sent((await show_methods()).message.edit_text)[1]
    assert markup.inline_keyboard[0][0].text == 'СБП, карта'


@pytest.mark.asyncio
async def test_theme_on_payment_methods_with_a_saved_cart_keep_one_green(theme_on, monkeypatch):
    from app.services.user_cart_service import user_cart_service

    monkeypatch.setattr(
        user_cart_service, 'get_user_cart', AsyncMock(return_value={'saved_cart': True, 'missing_amount': 14900})
    )
    _, markup = _sent((await show_methods()).message.edit_text)
    assert next(iter(_buttons(markup))).callback_data == 'topup_amount|rollypay|14900'
    assert _greens(markup) == 1


@pytest.mark.asyncio
async def test_theme_on_payment_methods_en(theme_on):
    text, markup = _sent((await show_methods('en')).message.edit_text)
    assert not CYRILLIC_RE.search(text)
    assert [button.text for button in _buttons(theme_markup(markup))] == ['RollyPay', 'Via support', '⬅️ Back']


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_theme_on_topup_restriction(theme_on, language):
    callback = await show_methods(language, restriction_topup=True, restriction_reason='Спам <b>')
    text, markup = _sent(callback.message.edit_text)
    assert text.startswith(f'<b>{_tag("no_entry")} ')
    assert 'Спам &lt;b&gt;' in text
    assert '🚫' not in _without_emoji(text)
    assert _greens(markup) == 0
    assert markup.inline_keyboard[0][0].url == SUPPORT_URL
    assert theme_markup(markup).inline_keyboard[0][0].text in ('Обжаловать', 'Appeal')
    assert theme_markup(markup).inline_keyboard[1][0].icon_custom_emoji_id is None

    default = await show_methods(language, restriction_topup=True)
    assert (
        'администратором' in _sent(default.message.edit_text)[0]
        or 'administrator' in _sent(default.message.edit_text)[0]
    )


@pytest.mark.asyncio
async def test_theme_on_rollypay_restriction_uses_the_same_text(theme_on):
    text, markup = _sent((await ask_amount(restriction_topup=True, restriction_reason='Спам')).message.edit_text)
    assert text.startswith(
        f'<b>{_tag("no_entry")} Пополнение недоступно</b>\n\n{_tag("info")} Причина: <b>Спам</b>\n\n'
    )
    assert _greens(markup) == 0


@pytest.mark.asyncio
async def test_theme_on_restriction_reason_stays_one_bold_line(theme_on):
    reason = 'Спам\n\nповторные жалобы <b>'
    text, _ = _sent((await show_methods(restriction_topup=True, restriction_reason=reason)).message.edit_text)
    assert f'{_tag("info")} Причина: <b>Спам повторные жалобы &lt;b&gt;</b>\n\n' in text
    assert_screen_pattern('restricted-multiline', text)


@pytest.mark.asyncio
async def test_theme_on_amount_prompt_ru(theme_on, quick_amounts):
    callback = await ask_amount()
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("coin")} Сумма пополнения</b>\n'
        '\n'
        f'{_tag("dollar")} Минимум: <b>100 ₽</b>\n'
        f'{_tag("money_bag")} Максимум: <b>100 000 ₽</b>\n'
        '\n'
        'Напишите сумму в рублях или выберите ниже.'
    )
    assert [button.text for row in markup.inline_keyboard[:2] for button in row] == [
        '100 ₽',
        '300 ₽',
        '500 ₽',
        '1000 ₽',
    ]
    assert _greens(markup) == 0


@pytest.mark.asyncio
async def test_theme_on_amount_and_payment_screens_do_not_name_the_provider(
    theme_on, quick_amounts, rollypay_api, monkeypatch
):
    # Пользователи не знают, что такое RollyPay: под темой экраны суммы и счёта его не называют.
    monkeypatch.setattr(settings, 'ROLLYPAY_DISPLAY_NAME', 'RollyPay')
    amount_text = _sent((await ask_amount()).message.edit_text)[0]
    link_text = _sent((await create_payment(50000)).answer)[0]
    assert 'RollyPay' not in amount_text
    assert 'RollyPay' not in link_text


@pytest.mark.asyncio
async def test_theme_on_amount_prompt_en(theme_on, quick_amounts):
    text, markup = _sent((await ask_amount('en')).message.edit_text)
    assert not CYRILLIC_RE.search(text.replace('₽', ''))
    assert not any(CYRILLIC_RE.search(b.text.replace('₽', '')) for b in _buttons(theme_markup(markup)))


@pytest.mark.asyncio
async def test_theme_on_payment_link_ru(theme_on, rollypay_api):
    message = await create_payment(50000)
    text, markup = _sent(message.answer)
    assert text == (
        f'<b>{_tag("coin")} Счёт на оплату</b>\n'
        '\n'
        f'{_tag("money_bag")} Сумма: <b>500 ₽</b>\n'
        '\n'
        'Нажмите кнопку ниже — баланс пополнится сразу после оплаты.'
    )
    assert '.00' not in text
    styled = theme_markup(markup)
    pay, back = _buttons(styled)
    assert (pay.text, pay.style, pay.url) == ('Оплатить 500 ₽', 'success', PAY_URL)
    assert pay.icon_custom_emoji_id
    assert back.style is None and back.icon_custom_emoji_id is None
    assert _greens(markup) == 1


@pytest.mark.asyncio
async def test_theme_on_payment_link_keeps_kopeks_when_they_exist(theme_on, rollypay_api):
    message = await create_payment(50050)
    text, markup = _sent(message.answer)
    assert 'Сумма: <b>500.50 ₽</b>' in text
    assert _buttons(theme_markup(markup))[0].text == 'Оплатить 500.50 ₽'


@pytest.mark.asyncio
async def test_theme_on_payment_link_en(theme_on, rollypay_api):
    text, markup = _sent((await create_payment(30000, 'en')).answer)
    assert _plain(text).splitlines()[0] == '🪙 Payment'
    assert '💰 Amount: 300 ₽' in _plain(text)
    assert not CYRILLIC_RE.search(text)
    assert _buttons(theme_markup(markup))[0].text == 'Pay 300 ₽'


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_theme_on_payment_error_and_limits(theme_on, rollypay_api, language):
    rollypay_api.create_rollypay_payment.return_value = None
    message = await create_payment(50000, language)
    text = message.answer.await_args.args[0]
    assert text.startswith(f'<b>{_tag("cross")} ')

    for kopeks in (5000, 20000000):
        limit_message = MagicMock()
        limit_message.answer = AsyncMock()
        await rollypay_handler.process_rollypay_payment_amount(
            limit_message, _user(language), MagicMock(), kopeks, _state()
        )
        limit_text = limit_message.answer.await_args.args[0]
        assert limit_text.startswith(f'<b>{_tag("warning")} ')
        assert ('100 ₽' if kopeks == 5000 else '100 000 ₽') in limit_text


@pytest.mark.asyncio
async def test_theme_on_typed_amount_limits_reuse_the_short_templates(theme_on):
    low = (await type_amount('0.5')).answer.await_args.args[0]
    high = (await type_amount('60000')).answer.await_args.args[0]
    assert low == f'<b>{_tag("warning")} Сумма слишком мала</b>\n\n{_tag("dollar")} Минимум: <b>1 ₽</b>'
    assert high == f'<b>{_tag("warning")} Сумма слишком велика</b>\n\n{_tag("money_bag")} Максимум: <b>50 000 ₽</b>'
    invalid = (await type_amount('abc')).answer.await_args.args[0]
    assert invalid == f'<b>{_tag("cross")} Неверная сумма</b>\n\nНапишите число, например 300.'


@pytest.mark.asyncio
async def test_theme_on_support_topup_ru(theme_on):
    callback = await support_topup(email='a<b>@x.ru', telegram_id=None)
    text, markup = _sent(callback.message.edit_text)
    assert text == (
        f'<b>{_tag("coin")} Пополнение через поддержку</b>\n'
        '\n'
        f'{_tag("speech")} Напишите: <b>@zanity_support</b>\n'
        f'{_tag("user")} Ваш ID: <code>a&lt;b&gt;@x.ru</code>\n'
        f'{_tag("hourglass")} Обработаем за 1–24 часа\n'
        '\n'
        'Укажите ID, сумму и удобный способ — например, криптовалюту или перевод между банками.'
    )
    assert '@zanity_support' in text
    assert '<code>a&lt;b&gt;@x.ru</code>' in text
    assert '•' not in text and '⏰' not in text
    styled = theme_markup(markup)
    assert [button.text for button in _buttons(styled)] == ['Написать в поддержку', '⬅️ Назад']
    assert _buttons(styled)[0].style == 'success'
    assert _buttons(styled)[0].url == SUPPORT_URL
    assert _greens(markup) == 1


@pytest.mark.asyncio
async def test_theme_on_support_topup_en(theme_on):
    text, markup = _sent((await support_topup('en')).message.edit_text)
    assert not CYRILLIC_RE.search(text)
    assert _buttons(theme_markup(markup))[0].text == 'Write to support'


# --- Сообщение об успешной оплате --------------------------------------------------------


def test_theme_on_success_message_ru_en_without_transaction_id(theme_on):
    ru = rollypay_service._topup_success_text(_user('ru'), 50000, 'RollyPay', 77)
    assert ru == (
        f'<b>{_tag("check")} Баланс пополнен</b>\n'
        '\n'
        f'{_tag("money_bag")} Зачислено: <b>500 ₽</b>\n'
        f'{_tag("coin")} На счёте: <b>150 ₽</b>'
    )
    en = rollypay_service._topup_success_text(_user('en'), 50000, 'RollyPay', 77)
    assert en == (
        f'<b>{_tag("check")} Balance topped up</b>\n'
        '\n'
        f'{_tag("money_bag")} Credited: <b>500 ₽</b>\n'
        f'{_tag("coin")} Available: <b>150 ₽</b>'
    )
    for text in (ru, en):
        assert 'Транзакция' not in text and 'Transaction' not in text and '77' not in text
        assert 'RollyPay' not in text, 'способ у всех один — в сообщении его нет'
        assert_screen_pattern('success', text)


def test_theme_on_success_message_shows_the_balance_after_the_top_up(theme_on):
    user = _user(balance=65000)
    assert 'На счёте: <b>650 ₽</b>' in rollypay_service._topup_success_text(user, 50000, 'RollyPay', 77)


def test_theme_on_success_message_falls_back_to_russian_for_a_missing_language(theme_on):
    text = rollypay_service._topup_success_text(SimpleNamespace(language=None), 50000, 'RollyPay', 1)
    assert 'Баланс пополнен' in text


# --- Кнопки под сообщением об оплате и «Вернуться к оформлению» ---------------------------


class _Service(PaymentCommonMixin):
    def __init__(self, bot):
        self.bot = bot


@pytest.fixture
def cart(monkeypatch):
    """Корзина пользователя в Redis: меняется на лету, как при автопокупке."""
    from app.services import user_cart_service as cart_module

    state = {'cart': {'cart_mode': 'tariff_purchase', 'total_price': 19900}}

    async def get_user_cart(user_id):
        return state['cart']

    monkeypatch.setattr(cart_module.user_cart_service, 'get_user_cart', get_user_cart)
    monkeypatch.setattr('app.services.payment.common.user_cart_service.get_user_cart', get_user_cart)
    monkeypatch.setattr('app.services.payment.common.has_subscription_checkout_draft', AsyncMock(return_value=False))
    return state


def _topup_user(language: str = 'ru', with_subscription: bool = False) -> SimpleNamespace:
    subscriptions = (
        [SimpleNamespace(is_active=True, is_trial=False, actual_status='active')] if with_subscription else []
    )
    return SimpleNamespace(id=1, language=language, subscriptions=subscriptions)


def _callbacks(markup) -> list[str]:
    return [button.callback_data for button in _buttons(markup)]


@pytest.mark.asyncio
async def test_theme_off_success_keyboard_matches_upstream(theme_off, cart):
    markup = await _Service(MagicMock()).build_topup_success_keyboard(_topup_user())
    assert _labels(markup) == [
        ['💎 Купить подписку'],
        ['⬅️ Вернуться к оформлению подписки'],
        ['💰 Мой баланс'],
        ['🏠 Главное меню'],
    ]
    assert _callbacks(markup) == ['menu_buy', 'return_to_saved_cart', 'menu_balance', 'back_to_menu']
    assert all(button.style is None for button in _buttons(markup))


@pytest.mark.asyncio
async def test_theme_off_refresh_does_nothing(theme_off, cart):
    bot = MagicMock()
    bot.edit_message_reply_markup = AsyncMock()
    service = _Service(bot)
    user = _topup_user()
    sent = await service.build_topup_success_keyboard(user)
    cart['cart'] = None
    await service.refresh_topup_success_keyboard(user, SimpleNamespace(chat=SimpleNamespace(id=1), message_id=5), sent)
    bot.edit_message_reply_markup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_theme_on_success_keyboard_with_a_cart_has_one_green_return_button(theme_on, cart, language):
    markup = await _Service(MagicMock()).build_topup_success_keyboard(_topup_user(language))
    assert _callbacks(markup) == ['return_to_saved_cart', 'menu_balance', 'back_to_menu']
    assert _greens(markup) == 1
    styled = theme_markup(markup)
    assert _buttons(styled)[0].style == 'success'
    assert _buttons(styled)[0].icon_custom_emoji_id  # корзина из каталога
    assert _buttons(styled)[-1].style is None and _buttons(styled)[-1].icon_custom_emoji_id is None
    if language == 'en':
        assert not any(CYRILLIC_RE.search(b.text) for b in _buttons(styled))


@pytest.mark.asyncio
async def test_theme_on_success_keyboard_without_a_cart_offers_buy_or_extend(theme_on, cart):
    cart['cart'] = None
    buy = await _Service(MagicMock()).build_topup_success_keyboard(_topup_user())
    assert _callbacks(buy) == ['menu_buy', 'menu_balance', 'back_to_menu']
    assert _buttons(buy)[0].style == 'success'
    assert _greens(buy) == 1
    assert [b.text for b in _buttons(theme_markup(buy))][:2] == ['Купить подписку', 'Баланс']

    extend = await _Service(MagicMock()).build_topup_success_keyboard(_topup_user(with_subscription=True))
    assert _callbacks(extend) == ['subscription_extend', 'menu_balance', 'back_to_menu']
    assert _greens(extend) == 1
    assert _buttons(theme_markup(extend))[0].text == 'Продлить'


@pytest.mark.asyncio
async def test_theme_on_gift_cart_return_button_is_green(theme_on, cart):
    cart['cart'] = {'cart_mode': 'gift_purchase'}
    markup = await _Service(MagicMock()).build_topup_success_keyboard(_topup_user())
    assert _callbacks(markup)[0] == 'return_to_gift_cart'
    assert _greens(markup) == 1


@pytest.mark.asyncio
async def test_theme_on_keyboard_loses_the_return_button_once_auto_purchase_used_the_cart(theme_on, cart):
    bot = MagicMock()
    bot.edit_message_reply_markup = AsyncMock()
    service = _Service(bot)
    user = _topup_user()
    sent = await service.build_topup_success_keyboard(user)
    assert 'return_to_saved_cart' in _callbacks(sent)

    cart['cart'] = None  # автопокупка израсходовала корзину
    message = SimpleNamespace(chat=SimpleNamespace(id=42), message_id=5)
    await service.refresh_topup_success_keyboard(user, message, sent)

    kwargs = bot.edit_message_reply_markup.await_args.kwargs
    assert (kwargs['chat_id'], kwargs['message_id']) == (42, 5)
    assert _callbacks(kwargs['reply_markup']) == ['menu_buy', 'menu_balance', 'back_to_menu']
    assert _greens(kwargs['reply_markup']) == 1


@pytest.mark.asyncio
async def test_theme_on_keyboard_is_left_alone_while_the_cart_still_exists(theme_on, cart):
    bot = MagicMock()
    bot.edit_message_reply_markup = AsyncMock()
    service = _Service(bot)
    user = _topup_user()
    sent = await service.build_topup_success_keyboard(user)
    await service.refresh_topup_success_keyboard(user, SimpleNamespace(chat=SimpleNamespace(id=1), message_id=5), sent)
    bot.edit_message_reply_markup.assert_not_awaited()


@pytest.mark.asyncio
async def test_theme_on_refresh_never_raises(theme_on, cart):
    bot = MagicMock()
    bot.edit_message_reply_markup = AsyncMock(side_effect=RuntimeError('telegram is down'))
    service = _Service(bot)
    user = _topup_user()
    sent = await service.build_topup_success_keyboard(user)
    cart['cart'] = None
    await service.refresh_topup_success_keyboard(user, SimpleNamespace(chat=SimpleNamespace(id=1), message_id=5), sent)
    await service.refresh_topup_success_keyboard(user, None, sent)
    await service.refresh_topup_success_keyboard(user, SimpleNamespace(chat=SimpleNamespace(id=1), message_id=5), None)


# --- Поток RollyPay: порядок сообщений и обновление кнопок --------------------------------


class _RollyService(RollyPayPaymentMixin, PaymentCommonMixin):
    def __init__(self, bot):
        self.bot = bot


@pytest.fixture
def finalize_env(monkeypatch, cart):
    """Всё, куда `_finalize_rollypay_payment` ходит за данными и побочными эффектами, — заглушки."""
    events: list[str] = []
    user = SimpleNamespace(
        id=1,
        language='ru',
        telegram_id=42,
        balance_kopeks=0,
        has_made_first_topup=True,
        referred_by_id=None,
        subscriptions=[],
        user_promo_groups=[],
        updated_at=None,
        get_primary_promo_group=lambda: None,
    )
    payment = SimpleNamespace(
        user_id=1,
        order_id='order-1',
        amount_kopeks=50000,
        transaction_id=None,
        is_paid=True,
        metadata_json={},
    )

    payment_module = SimpleNamespace(
        get_user_by_id=AsyncMock(return_value=user),
        get_transaction_by_external_id=AsyncMock(return_value=None),
        create_transaction=AsyncMock(return_value=SimpleNamespace(id=77)),
    )
    rollypay_crud = SimpleNamespace(link_rollypay_payment_to_transaction=AsyncMock())
    modules = {'app.services.payment_service': payment_module, 'app.database.crud.rollypay': rollypay_crud}
    monkeypatch.setattr(rollypay_service, 'import_module', lambda name: modules[name])

    async def lock_user(db, locked):
        return locked

    async def fulfill_guest(*args, **kwargs):
        return None

    async def cart_side_effects(*args, **kwargs):
        events.append('auto-purchase')
        cart['cart'] = None
        return False

    monkeypatch.setattr('app.database.crud.user.lock_user_for_update', lock_user)
    monkeypatch.setattr('app.database.crud.transaction.emit_transaction_side_effects', AsyncMock())
    monkeypatch.setattr('app.services.referral_service.process_referral_topup', AsyncMock())
    monkeypatch.setattr('app.services.payment.common.try_fulfill_guest_purchase', fulfill_guest)
    monkeypatch.setattr('app.services.payment.common.send_cart_notification_after_topup', cart_side_effects)
    monkeypatch.setattr(
        'app.services.admin_notification_service.AdminNotificationService',
        lambda bot: SimpleNamespace(send_balance_topup_notification=AsyncMock()),
    )
    monkeypatch.setattr(rollypay_service, 'format_referrer_info', lambda user: '')

    bot = MagicMock()

    async def send_message(chat_id, text, **kwargs):
        events.append('success-message')
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=9)

    async def edit_markup(**kwargs):
        events.append('markup-refresh')

    bot.send_message = AsyncMock(side_effect=send_message)
    bot.edit_message_reply_markup = AsyncMock(side_effect=edit_markup)

    async def commit():
        # Последний коммит платежа — тот, что записывает в метаданные `balance_credited`.
        if (payment.metadata_json or {}).get('balance_credited'):
            events.append('payment-committed')

    db = MagicMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock(side_effect=commit)
    return SimpleNamespace(events=events, user=user, payment=payment, bot=bot, db=db, cart=cart)


async def _finalize(env):
    service = _RollyService(env.bot)
    return await service._finalize_rollypay_payment(env.db, env.payment, rollypay_payment_id='rp-1', trigger='test')


@pytest.mark.asyncio
async def test_theme_on_flow_sends_success_first_then_drops_the_spent_cart_button(theme_on, finalize_env):
    assert await _finalize(finalize_env) is True

    # Кнопки обновляются только после коммита, который отмечает платёж зачисленным.
    assert finalize_env.events == ['success-message', 'auto-purchase', 'payment-committed', 'markup-refresh']
    sent = finalize_env.bot.send_message.await_args
    assert sent.args[1] == (
        f'<b>{_tag("check")} Баланс пополнен</b>\n'
        '\n'
        f'{_tag("money_bag")} Зачислено: <b>500 ₽</b>\n'
        f'{_tag("coin")} На счёте: <b>500 ₽</b>'
    )
    assert 'return_to_saved_cart' in _callbacks(sent.kwargs['reply_markup'])
    refreshed = finalize_env.bot.edit_message_reply_markup.await_args.kwargs['reply_markup']
    assert 'return_to_saved_cart' not in _callbacks(refreshed)


@pytest.mark.asyncio
async def test_theme_on_flow_keeps_the_return_button_when_auto_purchase_did_not_use_the_cart(
    theme_on, finalize_env, monkeypatch
):
    async def no_purchase(*args, **kwargs):
        finalize_env.events.append('auto-purchase')
        return False

    monkeypatch.setattr('app.services.payment.common.send_cart_notification_after_topup', no_purchase)
    await _finalize(finalize_env)
    assert finalize_env.events == ['success-message', 'auto-purchase', 'payment-committed']
    finalize_env.bot.edit_message_reply_markup.assert_not_awaited()


@pytest.mark.asyncio
async def test_theme_on_flow_refresh_sees_the_subscription_bought_by_auto_purchase(theme_on, finalize_env):
    async def refresh(obj, attribute_names=None):
        # В базе после автопокупки у пользователя уже есть подписка; в памяти — ещё нет.
        if attribute_names == ['subscriptions']:
            obj.subscriptions = [SimpleNamespace(is_active=True, is_trial=False, actual_status='active')]

    finalize_env.db.refresh = AsyncMock(side_effect=refresh)
    await _finalize(finalize_env)

    refreshed = finalize_env.bot.edit_message_reply_markup.await_args.kwargs['reply_markup']
    assert _callbacks(refreshed) == ['subscription_extend', 'menu_balance', 'back_to_menu']
    assert _greens(refreshed) == 1


@pytest.mark.asyncio
async def test_theme_on_refresh_reloads_subscriptions_and_survives_a_failed_reload(theme_on, cart):
    bot = MagicMock()
    bot.edit_message_reply_markup = AsyncMock()
    service = _Service(bot)
    user = _topup_user()
    sent = await service.build_topup_success_keyboard(user)
    cart['cart'] = None
    message = SimpleNamespace(chat=SimpleNamespace(id=1), message_id=5)

    async def refresh(obj, attribute_names=None):
        obj.subscriptions = [SimpleNamespace(is_active=True, is_trial=False, actual_status='active')]

    db = MagicMock()
    db.refresh = AsyncMock(side_effect=refresh)
    await service.refresh_topup_success_keyboard(user, message, sent, db=db)
    db.refresh.assert_awaited_once_with(user, attribute_names=['subscriptions'])
    assert _callbacks(bot.edit_message_reply_markup.await_args.kwargs['reply_markup'])[0] == 'subscription_extend'

    db.refresh = AsyncMock(side_effect=RuntimeError('db is gone'))
    bot.edit_message_reply_markup.reset_mock()
    await service.refresh_topup_success_keyboard(_topup_user(), message, sent, db=db)
    bot.edit_message_reply_markup.assert_not_awaited()


@pytest.mark.asyncio
async def test_theme_off_flow_is_unchanged(theme_off, finalize_env):
    await _finalize(finalize_env)
    assert finalize_env.events == ['success-message', 'auto-purchase', 'payment-committed']
    sent = finalize_env.bot.send_message.await_args
    assert sent.args[1].startswith('✅ <b>Пополнение успешно!</b>')
    assert '🆔 Транзакция: 77' in sent.args[1]
    finalize_env.bot.edit_message_reply_markup.assert_not_awaited()


# --- Общие правила визуального языка -----------------------------------------------------


async def _every_screen(language: str, history, rollypay_api) -> list[tuple[str, str, object]]:
    screens: list[tuple[str, str, object]] = []

    def take(name, mock):
        text, markup = _sent(mock)
        screens.append((name, text, markup))

    history['rows'] = [
        _transaction('deposit', 1234500, 'Top-up via RollyPay with a really long description of the operation', day=16),
        _transaction('subscription_payment', -19900, 'Tariff purchase for 30 days', day=15),
    ]
    take('balance', (await show_menu(language)).message.edit_caption)
    take('history', (await show_history(history['rows'], language)).message.edit_text)
    history['rows'] = []
    take('history-empty', (await show_history([], language)).message.edit_text)
    take('methods', (await show_methods(language)).message.edit_text)
    take('restricted', (await show_methods(language, restriction_topup=True, restriction_reason='x')).message.edit_text)
    take('amount', (await ask_amount(language)).message.edit_text)
    take('link', (await create_payment(50000, language)).answer)
    take('support', (await support_topup(language)).message.edit_text)
    for name, kopeks in (('limit-low', 5000), ('limit-high', 20000000)):
        limit_message = MagicMock()
        limit_message.answer = AsyncMock()
        await rollypay_handler.process_rollypay_payment_amount(
            limit_message, _user(language), MagicMock(), kopeks, _state()
        )
        take(name, limit_message.answer)
    take('invalid-amount', (await type_amount('abc', language)).answer)
    screens.append(('success', rollypay_service._topup_success_text(_user(language), 50000, 'RollyPay', 77), None))
    rollypay_api.create_rollypay_payment.return_value = None
    take('create-error', (await create_payment(50000, language)).answer)
    return screens


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_theme_on_every_screen_follows_the_visual_language(
    theme_on, history, rollypay_api, quick_amounts, language
):
    for name, text, markup in await _every_screen(language, history, rollypay_api):
        # История — список: строки начинаются обычным значком 💰/💸 (анимированных на сообщение не больше пяти).
        assert_screen_pattern(name, text, list_screen=name == 'history')
        banned_symbols = ('💳', '🛠', '📝', '📅', '📊', '🚫', '⏰') + (() if name == 'history' else ('💰', '💸'))
        for banned in banned_symbols:
            assert banned not in _without_emoji(text), (name, banned)
        assert _greens(markup) <= 1, name
        if language == 'en':
            assert not CYRILLIC_RE.search(text.replace('₽', '')), (name, text)
            for button in _buttons(theme_markup(markup)) if markup is not None else []:
                assert not CYRILLIC_RE.search(button.text.replace('₽', '')), (name, button.text)


@pytest.mark.asyncio
async def test_theme_on_decision_screens_have_one_green_each(theme_on, history, rollypay_api, quick_amounts):
    greens = {name: _greens(markup) for name, _, markup in await _every_screen('ru', history, rollypay_api)}
    assert greens['balance'] == greens['methods'] == greens['link'] == greens['support'] == 1
    assert greens['history'] == greens['history-empty'] == greens['restricted'] == greens['amount'] == 0


# --- Шаблоны форка форматируются настоящими kwargs вызова --------------------------------

REAL_KWARGS = {
    'BALANCE_INFO': {'balance': '150 ₽'},
    'ROLLYPAY_ENTER_AMOUNT': {'name': 'RollyPay', 'min_amount': 100, 'max_amount': '100 000'},
    'ROLLYPAY_PAYMENT_CREATED': {'name': 'RollyPay', 'amount': '500'},
    'PAY_BUTTON': {'amount': '500'},
    'PAYMENT_AMOUNT_TOO_LOW': {'min_amount': 100},
    'PAYMENT_AMOUNT_TOO_HIGH': {'max_amount': '100 000'},
}
PLAIN_KEYS = (
    'BALANCE_HISTORY',
    'BALANCE_TOP_UP',
    'MY_BALANCE_BUTTON',
    'FORK_HISTORY_TITLE',
    'FORK_HISTORY_EMPTY',
    'PAYMENT_METHODS_TITLE',
    'PAYMENT_METHODS_PROMPT',
    'FORK_BUTTON_PAY_VIA',
    'PAYMENT_VIA_SUPPORT',
    'FORK_TOPUP_RESTRICTED',
    'FORK_RESTRICTION_DEFAULT_REASON',
    'FORK_BUTTON_APPEAL',
    'INVALID_AMOUNT',
    'PAYMENT_CREATE_ERROR',
    'FORK_TOPUP_SUCCESS',
    'FORK_SUPPORT_TOPUP',
    'FORK_BUTTON_WRITE_SUPPORT',
)
# Эти ключи форка подставляют не .format(), а .replace() — фигурные скобки значений им не страшны.
REPLACE_PLACEHOLDERS = {
    'FORK_TOPUP_RESTRICTED': {'{reason}'},
    'FORK_TOPUP_SUCCESS': {'{amount}', '{balance}'},
    'FORK_SUPPORT_TOPUP': {'{contact}', '{user_id}'},
    'FORK_BUTTON_PAY_VIA': {'{name}'},
}


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
@pytest.mark.parametrize('key', sorted(REPLACE_PLACEHOLDERS))
def test_replace_templates_carry_exactly_their_placeholders(theme_on, key, language):
    value = get_texts(language).get(key)
    assert set(re.findall(r'\{[a-z_]+\}', value)) == REPLACE_PLACEHOLDERS[key], key


@pytest.mark.parametrize('language', ['ru', 'en'])
@pytest.mark.parametrize('key', [*sorted(REAL_KWARGS), *PLAIN_KEYS])
def test_rewritten_texts_follow_the_visual_language(theme_on, key, language):
    value = get_texts(language).get(key)
    assert value is not None, key
    assert value.count('<tg-emoji') <= 5, key
    for banned in ('<blockquote', '<h1', '<h2', '<h3', '▰', '▓', '━'):
        assert banned not in value, (key, banned)
    assert '\n\n\n' not in value, key
    if value.startswith('<b>'):
        assert value.split('\n')[0].count('<tg-emoji') <= 1, key


def test_payment_methods_text_under_theme_does_not_depend_on_the_provider_list(theme_on, monkeypatch):
    monkeypatch.setattr(payment_utils, 'get_available_payment_methods', lambda: [{'id': 'support', 'name': 'x'}] * 2)
    assert payment_utils.get_payment_methods_text('ru') == payment_utils.fork_payment_methods_text(get_texts('ru'))
