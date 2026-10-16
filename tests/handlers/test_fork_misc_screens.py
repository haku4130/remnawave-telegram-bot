"""Инфо, промокод, подарки, язык и частые ошибки — под темой форка.

Без темы каждый экран обязан остаться байт-в-байт как у апстрима: ожидаемые строки сняты с кода до
переписывания, тема выключена. Под темой тексты берутся из слоя форка (app/localization/fork/), клавиатуры —
те же функции, что у бота; обработчики настоящие, с заглушками на всё, что ходит в базу и в Telegram.
Тексты БД (FAQ, правила, политика, оферта) не трогаются — меняются только заголовки и кнопки.
"""

from __future__ import annotations

import html
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Chat

import app.handlers.common as common_handler
import app.handlers.gift_activation as gift_handler
import app.handlers.menu as menu_handler
import app.handlers.promocode as promo_handler
import app.handlers.start as start_handler
from app.config import settings
from app.keyboards.inline import get_info_menu_keyboard, get_language_selection_keyboard
from app.localization import loader
from app.localization.texts import get_texts
from app.middlewares.premium_theme import theme_markup
from app.services import guest_purchase_service
from app.services.gift_claim_service import (
    GiftClaimAlreadyOwnedError,
    GiftClaimNotActivatableError,
    GiftClaimNotFoundError,
    GiftClaimSelfActivationError,
)
from app.services.guest_purchase_service import GuestPurchaseError, build_gift_received_message
from app.services.promocode_service import PromoCodeService
from app.utils.message_patch import caption_exceeds_telegram_limit


TAG_RE = re.compile(r'<[^>]+>')
CYRILLIC_RE = re.compile('[а-яА-ЯёЁ]')


def _tag(name: str) -> str:
    from app.localization.emoji_catalog import tag

    return tag(name)


def _plain(text: str) -> str:
    return TAG_RE.sub('', text)


def _buttons(markup) -> list:
    return [button for row in markup.inline_keyboard for button in row]


def _labels(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _themed(markup) -> list:
    return _buttons(theme_markup(markup))


def _greens(markup) -> list[str]:
    return [button.callback_data or button.url for button in _themed(markup) if button.style == 'success']


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


def _user(language: str = 'ru') -> SimpleNamespace:
    return SimpleNamespace(id=1, language=language, telegram_id=42)


def _callback(data: str = '', language_code: str | None = 'ru') -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.from_user.id = 42
    callback.from_user.language_code = language_code
    callback.answer = AsyncMock()
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    return callback


def _state(previous=None) -> MagicMock:
    state = MagicMock()
    state.get_state = AsyncMock(return_value=previous)
    state.get_data = AsyncMock(return_value={})
    state.set_state = AsyncMock()
    state.set_data = AsyncMock()
    state.update_data = AsyncMock()
    state.clear = AsyncMock()
    return state


# --- Информационное меню -------------------------------------------------------------------


async def show_info(monkeypatch, language: str = 'ru', **sections):
    sent: dict = {}

    async def fake(callback, caption, keyboard, parse_mode='HTML', **kwargs):
        sent['text'], sent['markup'] = caption, keyboard

    flags = {'privacy': True, 'offer': True, 'faq': True, 'promo_groups': False, **sections}
    monkeypatch.setattr(menu_handler, 'edit_or_answer_photo', fake)
    monkeypatch.setattr(
        menu_handler.PrivacyPolicyService, 'is_policy_enabled', AsyncMock(return_value=flags['privacy'])
    )
    monkeypatch.setattr(menu_handler.PublicOfferService, 'is_offer_enabled', AsyncMock(return_value=flags['offer']))
    monkeypatch.setattr(menu_handler.FaqService, 'is_enabled', AsyncMock(return_value=flags['faq']))
    monkeypatch.setattr(menu_handler, 'has_auto_assign_promo_groups', AsyncMock(return_value=flags['promo_groups']))
    monkeypatch.setattr(menu_handler, 'get_all_info_pages', AsyncMock(return_value=[]))
    await menu_handler.show_info_menu(_callback('menu_info'), _user(language), MagicMock())
    return sent['text'], sent['markup']


@pytest.mark.asyncio
async def test_theme_off_info_menu_matches_upstream(theme_off, monkeypatch):
    text, markup = await show_info(monkeypatch)
    assert text == 'ℹ️ <b>Инфо</b>\n\nВыберите раздел:'
    assert _labels(markup) == [['❓ FAQ'], ['🛡️ Политика конф.'], ['📄 Оферта'], ['📋 Правила сервиса'], ['⬅️ Назад']]
    assert all(button.style is None for button in _buttons(markup))


@pytest.mark.asyncio
async def test_theme_off_info_menu_en_matches_upstream(theme_off, monkeypatch):
    text, markup = await show_info(monkeypatch, 'en')
    assert text == 'ℹ️ <b>Info</b>\n\nChoose a section:'
    assert _labels(markup)[1:4] == [['🛡️ Privacy policy'], ['📄 Offer'], ['📋 Service rules']]


@pytest.mark.asyncio
async def test_info_menu_ru(theme_on, monkeypatch):
    text, markup = await show_info(monkeypatch)
    assert text == f'<b>{_tag("info")} Инфо</b>'
    assert [button.text for button in _buttons(markup)] == [
        f'{_tag("question")} Частые вопросы',
        f'{_tag("lock")} Конфиденциальность',
        f'{_tag("memo")} Оферта',
        f'{_tag("book")} Правила',
        '⬅️ Назад',
    ]
    assert [button.callback_data for button in _buttons(markup)] == [
        'menu_faq',
        'menu_privacy_policy',
        'menu_public_offer',
        'menu_rules',
        'back_to_menu',
    ]


@pytest.mark.asyncio
async def test_info_menu_en_has_no_cyrillic_and_back_is_plain(theme_on, monkeypatch):
    text, markup = await show_info(monkeypatch, 'en', promo_groups=True)
    assert text == f'<b>{_tag("info")} Info</b>'
    themed = _themed(markup)
    assert not any(CYRILLIC_RE.search(button.text) for button in themed)
    assert [button.callback_data for button in themed][-1] == 'back_to_menu'
    back = themed[-1]
    assert back.style is None and back.icon_custom_emoji_id is None
    # Главного действия на экране нет — зелёных кнопок тоже.
    assert _greens(markup) == []
    # Все кнопки-разделы (кроме «Назад») получают анимированную иконку.
    assert all(button.icon_custom_emoji_id for button in themed[:-1])


@pytest.mark.asyncio
async def test_info_menu_without_optional_sections(theme_on, monkeypatch):
    _, markup = await show_info(monkeypatch, privacy=False, offer=False, faq=False)
    assert [button.callback_data for button in _buttons(markup)] == ['menu_rules', 'back_to_menu']


def test_info_section_headers_under_theme(theme_on):
    ru, en = get_texts('ru'), get_texts('en')
    assert f'<b>{_tag("question")} Частые вопросы</b>' == ru.FAQ_HEADER
    assert f'<b>{_tag("book")} Правила сервиса</b>' == ru.RULES_HEADER
    assert f'<b>{_tag("lock")} Политика конфиденциальности</b>' == ru.PRIVACY_POLICY_HEADER
    assert f'<b>{_tag("memo")} Публичная оферта</b>' == ru.PUBLIC_OFFER_HEADER
    assert f'<b>{_tag("question")} FAQ</b>' == en.FAQ_HEADER
    assert f'<b>{_tag("memo")} Public offer</b>' == en.PUBLIC_OFFER_HEADER
    # Подсказки «Выберите …» убраны: кнопки и так очевидны.
    assert ru.FAQ_PAGES_PROMPT == '' and en.FAQ_PAGES_PROMPT == ''
    assert ru.MENU_INFO_PROMPT == '' and en.MENU_INFO_PROMPT == ''


def test_theme_off_info_section_headers_match_upstream(theme_off):
    ru = get_texts('ru')
    assert ru.FAQ_HEADER == '❓ <b>FAQ</b>'
    assert ru.RULES_HEADER == '📋 <b>Правила сервиса</b>'
    assert ru.PRIVACY_POLICY_HEADER == '🛡️ <b>Политика конфиденциальности</b>'
    assert ru.PUBLIC_OFFER_HEADER == '📄 <b>Публичная оферта</b>'
    assert ru.FAQ_PAGES_PROMPT == 'Выберите вопрос:'


@pytest.mark.asyncio
async def test_faq_list_has_no_prompt_under_theme_and_keeps_database_titles(theme_on, monkeypatch):
    pages = [SimpleNamespace(id=3, title='Как подключиться <b>?', is_active=True)]
    monkeypatch.setattr(menu_handler.FaqService, 'get_pages', AsyncMock(return_value=pages))
    callback = _callback('menu_faq')
    await menu_handler.show_faq_pages(callback, _user(), MagicMock())
    call = callback.message.edit_text.await_args
    assert call.args[0] == f'<b>{_tag("question")} Частые вопросы</b>'
    labels = [button.text for button in _buttons(call.kwargs['reply_markup'])]
    assert labels == ['1. Как подключиться <b>?', '⬅️ Назад']


def test_info_keyboard_without_theme_unchanged_in_structure(theme_off):
    markup = get_info_menu_keyboard('ru', show_faq=True, show_rules=False)
    assert _labels(markup) == [['❓ FAQ'], ['⬅️ Назад']]


# --- Язык ---------------------------------------------------------------------------------


@pytest.fixture
def languages(monkeypatch):
    monkeypatch.setattr(settings, 'LANGUAGE_SELECTION_ENABLED', True)
    monkeypatch.setattr(settings, 'AVAILABLE_LANGUAGES', 'ru,en')


async def show_language(monkeypatch, language: str = 'ru'):
    sent: dict = {}

    async def fake(callback, caption, keyboard, parse_mode='HTML', **kwargs):
        sent['text'], sent['markup'] = caption, keyboard

    monkeypatch.setattr(menu_handler, 'edit_or_answer_photo', fake)
    await menu_handler.show_language_menu(_callback('menu_language'), _user(language), MagicMock())
    return sent['text'], sent['markup']


@pytest.mark.asyncio
async def test_theme_off_language_screen_matches_upstream(theme_off, languages, monkeypatch):
    text, markup = await show_language(monkeypatch)
    assert text == '🌐 Выберите язык интерфейса:'
    assert _labels(markup) == [['✅ 🇷🇺 Русский', '🇬🇧 English'], ['⬅️ Назад']]


@pytest.mark.asyncio
async def test_language_screen_under_theme(theme_on, languages, monkeypatch):
    text, markup = await show_language(monkeypatch)
    assert text == f'<b>{_tag("globe")} Язык интерфейса</b>'
    assert _labels(markup) == [['✅ 🇷🇺 Русский', '🇬🇧 English'], ['⬅️ Назад']]
    assert _greens(markup) == []

    text, _ = await show_language(monkeypatch, 'en')
    assert text == f'<b>{_tag("globe")} Interface language</b>'


def test_theme_off_language_texts_match_upstream(theme_off):
    assert get_texts('ru').LANGUAGE_SELECTED == '🌐 Язык интерфейса установлен: <b>Русский</b>'
    assert start_handler._get_language_prompt_text() == '🌐 Выберите язык / Choose your language:'


def test_language_registration_prompt_is_bilingual_under_theme(theme_on):
    text = start_handler._get_language_prompt_text()
    assert text == f'<b>{_tag("globe")} Выберите язык / Choose your language</b>'
    assert not caption_exceeds_telegram_limit(text)


@pytest.mark.parametrize(('language', 'name'), [('ru', 'Русский'), ('en', 'English')])
def test_language_confirmation_is_toast_safe(theme_on, language, name):
    value = get_texts(language).LANGUAGE_SELECTED
    # Один и тот же ключ — тост и сообщение регистрации: ни <b>, ни других тегов, кроме эмодзи.
    assert '<b>' not in value and name in value
    assert value.count('<tg-emoji') == 1


@pytest.mark.asyncio
async def test_language_change_answers_with_confirmation(theme_on, languages, monkeypatch):
    updated = _user('en')
    monkeypatch.setattr(menu_handler, 'update_user', AsyncMock(return_value=updated))
    monkeypatch.setattr(menu_handler, 'show_main_menu', AsyncMock())
    callback = _callback('language_select:en')
    await menu_handler.process_language_change(callback, _user('ru'), MagicMock())
    assert callback.answer.await_args.args[0] == f'{_tag("check")} Language: English'


# --- Промокод: вопросы и ошибки ------------------------------------------------------------


PROMO_ERROR_KEYS = (
    'PROMOCODE_EMPTY_INPUT',
    'PROMOCODE_INVALID',
    'PROMOCODE_EXPIRED',
    'PROMOCODE_INACTIVE',
    'PROMOCODE_NOT_YET_VALID',
    'PROMOCODE_USED',
    'PROMOCODE_USER_NOT_FOUND',
    'PROMOCODE_NOT_FIRST_PURCHASE',
    'PROMOCODE_ACTIVE_DISCOUNT_EXISTS',
    'PROMOCODE_NO_SUBSCRIPTION',
    'PROMOCODE_SUBSCRIPTION_NOT_FOUND',
    'PROMOCODE_TRAFFIC_NOT_APPLICABLE',
    'PROMOCODE_TRIAL_SUBSCRIPTION_EXISTS',
    'PROMOCODE_TRIAL_PROVISIONING_FAILED',
    'PROMO_RATE_LIMITED',
    'PROMO_DAILY_LIMIT',
    'FORK_PROMO_SERVER_ERROR',
)


@pytest.mark.asyncio
async def test_theme_off_promocode_prompt_matches_upstream(theme_off):
    callback = _callback('menu_promocode')
    await promo_handler.show_promocode_menu(callback, _user(), _state())
    call = callback.message.edit_text.await_args
    assert call.args[0] == '🎫 Введите промокод:'
    assert _labels(call.kwargs['reply_markup']) == [['⬅️ Назад']]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('language', 'text'),
    [
        ('ru', '<b>{e} Промокод</b>\nОтправьте код сообщением.'),
        ('en', '<b>{e} Promo code</b>\nSend the code as a message.'),
    ],
)
async def test_promocode_prompt_under_theme(theme_on, language, text):
    callback = _callback('menu_promocode')
    state = _state()
    await promo_handler.show_promocode_menu(callback, _user(language), state)
    call = callback.message.edit_text.await_args
    assert call.args[0] == text.replace('{e}', _tag('ticket'))
    assert [button.callback_data for button in _buttons(call.kwargs['reply_markup'])] == ['back_to_menu']
    assert _greens(call.kwargs['reply_markup']) == []
    state.set_state.assert_awaited_once()


def test_theme_off_promocode_error_texts_match_upstream(theme_off):
    ru = get_texts('ru')
    assert ru.PROMOCODE_INVALID == '❌ Неверный промокод'
    assert ru.PROMOCODE_USED == '❌ Промокод уже использован'
    assert ru.t('PROMO_RATE_LIMITED').format(cooldown=5) == '⏳ Слишком много попыток. Попробуйте через 5 сек.'
    assert ru.PROMOCODE_EXPIRED == '❌ Промокод истек'


@pytest.mark.parametrize('language', ['ru', 'en'])
@pytest.mark.parametrize('key', PROMO_ERROR_KEYS)
def test_promocode_error_texts_follow_visual_language(theme_on, language, key):
    value = get_texts(language).t(key).format(cooldown=7)
    first = value.split('\n')[0]
    assert first.startswith('<b>') and first.endswith('</b>')
    assert value.count('<tg-emoji') == 1 and first.index('<tg-emoji') == len('<b>')
    assert '<blockquote' not in value and '{' not in value
    assert not caption_exceeds_telegram_limit(value)
    if language == 'en':
        assert not CYRILLIC_RE.search(_plain(value))


def test_rate_limit_text_uses_real_kwargs(theme_on):
    assert '12 сек' in get_texts('ru').t('PROMO_RATE_LIMITED').format(cooldown=12)
    assert '12 sec' in get_texts('en').t('PROMO_RATE_LIMITED').format(cooldown=12)


def _promo_message(text: str) -> MagicMock:
    message = MagicMock()
    message.text = text
    message.from_user.id = 42
    message.answer = AsyncMock()
    message.bot = MagicMock()
    return message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('error', 'key'),
    [
        ('not_found', 'PROMOCODE_INVALID'),
        ('expired', 'PROMOCODE_EXPIRED'),
        ('inactive', 'PROMOCODE_INACTIVE'),
        ('used', 'PROMOCODE_USED'),
        ('daily_limit', 'PROMO_DAILY_LIMIT'),
        ('trial_subscription_exists', 'PROMOCODE_TRIAL_SUBSCRIPTION_EXISTS'),
    ],
)
async def test_process_promocode_answers_with_themed_error(theme_on, monkeypatch, error, key):
    monkeypatch.setattr(
        promo_handler, 'activate_promocode_for_registration', AsyncMock(return_value={'success': False, 'error': error})
    )
    message = _promo_message('SUMMER')
    state = _state()
    state.get_data = AsyncMock(return_value={'_prev_state': None, '_prev_data': {}})
    await promo_handler.process_promocode(message, _user(), state, MagicMock())
    call = message.answer.await_args
    assert call.args[0] == get_texts('ru').t(key)
    assert _greens(call.kwargs['reply_markup']) == []


# --- Промокод: успех ----------------------------------------------------------------------


def _success(*effects: dict) -> dict:
    return {'success': True, 'description': '✅ Русское описание', 'effects': list(effects)}


def test_theme_off_promocode_success_keeps_upstream_description(theme_off):
    ru = get_texts('ru')
    assert (
        promo_handler.build_promocode_success_text(ru, _success({'kind': 'balance', 'kopeks': 5000}))
        == '🎉 Промокод активирован! ✅ Русское описание'
    )


def test_promocode_success_every_effect_ru(theme_on):
    ru = get_texts('ru')
    text = promo_handler.build_promocode_success_text(
        ru,
        _success(
            {'kind': 'days', 'days': 30, 'tariff': ''},
            {'kind': 'days', 'days': 2, 'tariff': 'Про <i>'},
            {'kind': 'traffic', 'gb': 10},
            {'kind': 'balance', 'kopeks': 15050},
            {'kind': 'trial', 'days': 3},
            {'kind': 'discount', 'percent': 20, 'hours': 0},
            {'kind': 'discount', 'percent': 15, 'hours': 48},
            {'kind': 'promo_group', 'name': 'VIP & <co>'},
        ),
    )
    assert text.split('\n') == [
        f'<b>{_tag("party")} Промокод активирован</b>',
        'Подписка продлена на <b>30 дней</b>',
        'Подписка «Про &lt;i&gt;» продлена на <b>2 дня</b>',
        'Добавлено <b>10 ГБ</b> трафика',
        'На баланс зачислено <b>150.50 ₽</b>',
        'Включён пробный доступ на <b>3 дня</b>',
        'Скидка <b>20%</b> — до первой покупки',
        'Скидка <b>15%</b>, действует 48 ч',
        'Вам назначена группа скидок «VIP &amp; &lt;co&gt;»',
    ]
    assert text.count('<tg-emoji') == 1
    assert not caption_exceeds_telegram_limit(text)


def test_promocode_success_en_has_no_russian(theme_on):
    en = get_texts('en')
    text = promo_handler.build_promocode_success_text(
        en,
        _success(
            {'kind': 'days', 'days': 1, 'tariff': ''},
            {'kind': 'traffic', 'gb': 5},
            {'kind': 'balance', 'kopeks': 10000},
            {'kind': 'trial', 'days': 7},
            {'kind': 'discount', 'percent': 10, 'hours': 0},
            {'kind': 'discount', 'percent': 10, 'hours': 24},
            {'kind': 'promo_group', 'name': 'VIP'},
        ),
    )
    assert not CYRILLIC_RE.search(_plain(text).replace('₽', ''))
    assert 'Subscription extended by <b>1 day</b>' in text
    assert '<b>5 GB</b> of traffic added' in text


def test_promocode_success_without_effects_is_just_the_title(theme_on):
    assert promo_handler.build_promocode_success_text(get_texts('ru'), {'success': True, 'description': 'x'}) == (
        f'<b>{_tag("party")} Промокод активирован</b>'
    )


@pytest.mark.asyncio
async def test_process_promocode_success_uses_effects(theme_on, monkeypatch):
    result = _success({'kind': 'balance', 'kopeks': 10000})
    monkeypatch.setattr(promo_handler, 'activate_promocode_for_registration', AsyncMock(return_value=result))
    limiter = MagicMock()
    limiter.is_blocked.return_value = False
    limiter.can_activate.return_value = True
    monkeypatch.setattr('app.utils.promo_rate_limiter.promo_limiter', limiter)
    message = _promo_message('SUMMER')
    state = _state()
    state.get_data = AsyncMock(return_value={'_prev_state': None, '_prev_data': {}})
    await promo_handler.process_promocode(message, _user(), state, MagicMock())
    text = message.answer.await_args.args[0]
    assert text == f'<b>{_tag("party")} Промокод активирован</b>\nНа баланс зачислено <b>100 ₽</b>'
    limiter.record_activation.assert_called_once_with(42)


# --- Структурированные эффекты сервиса ------------------------------------------------------


def _promocode(**fields) -> SimpleNamespace:
    data = {
        'code': 'SUMMER',
        'type': 'balance',
        'balance_bonus_kopeks': 0,
        'subscription_days': 0,
        'traffic_gb': 0,
        'tariff_id': None,
        'promo_group_id': None,
    }
    data.update(fields)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_service_reports_balance_effect_details_and_keeps_russian_description(monkeypatch):
    import app.services.promocode_service as service_module

    monkeypatch.setattr(service_module, 'add_user_balance', AsyncMock())
    details: list[dict] = []
    description = await PromoCodeService()._apply_promocode_effects(
        MagicMock(),
        SimpleNamespace(telegram_id=42),
        _promocode(type='balance', balance_bonus_kopeks=15000),
        details=details,
    )
    assert description == '💰 Баланс пополнен на 150.0₽'
    assert details == [{'kind': 'balance', 'kopeks': 15000}]


@pytest.mark.asyncio
async def test_service_reports_discount_effect_details():
    db = MagicMock()
    db.flush = AsyncMock()
    user = SimpleNamespace(telegram_id=42, promo_offer_discount_percent=0, promo_offer_discount_expires_at=None)
    details: list[dict] = []
    description = await PromoCodeService()._apply_promocode_effects(
        db,
        user,
        _promocode(type='discount', balance_bonus_kopeks=20, subscription_days=24),
        details=details,
    )
    assert description == '💸 Получена скидка 20% (действует 24 ч.)'
    assert details == [{'kind': 'discount', 'percent': 20, 'hours': 24}]


@pytest.mark.asyncio
async def test_service_reports_days_effect_details(monkeypatch):
    import app.services.promocode_service as service_module

    target = SimpleNamespace(id=5, tariff=SimpleNamespace(name='Стандартный'))
    service = PromoCodeService()
    monkeypatch.setattr(service, '_pick_target_subscription', AsyncMock(return_value=target))
    monkeypatch.setattr(service_module, 'extend_subscription', AsyncMock())
    monkeypatch.setattr(service.subscription_service, 'update_remnawave_user', AsyncMock())
    monkeypatch.setattr(settings, 'MULTI_TARIFF_ENABLED', True)
    details: list[dict] = []
    description = await service._apply_promocode_effects(
        MagicMock(),
        SimpleNamespace(telegram_id=42),
        _promocode(type='subscription_days', subscription_days=30),
        details=details,
    )
    assert description == '⏰ Подписка «Стандартный» продлена на 30 дней'
    assert details == [{'kind': 'days', 'days': 30, 'tariff': 'Стандартный'}]


@pytest.mark.asyncio
async def test_service_works_without_details_argument(monkeypatch):
    import app.services.promocode_service as service_module

    monkeypatch.setattr(service_module, 'add_user_balance', AsyncMock())
    description = await PromoCodeService()._apply_promocode_effects(
        MagicMock(), SimpleNamespace(telegram_id=42), _promocode(type='balance', balance_bonus_kopeks=100)
    )
    assert description == '💰 Баланс пополнен на 1.0₽'


# --- Подарки: активация --------------------------------------------------------------------


def _db_session():
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=MagicMock())
    session.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=session)


async def activate_gift(monkeypatch, *, language: str = 'ru', claim=None, user='default', language_code='ru'):
    monkeypatch.setattr(gift_handler, 'AsyncSessionLocal', _db_session())
    found = _user(language) if user == 'default' else user
    monkeypatch.setattr(gift_handler, 'get_user_by_telegram_id', AsyncMock(return_value=found))
    monkeypatch.setattr(gift_handler, 'claim_bound_gift_for_user', claim or AsyncMock())
    callback = _callback('gift_activate:9', language_code=language_code)
    await gift_handler.handle_gift_activate(callback)
    return callback


def _edits(callback) -> list:
    return callback.message.edit_text.await_args_list


@pytest.mark.asyncio
async def test_theme_off_gift_activation_matches_upstream(theme_off, monkeypatch):
    purchase = SimpleNamespace(tariff=SimpleNamespace(name='Про <b>'), period_days=30)
    callback = await activate_gift(monkeypatch, claim=AsyncMock(return_value=purchase))
    first, last = _edits(callback)[0], _edits(callback)[-1]
    assert first.args[0] == '⏳ Активируем подарок...' and first.kwargs['parse_mode'] is None
    assert last.args[0] == '✅ <b>Подарок активирован!</b>\nПро &lt;b&gt; — 30 дн.\n\nВаша подписка обновлена.'

    callback = await activate_gift(monkeypatch, claim=AsyncMock(side_effect=GiftClaimNotFoundError()))
    last = _edits(callback)[-1]
    assert last.args[0] == 'Подарок не найден или недоступен.' and last.kwargs['parse_mode'] is None


@pytest.mark.asyncio
async def test_theme_off_gift_early_alerts_match_upstream(theme_off, monkeypatch):
    callback = _callback('gift_activate:oops')
    await gift_handler.handle_gift_activate(callback)
    assert callback.answer.await_args.args == ('Подарок не найден или недоступен.',)

    callback = _callback('gift_activate:5')
    callback.message = gift_handler.InaccessibleMessage(chat=Chat(id=1, type='private'), message_id=1)
    await gift_handler.handle_gift_activate(callback)
    assert callback.answer.await_args.args == ('Сообщение устарело. Попробуйте /start.',)


@pytest.mark.asyncio
async def test_gift_activation_success_under_theme(theme_on, monkeypatch):
    purchase = SimpleNamespace(tariff=SimpleNamespace(name='Про <b>'), period_days=21)
    callback = await activate_gift(monkeypatch, claim=AsyncMock(return_value=purchase))
    first, last = _edits(callback)[0], _edits(callback)[-1]
    assert first.args[0] == f'<b>{_tag("hourglass")} Активируем подарок</b>'
    assert first.kwargs['parse_mode'] == 'HTML'
    assert last.args[0] == (f'<b>{_tag("gift")} Подарок активирован</b>\nПро &lt;b&gt; · 21 день\nПодписка обновлена.')
    assert not caption_exceeds_telegram_limit(last.args[0])


@pytest.mark.asyncio
async def test_gift_activation_success_en_under_theme(theme_on, monkeypatch):
    purchase = SimpleNamespace(tariff=SimpleNamespace(name='Standard'), period_days=30)
    callback = await activate_gift(
        monkeypatch, language='en', claim=AsyncMock(return_value=purchase), language_code='en'
    )
    last = _edits(callback)[-1]
    assert last.args[0] == (f'<b>{_tag("gift")} Gift activated</b>\nStandard · 30 days\nYour subscription is updated.')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('error', 'key'),
    [
        (GiftClaimNotFoundError(), 'GIFT_ACTIVATION_NOT_FOUND'),
        (GiftClaimAlreadyOwnedError(), 'GIFT_ACTIVATION_NOT_FOUND'),
        (GiftClaimSelfActivationError(), 'GIFT_ACTIVATION_SELF_CLAIM_ERROR'),
        (GiftClaimNotActivatableError(), 'GIFT_ACTIVATION_NOT_ACTIVATABLE_ERROR'),
        (GuestPurchaseError('boom', 503), 'GIFT_ACTIVATION_GENERIC_ERROR'),
        (RuntimeError('boom'), 'GIFT_ACTIVATION_GENERIC_ERROR'),
    ],
)
async def test_gift_activation_errors_under_theme(theme_on, monkeypatch, error, key):
    callback = await activate_gift(monkeypatch, claim=AsyncMock(side_effect=error))
    last = _edits(callback)[-1]
    assert last.args[0] == get_texts('ru').t(key)
    assert last.kwargs['parse_mode'] == 'HTML'


@pytest.mark.asyncio
async def test_gift_activation_business_error_is_escaped_once(theme_on, monkeypatch):
    claim = AsyncMock(side_effect=GuestPurchaseError('Срок <истёк> & всё', 409))
    callback = await activate_gift(monkeypatch, claim=claim)
    last = _edits(callback)[-1]
    assert last.args[0] == (
        f'<b>{_tag("cross")} Не получилось активировать подарок</b>\n' + html.escape('Срок <истёк> & всё')
    )
    assert last.kwargs['parse_mode'] == 'HTML'


@pytest.mark.asyncio
async def test_gift_activation_unknown_user_under_theme(theme_on, monkeypatch):
    callback = await activate_gift(monkeypatch, user=None)
    last = _edits(callback)[-1]
    assert last.args[0] == get_texts('ru').t('GIFT_ACTIVATION_NOT_FOUND')


@pytest.mark.asyncio
async def test_gift_early_alerts_are_plain_and_localized_under_theme(theme_on):
    callback = _callback('gift_activate:oops', language_code='en-US')
    await gift_handler.handle_gift_activate(callback)
    assert callback.answer.await_args.args == ('Gift not found or unavailable',)
    assert callback.answer.await_args.kwargs == {'show_alert': True}

    callback = _callback('gift_activate:5', language_code='ru')
    callback.message = gift_handler.InaccessibleMessage(chat=Chat(id=1, type='private'), message_id=1)
    await gift_handler.handle_gift_activate(callback)
    assert callback.answer.await_args.args == ('Сообщение устарело. Отправьте /start',)


def test_gift_activated_text_other_flows_under_theme(theme_on):
    ru = get_texts('ru')
    assert gift_handler.gift_activated_text(ru, 'Про', 90, callback_variant=False) == (
        f'<b>{_tag("gift")} Подарок активирован</b>\nПро · 90 дней\nПодписка обновлена.'
    )
    assert gift_handler.gift_activated_text(ru, '', 5, callback_variant=False).split('\n')[1] == '5 дней'
    assert gift_handler.gift_activated_text(ru, 'Про', 0, callback_variant=False).split('\n')[1] == 'Про'


def test_theme_off_gift_activated_text_matches_upstream(theme_off):
    ru = get_texts('ru')
    assert gift_handler.gift_activated_text(ru, 'Про', 30, callback_variant=False) == (
        '🎁 <b>Подарок активирован!</b>\nПро — 30 дн.\n\nВаша подписка обновлена.'
    )
    assert gift_handler.gift_activated_text(ru, 'Про', 30, callback_variant=True) == (
        '✅ <b>Подарок активирован!</b>\nПро — 30 дн.\n\nВаша подписка обновлена.'
    )
    assert gift_handler.gift_activated_text(ru, '', None, callback_variant=True).split('\n')[1] == ''


# --- Подарки: уведомление получателю --------------------------------------------------------


def test_theme_off_gift_notification_matches_upstream(theme_off):
    text, markup = build_gift_received_message(
        'ru',
        tariff_name='Про <b>',
        period_days=30,
        gift_from='Аня <x>',
        gift_message='С "праздником"',
        purchase_id=9,
        is_pending_activation=True,
    )
    assert text == (
        '🎁 <b>Вам подарили VPN подписку!</b>\nПро &lt;b&gt; — 30 дн.\nОт: Аня &lt;x&gt;\n\n"С &quot;праздником&quot;"'
        '\n\nУ вас уже есть активная подписка. Нажмите кнопку ниже, чтобы активировать подарок '
        '(текущая подписка будет заменена).'
    )
    assert _labels(markup) == [['Активировать подарок']]
    assert markup.inline_keyboard[0][0].callback_data == 'gift_activate:9'
    assert markup.inline_keyboard[0][0].style is None

    text, markup = build_gift_received_message(
        'ru',
        tariff_name='',
        period_days=None,
        gift_from=None,
        gift_message=None,
        purchase_id=9,
        is_pending_activation=False,
    )
    assert text == '🎁 <b>Вам подарили VPN подписку!</b>\n'
    assert markup is None


@pytest.mark.parametrize('language', ['ru', 'en'])
def test_gift_notification_under_theme(theme_on, language):
    text, markup = build_gift_received_message(
        language,
        tariff_name='Про <b>',
        period_days=30,
        gift_from='Аня <x>',
        gift_message='Тебе <3',
        purchase_id=9,
        is_pending_activation=True,
    )
    lines = text.split('\n')
    assert lines[0].startswith(f'<b>{_tag("gift")} ') and lines[0].endswith('</b>')
    assert lines[1] == ('Про &lt;b&gt; · 30 дней' if language == 'ru' else 'Про &lt;b&gt; · 30 days')
    assert lines[2] == ('От: Аня &lt;x&gt;' if language == 'ru' else 'From: Аня &lt;x&gt;')
    assert 'Тебе &lt;3' in text and '<3' not in text
    assert text.count('<tg-emoji') == 1
    assert not caption_exceeds_telegram_limit(text)
    button = markup.inline_keyboard[0][0]
    assert button.callback_data == 'gift_activate:9' and button.style == 'success'
    assert len(_buttons(markup)) == 1 and _greens(markup) == ['gift_activate:9']
    assert button.text.startswith(_tag('gift'))
    if language == 'en':
        assert not CYRILLIC_RE.search(_plain(lines[0]))
        assert 'Anna' not in text


def test_gift_notification_without_pending_has_no_button_under_theme(theme_on):
    text, markup = build_gift_received_message(
        'en',
        tariff_name='Std',
        period_days=1,
        gift_from=None,
        gift_message=None,
        purchase_id=3,
        is_pending_activation=False,
    )
    assert markup is None
    assert text == f'<b>{_tag("gift")} You received a subscription as a gift</b>\nStd · 1 day'


@pytest.mark.asyncio
async def test_notification_sender_uses_builder_and_recipient_language(theme_on, monkeypatch):
    sent = {}

    class FakeBot:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def send_message(self, **kwargs):
            sent.update(kwargs)

    monkeypatch.setattr(settings, 'BOT_TOKEN', '123:abc')
    monkeypatch.setattr('app.bot_factory.create_bot', lambda *a, **k: FakeBot())
    purchase = SimpleNamespace(
        id=9,
        user=SimpleNamespace(telegram_id=42, language='en'),
        contact_value='Anna',
        gift_message=None,
        period_days=30,
    )
    await guest_purchase_service._send_telegram_gift_notification(
        purchase, is_pending_activation=True, tariff_name='Std'
    )
    assert sent['chat_id'] == 42
    assert sent['text'].startswith(
        f'<b>{_tag("gift")} You received a subscription as a gift</b>\nStd · 30 days\nFrom: Anna'
    )
    assert sent['reply_markup'].inline_keyboard[0][0].callback_data == 'gift_activate:9'


# --- Общие ошибки --------------------------------------------------------------------------


def test_theme_off_common_errors_match_upstream(theme_off):
    ru, en = get_texts('ru'), get_texts('en')
    assert ru.ERROR == '❌ Произошла ошибка' and en.ERROR == '❌ An error occurred'
    assert ru.ACCESS_DENIED == '❌ Доступ запрещен'
    assert ru.USER_NOT_FOUND_ERROR == 'Ошибка: пользователь не найден.'
    assert ru.UNKNOWN_COMMAND_MESSAGE == '❓ Не понимаю эту команду. Используйте кнопки меню.'
    assert ru.OPERATION_CANCELLED == '❌ Операция отменена'


@pytest.mark.parametrize('language', ['ru', 'en'])
@pytest.mark.parametrize('key', ['ERROR', 'ACCESS_DENIED', 'USER_NOT_FOUND_ERROR', 'GIFT_ACTIVATION_OBSOLETE_MESSAGE'])
def test_alert_keys_are_plain_text(theme_on, language, key):
    # Эти ключи идут и в callback.answer (тост/алерт без HTML), и в сообщения: ни тегов, ни эмодзи-маркеров.
    value = get_texts(language).t(key)
    assert '<' not in value and '{' not in value
    assert len(value) <= 200
    if language == 'en':
        assert not CYRILLIC_RE.search(value)


def test_error_text_survives_exclamation_suffix_used_by_registration(theme_on):
    assert f'{get_texts("ru").ERROR}!' == 'Что-то пошло не так. Попробуйте ещё раз!'


@pytest.mark.asyncio
async def test_unknown_command_answer_under_theme(theme_on):
    message = MagicMock()
    message.answer = AsyncMock()
    await common_handler.handle_unknown_message(message, _user('en'))
    call = message.answer.await_args
    assert call.args[0] == f"<b>{_tag('question')} I don't get that command</b>\nUse the menu buttons."
    assert [button.callback_data for button in _buttons(call.kwargs['reply_markup'])] == ['back_to_menu']


@pytest.mark.asyncio
async def test_cancel_confirmation_under_theme(theme_on):
    callback = _callback('cancel')
    await common_handler.handle_cancel(callback, _state(), _user())
    assert callback.message.edit_text.await_args.args[0] == f'<b>{_tag("cross")} Отменено</b>'


def test_new_texts_never_leave_unresolved_markers(theme_on):
    for language in ('ru', 'en'):
        texts = get_texts(language)
        for key in (
            'MENU_INFO_HEADER',
            'MENU_FAQ',
            'MENU_PRIVACY_POLICY',
            'MENU_PUBLIC_OFFER',
            'MENU_RULES',
            'MENU_PROMO_GROUPS_INFO',
            'LANGUAGE_PROMPT',
            'PROMOCODE_ENTER',
            'GIFT_ACTIVATION_IN_PROGRESS',
            'FORK_PROMO_SUCCESS_TITLE',
            'FORK_GIFT_ACTIVATE_BUTTON',
        ):
            assert '{e:' not in texts.t(key), key


def test_language_keyboard_unchanged(theme_on, languages):
    markup = get_language_selection_keyboard('en', include_back=True, language='en')
    assert _labels(markup) == [['🇷🇺 Русский', '✅ 🇬🇧 English'], ['⬅️ Back']]


# --- Язык без слоя форка (в базе есть пользователь с языком 'tr') ---------------------------


def test_promocode_success_without_a_fork_locale_falls_back_to_upstream(theme_on):
    text = promo_handler.build_promocode_success_text(get_texts('tr'), _success({'kind': 'balance', 'kopeks': 5000}))
    assert text == get_texts('tr').PROMOCODE_SUCCESS.format(description='✅ Русское описание')
    assert text


def test_gift_activated_text_without_a_fork_locale_falls_back_to_upstream(theme_on):
    tr = get_texts('tr')
    assert gift_handler.gift_activated_text(tr, 'Про', 30, callback_variant=True) == (
        '✅ <b>Подарок активирован!</b>\nПро — 30 дн.\n\nВаша подписка обновлена.'
    )
    assert gift_handler.gift_activated_text(tr, 'Про', 30, callback_variant=False) == (
        '🎁 <b>Подарок активирован!</b>\nПро — 30 дн.\n\nВаша подписка обновлена.'
    )


def test_gift_notification_without_a_fork_locale_falls_back_to_upstream(theme_on):
    text, markup = build_gift_received_message(
        'tr',
        tariff_name='Про',
        period_days=30,
        gift_from='Аня',
        gift_message=None,
        purchase_id=9,
        is_pending_activation=True,
    )
    assert text.startswith('🎁 <b>Вам подарили VPN подписку!</b>\nПро — 30 дн.\nОт: Аня')
    assert _labels(markup) == [['Активировать подарок']]
    assert markup.inline_keyboard[0][0].callback_data == 'gift_activate:9'


@pytest.mark.parametrize('language', ['tr', 'ua', 'zh'])
def test_no_empty_texts_for_languages_without_a_fork_locale(theme_on, language):
    texts = get_texts(language)
    assert promo_handler.build_promocode_success_text(texts, _success({'kind': 'days', 'days': 3, 'tariff': ''}))
    assert gift_handler.gift_activated_text(texts, 'Std', 3, callback_variant=True)
    text, markup = build_gift_received_message(
        language,
        tariff_name='Std',
        period_days=3,
        gift_from=None,
        gift_message=None,
        purchase_id=1,
        is_pending_activation=True,
    )
    assert text and all(button.text for button in _buttons(markup))


# --- Промокод: ошибка сервера ----------------------------------------------------------------


async def _promo_server_error(language: str) -> str:
    message = _promo_message('SUMMER')
    state = _state()
    state.get_data = AsyncMock(return_value={'_prev_state': None, '_prev_data': {}})
    await promo_handler.process_promocode(message, _user(language), state, MagicMock())
    return message.answer.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('language', 'expected'),
    [
        ('ru', '<b>{cross} Не получилось применить промокод</b>\nПопробуйте ещё раз чуть позже.'),
        ('en', "<b>{cross} Couldn't apply the promo code</b>\nPlease try again a bit later."),
    ],
)
async def test_promocode_server_error_under_theme(theme_on, monkeypatch, language, expected):
    monkeypatch.setattr(
        promo_handler,
        'activate_promocode_for_registration',
        AsyncMock(return_value={'success': False, 'error': 'server_error'}),
    )
    assert await _promo_server_error(language) == expected.format(cross=_tag('cross'))


@pytest.mark.asyncio
async def test_promocode_server_error_falls_back_to_the_common_error(theme_off, monkeypatch):
    monkeypatch.setattr(
        promo_handler,
        'activate_promocode_for_registration',
        AsyncMock(return_value={'success': False, 'error': 'server_error'}),
    )
    assert await _promo_server_error('ru') == '❌ Произошла ошибка'


@pytest.mark.asyncio
async def test_promocode_server_error_without_a_fork_locale(theme_on, monkeypatch):
    monkeypatch.setattr(
        promo_handler,
        'activate_promocode_for_registration',
        AsyncMock(return_value={'success': False, 'error': 'server_error'}),
    )
    assert await _promo_server_error('tr') == get_texts('tr').ERROR
