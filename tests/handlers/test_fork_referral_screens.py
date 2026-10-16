"""Реферальная программа — под темой форка.

Без темы каждый экран и каждое уведомление обязаны остаться байт-в-байт как у апстрима: строки ниже
сняты с кода до переписывания (618e2865), тема выключена. Под темой тексты собирают FORK_*-ключи и
переопределённые ключи апстрима (app/localization/fork/), клавиатуры — те же функции, что у бота.
Обработчики и сервис гоняются настоящие, с заглушками на всё, что ходит наружу.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import InlineKeyboardMarkup

import app.handlers.referral as ref
import app.services.referral_service as ref_service
from app.config import settings
from app.localization import loader
from app.middlewares.premium_theme import theme_markup
from app.utils.message_patch import caption_exceeds_telegram_limit


TAG_RE = re.compile(r'<[^>]+>')
CYRILLIC_RE = re.compile('[а-яА-ЯёЁ]')
BOT_LINK = 'https://t.me/zanity_bot?start=refABC'
CABINET_LINK = 'https://cab.example/?ref=ABC&u=1'


def _tag(name: str) -> str:
    from app.localization.emoji_catalog import tag

    return tag(name)


def _plain(text: str) -> str:
    return TAG_RE.sub('', text)


def _without_emoji(text: str) -> str:
    return re.sub(r'<tg-emoji[^>]*>.*?</tg-emoji>', '', text)


def _labels(markup: InlineKeyboardMarkup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def _greens(markup) -> int:
    return sum(1 for button in _buttons(theme_markup(markup)) if button.style == 'success')


@pytest.fixture
def prod_like(monkeypatch):
    monkeypatch.setattr(settings, 'REFERRAL_PROGRAM_ENABLED', True)
    monkeypatch.setattr(settings, 'REFERRAL_REWARD_SCHEME', 'legacy')
    monkeypatch.setattr(settings, 'REFERRAL_COMMISSION_PERCENT', 25)
    monkeypatch.setattr(settings, 'REFERRAL_FIRST_TOPUP_BONUS_KOPEKS', 10000)
    monkeypatch.setattr(settings, 'REFERRAL_INVITER_BONUS_KOPEKS', 10000)
    monkeypatch.setattr(settings, 'REFERRAL_MINIMUM_TOPUP_KOPEKS', 10000)
    monkeypatch.setattr(settings, 'REFERRAL_MAX_COMMISSION_PAYMENTS', 0)
    monkeypatch.setattr(settings, 'REFERRAL_NOTIFICATIONS_ENABLED', True)
    monkeypatch.setattr(settings, 'REFERRAL_WITHDRAWAL_ENABLED', False)
    monkeypatch.setattr(settings, 'PRICE_ROUNDING_ENABLED', False)
    monkeypatch.setattr(type(settings), 'get_bot_referral_link', lambda self, code, bot=None: BOT_LINK)
    monkeypatch.setattr(type(settings), 'get_cabinet_referral_link', lambda self, code: CABINET_LINK)


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


def _user(language: str = 'ru', **extra) -> SimpleNamespace:
    data = {
        'id': 1,
        'language': language,
        'telegram_id': 42,
        'referral_code': 'ABC',
        'referral_commission_percent': None,
        'full_name': 'Андрей',
    }
    data.update(extra)
    return SimpleNamespace(**data)


def _callback() -> MagicMock:
    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.bot.get_me = AsyncMock(return_value=SimpleNamespace(username='zanity_bot'))
    callback.message.edit_media = AsyncMock()
    callback.message.answer_photo = AsyncMock()
    callback.message.delete = AsyncMock()
    return callback


def _summary(**overrides) -> dict:
    data = {
        'invited_count': 5,
        'paid_referrals_count': 3,
        'active_referrals_count': 4,
        'conversion_rate': 60.0,
        'total_earned_kopeks': 125000,
        'total_earned_days': 0,
        'month_earned_kopeks': 45000,
        'month_earned_days': 0,
        'recent_earnings': [
            {
                'amount_kopeks': 2500,
                'reason': 'referral_commission_topup',
                'referral_name': 'Иван <b>',
                'days_granted': 0,
                'level': 1,
            },
            {
                'amount_kopeks': 20000,
                'reason': 'referral_first_topup',
                'referral_name': 'Мария',
                'days_granted': 0,
                'level': 1,
            },
        ],
        'earnings_by_type': {
            'referral_first_topup': {'count': 3, 'total_amount_kopeks': 60000, 'total_days': 0},
            'referral_commission_topup': {'count': 7, 'total_amount_kopeks': 65000, 'total_days': 0},
        },
    }
    data.update(overrides)
    return data


def _referral(index: int, **overrides) -> dict:
    data = {
        'id': index,
        'full_name': f'Друг {index}',
        'status': 'active',
        'has_made_first_topup': True,
        'topups_count': 2,
        'total_earned_kopeks': 15000,
        'days_earned': 0,
        'days_since_registration': 12,
        'days_since_activity': 3,
    }
    data.update(overrides)
    return data


def _referral_page(rows: list[dict], *, page: int = 1, total_pages: int = 1) -> dict:
    return {
        'referrals': rows,
        'total_count': len(rows),
        'has_next': page < total_pages,
        'has_prev': page > 1,
        'current_page': page,
        'total_pages': total_pages,
    }


def _analytics(**overrides) -> dict:
    data = {
        'earnings_by_period': {'today': 2500, 'week': 12000, 'month': 45000, 'quarter': 125000},
        'days_by_period': {'today': 0, 'week': 0, 'month': 0, 'quarter': 0},
        'top_referrals': [
            {'referral_name': 'Мария', 'total_earned_kopeks': 60000, 'total_earned_days': 0, 'earnings_count': 4},
            {'referral_name': 'Иван <b>', 'total_earned_kopeks': 25000, 'total_earned_days': 0, 'earnings_count': 2},
        ],
    }
    data.update(overrides)
    return data


# --- запуск обработчиков ------------------------------------------------------------------


def _capture_send(monkeypatch) -> dict:
    sent: dict = {}

    async def fake_edit(callback, text, keyboard, parse_mode='HTML', **kwargs):
        sent['text'], sent['markup'] = text, keyboard

    monkeypatch.setattr(ref, 'edit_or_answer_photo', fake_edit)
    return sent


async def show_main(monkeypatch, language: str = 'ru', summary: dict | None = None, **user_extra):
    sent = _capture_send(monkeypatch)
    monkeypatch.setattr(ref, 'get_user_referral_summary', AsyncMock(return_value=summary or _summary()))
    await ref.show_referral_info(_callback(), _user(language, **user_extra), MagicMock())
    return sent['text'], sent['markup']


async def show_list(monkeypatch, page_data: dict, language: str = 'ru', page: int = 1):
    sent = _capture_send(monkeypatch)
    monkeypatch.setattr(ref, 'get_detailed_referral_list', AsyncMock(return_value=page_data))
    await ref.show_detailed_referral_list(_callback(), _user(language), MagicMock(), page)
    return sent['text'], sent['markup']


async def show_analytics(monkeypatch, data: dict | None = None, language: str = 'ru'):
    sent = _capture_send(monkeypatch)
    monkeypatch.setattr(ref, 'get_referral_analytics', AsyncMock(return_value=data or _analytics()))
    await ref.show_referral_analytics(_callback(), _user(language), MagicMock())
    return sent['text'], sent['markup']


async def show_invite(monkeypatch, language: str = 'ru'):
    sent = _capture_send(monkeypatch)
    await ref.create_invite_message(_callback(), _user(language), MagicMock())
    return sent['text'], sent['markup']


async def show_qr(monkeypatch, tmp_path, language: str = 'ru'):
    monkeypatch.chdir(tmp_path)
    callback = _callback()
    await ref.show_referral_qr(callback, _user(language))
    call = callback.message.edit_media.await_args
    return call.args[0].caption, call.kwargs['reply_markup']


# --- уведомления сервиса ------------------------------------------------------------------


@pytest.fixture
def referral_world(monkeypatch):
    """Пригласивший (id 10) и приглашённый (id 20) в памяти; наружу ходят только заглушки."""
    world = SimpleNamespace(
        send=AsyncMock(),
        users={},
        db=MagicMock(),
    )
    world.db.commit = AsyncMock()
    world.db.rollback = AsyncMock()
    world.db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

    def make_users(language: str = 'ru', first_topup_done: bool = False):
        # Имена — данные пользователей: в английских проверках «нет кириллицы» они тоже латиницей.
        inviter_name, friend_name = ('Andrew', 'Ivan <b>') if language == 'en' else ('Андрей', 'Иван <b>')
        world.users = {
            10: SimpleNamespace(
                id=10,
                telegram_id=100,
                email=None,
                language=language,
                full_name=inviter_name,
                referral_commission_percent=None,
                referred_by_id=None,
                has_made_first_topup=True,
            ),
            20: SimpleNamespace(
                id=20,
                telegram_id=200,
                email=None,
                language=language,
                full_name=friend_name,
                referral_commission_percent=None,
                referred_by_id=10,
                has_made_first_topup=first_topup_done,
            ),
        }

    world.make_users = make_users
    monkeypatch.setattr(ref_service, 'send_referral_notification', world.send)
    monkeypatch.setattr(ref_service, 'get_user_by_id', AsyncMock(side_effect=lambda db, uid: world.users.get(uid)))
    monkeypatch.setattr(ref_service, 'add_user_balance', AsyncMock(return_value=True))
    monkeypatch.setattr(ref_service, 'create_referral_earning', AsyncMock())
    monkeypatch.setattr(ref_service, 'get_user_campaign_id', AsyncMock(return_value=None))
    monkeypatch.setattr(ref_service, 'calculate_referral_commission_percent', AsyncMock(return_value=25))
    monkeypatch.setattr(ref_service, 'get_referral_reward_payment_count', AsyncMock(return_value=0))
    monkeypatch.setattr(ref_service, '_is_commission_limit_reached', AsyncMock(return_value=False))
    return world


def _sent_messages(world) -> list[tuple[int, str]]:
    """(кому, текст) для каждого отправленного уведомления, по порядку."""
    return [(call.args[1], call.args[2]) for call in world.send.await_args_list]


async def register(world, language: str = 'ru'):
    world.make_users(language)
    await ref_service.process_referral_registration(world.db, 20, 10, bot=MagicMock())
    return _sent_messages(world)


async def first_topup(world, kopeks: int = 50000, language: str = 'ru'):
    world.make_users(language, first_topup_done=False)
    await ref_service.process_referral_topup(world.db, 20, kopeks, bot=MagicMock())
    return _sent_messages(world)


async def next_topup(world, kopeks: int = 30000, language: str = 'ru'):
    world.make_users(language, first_topup_done=True)
    await ref_service.process_referral_topup(world.db, 20, kopeks, bot=MagicMock())
    return _sent_messages(world)


# --- Без темы: как у апстрима -------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_off_main_screen_matches_upstream(theme_off, monkeypatch):
    text, markup = await show_main(monkeypatch)
    assert text == (
        '👥 <b>Реферальная программа</b>\n\n'
        '📊 <b>Ваша статистика:</b>\n'
        '• Приглашено пользователей: <b>5</b>\n'
        '• Сделали первое пополнение: <b>3</b>\n'
        '• Активных рефералов: <b>4</b>\n'
        '• Конверсия: <b>60.0%</b>\n'
        '• Заработано всего: <b>1250 ₽</b>\n'
        '• За последний месяц: <b>450 ₽</b>\n\n'
        '🎁 <b>Как работают награды:</b>\n'
        '• Новый пользователь получает: <b>100 ₽</b> при первом пополнении от <b>100 ₽</b>\n'
        '• Вы получаете при первом пополнении реферала: <b>100 ₽</b>\n'
        '• Комиссия с каждого пополнения реферала: <b>25%</b>\n\n'
        '🤖 <b>Ссылка на бота:</b>\nhttps://t.me/zanity_bot?start=refABC\n\n'
        '🌐 <b>Ссылка на кабинет:</b>\nhttps://cab.example/?ref=ABC&amp;u=1\n\n'
        '🆔 <b>Ваш код:</b> <code>ABC</code>\n\n'
        '💰 <b>Последние начисления:</b>\n'
        '• 💰 Комиссия с пополнения: <b>25 ₽</b> от Иван &lt;b&gt;\n'
        '• 🎉 Первое пополнение: <b>200 ₽</b> от Мария\n\n'
        '📈 <b>Доходы по типам:</b>\n'
        '• Бонусы за первые пополнения: <b>3</b> (600 ₽)\n'
        '• Комиссии с пополнений: <b>7</b> (650 ₽)\n\n'
        '📢 Приглашайте друзей и зарабатывайте!'
    )
    assert _labels(markup) == [
        ['📝 Создать приглашение'],
        ['📱 Показать QR код'],
        ['👥 Список рефералов'],
        ['📊 Аналитика'],
        ['⬅️ Назад'],
    ]
    assert [button.callback_data for button in _buttons(markup)] == [
        'referral_create_invite',
        'referral_show_qr',
        'referral_list',
        'referral_analytics',
        'back_to_menu',
    ]
    assert all(button.style is None for button in _buttons(markup))


@pytest.mark.asyncio
async def test_theme_off_list_matches_upstream(theme_off, monkeypatch):
    rows = [
        _referral(1),
        _referral(2, status='inactive', days_since_activity=None, has_made_first_topup=False, topups_count=0)
        | {'total_earned_kopeks': 0},
    ]
    text, markup = await show_list(monkeypatch, _referral_page(rows, page=2, total_pages=3), page=2)
    assert text == (
        '👥 <b>Ваши рефералы</b> (стр. 2/3)\n\n'
        '1. 🟢 <b>Друг 1</b>\n'
        '   💰 Пополнений: 2\n'
        '   💎 Заработано с него: 150 ₽\n'
        '   📅 Регистрация: 12 дн. назад\n'
        '   🕐 Активность: 3 дн. назад\n\n'
        '2. 🔴 <b>Друг 2</b>\n'
        '   ⏳ Пополнений: 0\n'
        '   💎 Заработано с него: 0 ₽\n'
        '   📅 Регистрация: 12 дн. назад\n'
        '   🕐 Активность: давно\n\n'
    )
    assert _labels(markup) == [['⬅️ Назад', 'Вперед ➡️'], ['⬅️ Назад']]
    assert [button.callback_data for button in _buttons(markup)] == [
        'referral_list_page_1',
        'referral_list_page_3',
        'menu_referrals',
    ]


@pytest.mark.asyncio
async def test_theme_off_empty_list_matches_upstream(theme_off, monkeypatch):
    text, markup = await show_list(monkeypatch, _referral_page([]))
    assert text == '📋 У вас пока нет рефералов.\n\nПоделитесь своей реферальной ссылкой, чтобы начать зарабатывать!'
    assert _labels(markup) == [['⬅️ Назад']]


@pytest.mark.asyncio
async def test_theme_off_analytics_matches_upstream(theme_off, monkeypatch):
    text, markup = await show_analytics(monkeypatch)
    assert text == (
        '📊 <b>Аналитика рефералов</b>\n\n'
        '💰 <b>Доходы по периодам:</b>\n'
        '• Сегодня: 25 ₽\n'
        '• За неделю: 120 ₽\n'
        '• За месяц: 450 ₽\n'
        '• За квартал: 1250 ₽\n\n'
        '🏆 <b>Топ-2 рефералов:</b>\n'
        '1. Мария: 600 ₽ (4 начислений)\n'
        '2. Иван &lt;b&gt;: 250 ₽ (2 начислений)\n\n'
        '📈 Продолжайте развивать свою реферальную сеть!'
    )
    assert _labels(markup) == [['⬅️ Назад']]


@pytest.mark.asyncio
async def test_theme_off_invite_matches_upstream(theme_off, monkeypatch):
    text, markup = await show_invite(monkeypatch)
    assert text == (
        '📝 <b>Приглашение создано!</b>\n\n'
        'Нажмите на текст ниже, чтобы скопировать:\n\n'
        '<blockquote>🎉 Присоединяйся к VPN сервису!\n\n'
        '💎 При первом пополнении от 100 ₽ ты получишь 100 ₽ бонусом на баланс!\n\n'
        '🚀 Быстрое подключение\n🌍 Серверы по всему миру\n🔒 Надежная защита\n\n'
        '👇 Переходи по ссылке:\n<code>https://t.me/zanity_bot?start=refABC</code>\n\n'
        '🌐 <code>https://cab.example/?ref=ABC&amp;u=1</code></blockquote>'
    )
    assert _labels(markup) == [['⬅️ Назад']]


@pytest.mark.asyncio
async def test_theme_off_qr_matches_upstream(theme_off, monkeypatch, tmp_path):
    caption, markup = await show_qr(monkeypatch, tmp_path)
    assert caption == (
        '🤖 Ссылка на бота:\nhttps://t.me/zanity_bot?start=refABC\n\n🌐 Ссылка на кабинет:\nhttps://cab.example/?ref=ABC&u=1'
    )
    assert _labels(markup) == [['⬅️ Назад']]


@pytest.mark.asyncio
async def test_theme_off_notifications_match_upstream(theme_off, referral_world):
    assert await register(referral_world) == [
        (
            200,
            (
                '🎉 <b>Добро пожаловать!</b>\n\nВы перешли по реферальной ссылке пользователя <b>Андрей</b>!\n\n'
                '💰 При первом пополнении от 100 ₽ вы получите бонус 100 ₽!'
            ),
        ),
        (
            100,
            (
                '👥 <b>Новый реферал!</b>\n\n'
                'По вашей ссылке зарегистрировался пользователь <b>Иван &lt;b&gt;</b>!\n\n'
                '💰 Когда он пополнит баланс от 100 ₽, вы получите 100 ₽ + 25% от суммы пополнения.\n\n'
                '📈 С каждого последующего пополнения вы будете получать 25% комиссии.'
            ),
        ),
    ]
    referral_world.send.reset_mock()
    assert await first_topup(referral_world) == [
        (
            200,
            '🎉 <b>Бонус получен!</b>\n\nЗа первое пополнение вы получили бонус 100 ₽!\n\n💎 Средства зачислены на ваш баланс.',
        ),
        (
            100,
            (
                '💰 <b>Реферальная награда!</b>\n\n'
                'Ваш реферал <b>Иван &lt;b&gt;</b> сделал первое пополнение на 500 ₽!\n\n'
                '🎁 Ваша награда: 225 ₽ (фикс. бонус 100 ₽ + комиссия 25% = 125 ₽)\n\n'
                '📈 Теперь с каждого его пополнения вы будете получать 25% комиссии.'
            ),
        ),
    ]
    referral_world.send.reset_mock()
    commission = (
        '💰 <b>Реферальная комиссия!</b>\n\nВаш реферал <b>Иван &lt;b&gt;</b> пополнил баланс на {amount}\n\n'
        '🎁 Ваша комиссия (25%): {fee}\n\n💎 Средства зачислены на ваш баланс.'
    )
    assert await next_topup(referral_world) == [(100, commission.format(amount='300 ₽', fee='75 ₽'))]
    referral_world.send.reset_mock()
    assert await first_topup(referral_world, 5000) == [(100, commission.format(amount='50 ₽', fee='12.5 ₽'))]


# --- Главный экран ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_main_screen_ru(theme_on, monkeypatch):
    text, markup = await show_main(monkeypatch)
    assert text == (
        f'<b>{_tag("people")} Приглашайте друзей — 25% с их оплат</b>\n'
        'Другу — <b>100 ₽</b> на баланс при первом пополнении от <b>100 ₽</b>.\n'
        'Вам — <b>100 ₽</b> за его первое пополнение.\n\n'
        'Приглашено: <b>5</b>, пополнили баланс: <b>3</b>\n'
        'Заработано: <b>1250 ₽</b>, за 30 дней: <b>450 ₽</b>\n\n'
        'Ваша ссылка:\n<code>https://t.me/zanity_bot?start=refABC</code>\n'
        'Ссылка на кабинет:\n<code>https://cab.example/?ref=ABC&amp;u=1</code>\n'
        'Код: <code>ABC</code>\n\n'
        'Последние начисления:\n'
        'Комиссия с пополнения — <b>25 ₽</b>, Иван &lt;b&gt;\n'
        'Первое пополнение — <b>200 ₽</b>, Мария'
    )
    assert text.count('<tg-emoji') == 1
    for banned in ('<blockquote', '<h1', '•', '█', '📊', '🎁', '🤖'):
        assert banned not in text
    assert not caption_exceeds_telegram_limit(text)


@pytest.mark.asyncio
async def test_theme_on_main_keyboard_has_one_green_invite_button(theme_on, monkeypatch):
    _, markup = await show_main(monkeypatch)
    styled = theme_markup(markup)
    assert [[button.text for button in row] for row in styled.inline_keyboard] == [
        ['Пригласить друга'],
        ['Рефералы', 'Аналитика'],
        ['QR-код'],
        ['⬅️ Назад'],
    ]
    assert [button.callback_data for button in _buttons(styled)] == [
        'referral_create_invite',
        'referral_list',
        'referral_analytics',
        'referral_show_qr',
        'back_to_menu',
    ]
    assert [button.style for button in _buttons(styled)] == ['success', None, None, None, None]
    assert [bool(button.icon_custom_emoji_id) for button in _buttons(styled)] == [True, True, True, True, False]
    assert _greens(markup) == 1


@pytest.mark.asyncio
async def test_theme_on_main_screen_en(theme_on, monkeypatch):
    latin = _summary()
    for earning, name in zip(latin['recent_earnings'], ('Ivan', 'Maria'), strict=True):
        earning['referral_name'] = name
    text, markup = await show_main(monkeypatch, 'en', summary=latin)
    assert not CYRILLIC_RE.search(text.replace('₽', ''))
    assert text.startswith(f'<b>{_tag("people")} Invite friends — 25% of their payments</b>\n')
    assert 'Invited: <b>5</b>, topped up: <b>3</b>' in text
    assert 'Earned: <b>1250 ₽</b>, last 30 days: <b>450 ₽</b>' in text
    assert all(not CYRILLIC_RE.search(label.replace('₽', '')) for row in _labels(theme_markup(markup)) for label in row)
    assert _greens(markup) == 1


@pytest.mark.asyncio
async def test_theme_on_main_screen_without_cabinet_bonuses_and_earnings(theme_on, monkeypatch):
    monkeypatch.setattr(type(settings), 'get_cabinet_referral_link', lambda self, code: None)
    monkeypatch.setattr(settings, 'REFERRAL_FIRST_TOPUP_BONUS_KOPEKS', 0)
    monkeypatch.setattr(settings, 'REFERRAL_INVITER_BONUS_KOPEKS', 0)
    summary = _summary(recent_earnings=[], earnings_by_type={}, invited_count=0, paid_referrals_count=0)
    text, _ = await show_main(monkeypatch, summary=summary)
    assert text == (
        f'<b>{_tag("people")} Приглашайте друзей — 25% с их оплат</b>\n\n'
        'Приглашено: <b>0</b>, пополнили баланс: <b>0</b>\n'
        'Заработано: <b>1250 ₽</b>, за 30 дней: <b>450 ₽</b>\n\n'
        'Ваша ссылка:\n<code>https://t.me/zanity_bot?start=refABC</code>\n'
        'Код: <code>ABC</code>'
    )


@pytest.mark.asyncio
async def test_theme_on_main_screen_with_a_personal_percent_and_a_payment_limit(theme_on, monkeypatch):
    monkeypatch.setattr(settings, 'REFERRAL_MAX_COMMISSION_PAYMENTS', 3)
    text, _ = await show_main(monkeypatch, referral_commission_percent=40)
    assert text.startswith(
        f'<b>{_tag("people")} Приглашайте друзей — 40% с их оплат</b>\nКомиссия — с первых 3 пополнений друга.\n'
    )


@pytest.mark.asyncio
async def test_theme_on_main_screen_without_commission_does_not_promise_zero_percent(theme_on, monkeypatch):
    text, _ = await show_main(monkeypatch, referral_commission_percent=0)
    assert text.startswith(f'<b>{_tag("people")} Приглашайте друзей</b>\n')
    assert '0%' not in text.splitlines()[0]


@pytest.mark.asyncio
async def test_theme_on_main_screen_shows_days_rewards_and_unknown_reasons(theme_on, monkeypatch):
    summary = _summary(
        recent_earnings=[
            {'amount_kopeks': 0, 'reason': 'referral_days_reward', 'referral_name': 'Мария', 'days_granted': 7},
            {'amount_kopeks': 500, 'reason': 'something_new', 'referral_name': 'Олег', 'days_granted': 0},
            {'amount_kopeks': 0, 'reason': 'referral_registration_pending', 'referral_name': 'Нуль', 'days_granted': 0},
        ]
    )
    text, _ = await show_main(monkeypatch, summary=summary)
    assert 'Дни подписки — <b>7 дн.</b>, Мария\nsomething_new — <b>5 ₽</b>, Олег' in text
    assert 'Нуль' not in text


@pytest.mark.asyncio
async def test_theme_on_main_screen_fits_the_logo_caption_in_the_worst_case(theme_on, monkeypatch):
    long_name = 'Очень-очень-длинное-имя-реферала-из-64-символов ' * 2
    summary = _summary(
        recent_earnings=[
            {'amount_kopeks': 99999900, 'reason': 'referral_first_topup', 'referral_name': long_name, 'days_granted': 0}
        ]
        * 5,
        total_earned_kopeks=999999900,
        month_earned_kopeks=999999900,
    )
    monkeypatch.setattr(
        type(settings),
        'get_cabinet_referral_link',
        lambda self, code: 'https://cabinet.example.com/login?ref=' + 'A' * 40,
    )
    for language in ('ru', 'en'):
        text, _ = await show_main(monkeypatch, language, summary=summary, referral_code='A' * 24)
        assert not caption_exceeds_telegram_limit(text), len(_plain(text))
        assert text.count('<tg-emoji') == 1


@pytest.mark.asyncio
async def test_theme_on_main_screen_with_the_levels_scheme_keeps_the_upstream_path(theme_on, monkeypatch):
    """Схема уровней на проде выключена; под темой она остаётся рабочей и не падает на новых ключах."""
    monkeypatch.setattr(settings, 'REFERRAL_REWARD_SCHEME', 'levels')
    import app.services.referral_reward_service as rewards

    monkeypatch.setattr(rewards, 'describe_active_levels', AsyncMock(return_value=['Ур. 1: 10%']))
    monkeypatch.setattr(rewards, 'describe_referee_bonus', AsyncMock(return_value=None))
    monkeypatch.setattr(rewards, 'resolve_tier_progress', AsyncMock(return_value=None))
    monkeypatch.setattr(rewards, 'format_tier_progress', lambda progress, language=None: [])
    monkeypatch.setattr(ref, '_reward_tariff_names', AsyncMock(return_value={}))
    text, markup = await show_main(monkeypatch)
    assert 'Ур. 1: 10%' in text
    assert markup.inline_keyboard


# --- Список -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_list_ru(theme_on, monkeypatch):
    rows = [
        _referral(1),
        _referral(2, status='inactive', days_since_activity=None, has_made_first_topup=False, topups_count=0)
        | {'total_earned_kopeks': 0},
        _referral(3, full_name='Мария <b>', status='inactive', days_since_activity=45),
    ]
    text, markup = await show_list(monkeypatch, _referral_page(rows, page=2, total_pages=3), page=2)
    assert text == (
        f'<b>{_tag("people")} Ваши рефералы</b>\n'
        'Страница 2 из 3\n'
        '11. <b>Друг 1</b> — <b>150 ₽</b>, пополнений: 2\n'
        '12. <b>Друг 2</b> — пока не пополнял · давно не заходил\n'
        '13. <b>Мария &lt;b&gt;</b> — <b>150 ₽</b>, пополнений: 2 · давно не заходил'
    )
    assert _labels(markup) == [['◀️ Предыдущие', 'Следующие ▶️'], ['⬅️ Назад']]
    assert [button.callback_data for button in _buttons(markup)] == [
        'referral_list_page_1',
        'referral_list_page_3',
        'menu_referrals',
    ]
    assert _greens(markup) == 0
    assert all(button.style is None and not button.icon_custom_emoji_id for button in _buttons(theme_markup(markup)))


@pytest.mark.asyncio
async def test_theme_on_list_single_page_has_no_page_line(theme_on, monkeypatch):
    text, _ = await show_list(monkeypatch, _referral_page([_referral(1)]))
    assert text == f'<b>{_tag("people")} Ваши рефералы</b>\n1. <b>Друг 1</b> — <b>150 ₽</b>, пополнений: 2'


@pytest.mark.asyncio
async def test_theme_on_list_en(theme_on, monkeypatch):
    rows = [
        _referral(1, full_name='Friend 1'),
        _referral(2, full_name='Friend 2', status='inactive', has_made_first_topup=False, topups_count=0)
        | {'total_earned_kopeks': 0},
    ]
    text, markup = await show_list(monkeypatch, _referral_page(rows, page=1, total_pages=2), 'en')
    assert not CYRILLIC_RE.search(text.replace('₽', ''))
    assert 'top-ups: 2' in text and 'no top-up yet' in text
    assert [button.text for button in _buttons(theme_markup(markup))] == ['Next ▶️', '⬅️ Back']


@pytest.mark.asyncio
async def test_theme_on_full_list_page_fits_the_logo_caption(theme_on, monkeypatch):
    rows = [
        _referral(
            number,
            full_name='Очень длинное имя реферала, которое Telegram разрешает до шестидесяти четырёх знаков',
            status='inactive',
            total_earned_kopeks=99999900,
            topups_count=1234,
        )
        for number in range(1, 11)
    ]
    for language in ('ru', 'en'):
        text, _ = await show_list(monkeypatch, _referral_page(rows, page=12, total_pages=99), language, page=12)
        assert not caption_exceeds_telegram_limit(text), len(_plain(text))


@pytest.mark.asyncio
async def test_theme_on_empty_list_offers_to_invite(theme_on, monkeypatch):
    text, markup = await show_list(monkeypatch, _referral_page([]))
    assert text == (
        f'<b>{_tag("people")} Рефералов пока нет</b>\n'
        'Отправьте другу ссылку — награда придёт, когда он пополнит баланс.'
    )
    styled = theme_markup(markup)
    assert [button.text for button in _buttons(styled)] == ['Пригласить друга', '⬅️ Назад']
    assert [button.callback_data for button in _buttons(styled)] == ['referral_create_invite', 'menu_referrals']
    assert _greens(markup) == 1


@pytest.mark.asyncio
async def test_theme_on_empty_list_en(theme_on, monkeypatch):
    text, markup = await show_list(monkeypatch, _referral_page([]), 'en')
    assert not CYRILLIC_RE.search(text)
    assert [button.text for button in _buttons(theme_markup(markup))] == ['Invite a friend', '⬅️ Back']


# --- Аналитика ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_analytics_ru(theme_on, monkeypatch):
    text, markup = await show_analytics(monkeypatch)
    assert text == (
        f'<b>{_tag("chart_up")} Доходы с рефералов</b>\n'
        'Сегодня: <b>25 ₽</b>\n'
        'За неделю: <b>120 ₽</b>\n'
        'За месяц: <b>450 ₽</b>\n'
        'За квартал: <b>1250 ₽</b>\n\n'
        'Лучшие рефералы:\n'
        '1. Мария — <b>600 ₽</b>\n'
        '2. Иван &lt;b&gt; — <b>250 ₽</b>'
    )
    assert _labels(markup) == [['⬅️ Назад']]
    assert _greens(markup) == 0
    assert not caption_exceeds_telegram_limit(text)


@pytest.mark.asyncio
async def test_theme_on_analytics_without_top_and_en(theme_on, monkeypatch):
    text, _ = await show_analytics(monkeypatch, _analytics(top_referrals=[]))
    assert 'Лучшие' not in text and text.endswith('За квартал: <b>1250 ₽</b>')

    latin = _analytics()
    for top, name in zip(latin['top_referrals'], ('Maria', 'Ivan'), strict=True):
        top['referral_name'] = name
    text, _ = await show_analytics(monkeypatch, latin, language='en')
    assert not CYRILLIC_RE.search(text.replace('₽', ''))
    assert 'Top referrals:' in text


@pytest.mark.asyncio
async def test_theme_on_analytics_days_rewards(theme_on, monkeypatch):
    data = _analytics()
    data['earnings_by_period']['today'] = 0
    data['days_by_period']['today'] = 7
    text, _ = await show_analytics(monkeypatch, data)
    assert 'Сегодня: <b>7 дн.</b>' in text


# --- Приглашение и QR ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_invite_ru(theme_on, monkeypatch):
    text, markup = await show_invite(monkeypatch)
    assert text == (
        f'<b>{_tag("check")} Приглашение готово</b>\n'
        'Нажмите на текст ниже, чтобы скопировать, и отправьте другу.\n'
        '<blockquote>Присоединяйся к ZanityVPN — быстрому VPN для телефона, компьютера и ТВ.\n\n'
        'Бонус тебе: 100 ₽ на баланс при первом пополнении от 100 ₽.\n\n'
        'YouTube без рекламы, звонки в WhatsApp и Telegram, серверы в разных странах.\n\n'
        'Переходи по ссылке:\n<code>https://t.me/zanity_bot?start=refABC</code>\n\n'
        '🌐 <code>https://cab.example/?ref=ABC&amp;u=1</code></blockquote>'
    )
    assert _labels(markup) == [['⬅️ Назад']]
    assert not caption_exceeds_telegram_limit(text)


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['ru', 'en'])
async def test_theme_on_invite_for_friends_has_no_animated_emoji(theme_on, monkeypatch, language):
    """Приглашение уходит другим людям и экранируется целиком — `<tg-emoji>` в нём превратился бы в видимый код."""
    text, _ = await show_invite(monkeypatch, language)
    quote = text[text.index('<blockquote>') :]
    assert 'tg-emoji' not in quote
    assert '&lt;' not in quote.replace('&lt;/', '')  # ни одного экранированного тега
    assert '{e:' not in quote


@pytest.mark.parametrize('language', ['ru', 'en'])
def test_fork_invite_keys_carry_no_emoji_markers(language):
    import json
    from pathlib import Path

    fork = json.loads((Path(ref.__file__).parents[1] / 'localization' / 'fork' / f'{language}.json').read_text('utf-8'))
    for key in ('REFERRAL_INVITE_TEXT', 'REFERRAL_INVITE_BONUS', 'REFERRAL_INVITE_BONUS_LEVELS'):
        assert '{e:' not in fork.get(key, ''), key
    assert '{bonus_block}' in fork['REFERRAL_INVITE_TEXT'] and '{link}' in fork['REFERRAL_INVITE_TEXT']


@pytest.mark.asyncio
async def test_theme_on_invite_without_bonus_and_cabinet(theme_on, monkeypatch):
    monkeypatch.setattr(settings, 'REFERRAL_FIRST_TOPUP_BONUS_KOPEKS', 0)
    monkeypatch.setattr(type(settings), 'get_cabinet_referral_link', lambda self, code: None)
    text, _ = await show_invite(monkeypatch)
    assert 'Бонус тебе' not in text and '🌐' not in text
    assert text.endswith('<code>https://t.me/zanity_bot?start=refABC</code></blockquote>')


@pytest.mark.asyncio
async def test_theme_on_invite_en(theme_on, monkeypatch):
    text, _ = await show_invite(monkeypatch, 'en')
    assert not CYRILLIC_RE.search(text.replace('₽', ''))
    assert 'Join ZanityVPN' in text


@pytest.mark.asyncio
async def test_theme_on_qr_caption_ru(theme_on, monkeypatch, tmp_path):
    caption, markup = await show_qr(monkeypatch, tmp_path)
    assert caption == (
        f'<b>{_tag("link")} QR-код приглашения</b>\n'
        'Покажите его другу — откроется бот. Или отправьте ссылку:\n'
        '<code>https://t.me/zanity_bot?start=refABC</code>\n\n'
        'Ссылка на кабинет:\n<code>https://cab.example/?ref=ABC&amp;u=1</code>'
    )
    assert not caption_exceeds_telegram_limit(caption)
    assert _labels(markup) == [['⬅️ Назад']]


@pytest.mark.asyncio
async def test_theme_on_qr_caption_en(theme_on, monkeypatch, tmp_path):
    caption, _ = await show_qr(monkeypatch, tmp_path, 'en')
    assert not CYRILLIC_RE.search(caption)


# --- Уведомления --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_on_registration_notifications_ru(theme_on, referral_world):
    welcome, new_referral = await register(referral_world)
    assert welcome == (
        200,
        (
            f'<b>{_tag("wave")} Добро пожаловать!</b>\n'
            'Вы пришли по приглашению: <b>Андрей</b>.\n'
            'При первом пополнении от <b>100 ₽</b> вы получите бонус <b>100 ₽</b>.'
        ),
    )
    assert new_referral == (
        100,
        (
            f'<b>{_tag("people")} Новый реферал</b>\n'
            'Приглашённый: <b>Иван &lt;b&gt;</b>.\n'
            'Когда друг пополнит баланс от <b>100 ₽</b>, вы получите <b>100 ₽</b> + <b>25%</b> от суммы пополнения.'
        ),
    )
    assert referral_world.send.await_args_list[1].kwargs['notification_type'].value == 'referral_registered'


@pytest.mark.asyncio
async def test_theme_on_first_topup_notifications_ru(theme_on, referral_world):
    bonus, reward = await first_topup(referral_world)
    assert bonus == (
        200,
        f'<b>{_tag("gift")} Бонус получен</b>\nЗа первое пополнение мы добавили <b>100 ₽</b> на баланс.',
    )
    assert reward == (
        100,
        (
            f'<b>{_tag("coin")} Реферальная награда</b>\n'
            '<b>Иван &lt;b&gt;</b>: первое пополнение на 500 ₽.\n'
            'Вам начислено <b>225 ₽</b> (бонус 100 ₽ + комиссия 25% — 125 ₽).\n'
            'С каждого следующего пополнения друга вы будете получать <b>25%</b>.'
        ),
    )


@pytest.mark.asyncio
async def test_theme_on_commission_notifications_ru(theme_on, referral_world):
    expected = (
        f'<b>{_tag("coin")} Реферальная комиссия</b>\n'
        '<b>Иван &lt;b&gt;</b>: пополнение на {amount}.\n'
        'Вам на баланс — <b>{fee}</b> (25%).'
    )
    assert await next_topup(referral_world) == [(100, expected.format(amount='300 ₽', fee='75 ₽'))]
    referral_world.send.reset_mock()
    # Пополнение меньше минимума для первого бонуса: комиссия приходит тем же текстом.
    assert await first_topup(referral_world, 5000) == [(100, expected.format(amount='50 ₽', fee='12.5 ₽'))]


@pytest.mark.asyncio
async def test_theme_on_reward_without_fixed_bonus_does_not_repeat_itself(theme_on, referral_world, monkeypatch):
    monkeypatch.setattr(settings, 'REFERRAL_INVITER_BONUS_KOPEKS', 0)
    _, reward = await first_topup(referral_world)
    assert 'Вам начислено <b>125 ₽</b>.\n' in reward[1]
    assert '(' not in _plain(reward[1].split('\n')[2])


@pytest.mark.asyncio
async def test_theme_on_registration_without_bonuses(theme_on, referral_world, monkeypatch):
    monkeypatch.setattr(settings, 'REFERRAL_FIRST_TOPUP_BONUS_KOPEKS', 0)
    monkeypatch.setattr(settings, 'REFERRAL_INVITER_BONUS_KOPEKS', 0)
    welcome, new_referral = await register(referral_world)
    assert welcome[1] == f'<b>{_tag("wave")} Добро пожаловать!</b>\nВы пришли по приглашению: <b>Андрей</b>.'
    assert 'вы получите <b>25%</b> от суммы пополнения' in new_referral[1]
    assert '+' not in new_referral[1]


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['register', 'first', 'next'])
async def test_theme_on_notifications_en_have_no_cyrillic(theme_on, referral_world, scenario):
    runner = {'register': register, 'first': first_topup, 'next': next_topup}[scenario]
    messages = await runner(referral_world, language='en')
    assert messages
    for _, text in messages:
        assert not CYRILLIC_RE.search(text.replace('₽', '')), text
        assert text.startswith('<b><tg-emoji')
        assert text.count('<tg-emoji') == 1
        assert not caption_exceeds_telegram_limit(text)


@pytest.mark.asyncio
async def test_theme_on_notifications_go_in_the_language_of_the_recipient(theme_on, referral_world):
    referral_world.make_users('ru')
    referral_world.users[10].language = 'en'
    await ref_service.process_referral_registration(referral_world.db, 20, 10, bot=MagicMock())
    (welcome_to, welcome), (new_to, new_referral) = _sent_messages(referral_world)
    assert welcome_to == 200 and 'Добро пожаловать' in welcome
    assert new_to == 100 and 'New referral' in new_referral and 'Приглашённый' not in new_referral


# --- Сбой текста под темой не мешает начислению ------------------------------------------

_FORK_BUILDERS = (
    '_fork_welcome_notification',
    '_fork_new_referral_notification',
    '_fork_first_topup_bonus_notification',
    '_fork_first_topup_reward_notification',
    '_fork_commission_notification',
)


async def _all_scenarios(world) -> list[tuple[int, str]]:
    messages = await register(world)
    world.send.reset_mock()
    messages += await first_topup(world)
    world.send.reset_mock()
    messages += await next_topup(world)
    world.send.reset_mock()
    return messages


@pytest.mark.asyncio
async def test_broken_fork_text_falls_back_to_upstream_and_still_credits(theme_on, referral_world, monkeypatch):
    def broken(*args, **kwargs):
        raise KeyError('placeholder')

    for name in _FORK_BUILDERS:
        monkeypatch.setattr(ref_service, name, broken)
    themed = await _all_scenarios(referral_world)

    credited = [call.args[1].id for call in ref_service.add_user_balance.await_args_list]
    # Первое пополнение: бонус другу (20) и награда пригласившему (10); следующее — комиссия пригласившему.
    assert credited == [20, 10, 10]
    reasons = [call.kwargs['reason'] for call in ref_service.create_referral_earning.await_args_list]
    assert reasons[-2:] == ['referral_first_topup', 'referral_commission_topup']

    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    assert themed == await _all_scenarios(referral_world)
