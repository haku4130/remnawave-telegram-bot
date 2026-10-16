"""Поддержка и обращения — под темой форка.

Без темы каждый экран обязан остаться байт-в-байт как у апстрима: ожидаемые строки ниже сняты с кода
до переписывания, тема выключена. Под темой тексты собирают FORK_*-ключи и переопределённые ключи
апстрима (app/localization/fork/), клавиатуры — те же функции, что у бота; обработчики настоящие, с
заглушками на всё, что ходит в базу и в Telegram. Текст поддержки из базы (SUPPORT_INFO_TEXT_*) не трогается.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.handlers.admin.tickets as admin_tickets
import app.handlers.support as support_handler
import app.handlers.tickets as handler
from app.config import settings
from app.keyboards.inline import get_support_keyboard, get_ticket_view_keyboard
from app.localization import loader
from app.localization.texts import _DYNAMIC_LANGUAGE_CONFIGS, get_texts
from app.middlewares.premium_theme import theme_markup
from app.services.support_settings_service import SupportSettingsService
from app.utils.message_patch import caption_exceeds_telegram_limit
from app.utils.timezone import format_local_datetime


TAG_RE = re.compile(r'<[^>]+>')
CYRILLIC_RE = re.compile('[а-яА-ЯёЁ]')
CREATED = datetime(2026, 10, 16, 12, 5, tzinfo=UTC)
REPLIED = datetime(2026, 10, 16, 13, 20, tzinfo=UTC)


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
def support_config(monkeypatch):
    monkeypatch.setattr(settings, 'SUPPORT_SYSTEM_MODE', 'both')
    monkeypatch.setattr(settings, 'SUPPORT_USERNAME', '@support')
    monkeypatch.setattr(settings, 'SUPPORT_INFO_TEXT_RU', '')
    monkeypatch.setattr(settings, 'SUPPORT_INFO_TEXT_EN', '')


@pytest.fixture
def theme_on(monkeypatch, support_config):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    loader.clear_locale_cache()
    yield
    loader.clear_locale_cache()


@pytest.fixture
def theme_off(monkeypatch, support_config):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    yield
    loader.clear_locale_cache()


def _user(language: str = 'ru') -> SimpleNamespace:
    return SimpleNamespace(id=1, language=language, telegram_id=42)


def _message(text: str, *, mine: bool, created=CREATED, photo: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        created_at=created,
        message_text=text,
        is_user_message=mine,
        has_media=photo,
        media_type='photo' if photo else None,
    )


def _ticket(status: str = 'open', *, messages=None, title: str = 'Не подключается VPN', **extra) -> SimpleNamespace:
    emoji = {'open': '🔴', 'answered': '🟡', 'closed': '🟢', 'pending': '⏳'}[status]
    data = {
        'id': 17,
        'user_id': 1,
        'title': title,
        'status': status,
        'status_emoji': emoji,
        'is_closed': status == 'closed',
        'is_user_reply_blocked': False,
        'created_at': CREATED,
        'messages': messages
        if messages is not None
        else [
            _message('Не открывается сайт', mine=True),
            _message('Попробуйте <config> заново', mine=False, created=REPLIED),
        ],
    }
    data.update(extra)
    return SimpleNamespace(**data)


def _callback(data: str = '') -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.answer = AsyncMock()
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.message.delete = AsyncMock()
    return callback


def _state() -> MagicMock:
    state = MagicMock()
    state.clear = AsyncMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    return state


def _capture_photo(monkeypatch) -> dict:
    sent: dict = {}

    async def fake(callback, caption, keyboard, parse_mode='HTML', **kwargs):
        sent['text'], sent['markup'] = caption, keyboard

    monkeypatch.setattr(handler, 'edit_or_answer_photo', fake)
    return sent


async def show_support(monkeypatch, language: str = 'ru'):
    sent: dict = {}

    async def fake(callback, caption, keyboard, parse_mode='HTML', **kwargs):
        sent['text'], sent['markup'] = caption, keyboard

    monkeypatch.setattr(support_handler, 'edit_or_answer_photo', fake)
    await support_handler.show_support_info(_callback('menu_support'), _user(language))
    return sent['text'], sent['markup']


async def start_ticket(language: str = 'ru'):
    callback = _callback('create_ticket')
    with (
        patch.object(handler.TicketCRUD, 'is_user_globally_blocked', AsyncMock(return_value=None), create=True),
        patch.object(handler.TicketCRUD, 'user_has_active_ticket', AsyncMock(return_value=False)),
    ):
        await handler.show_ticket_priority_selection(callback, _state(), _user(language), MagicMock())
    call = callback.message.edit_text.await_args
    return call.args[0], call.kwargs['reply_markup']


async def open_ticket(ticket, language: str = 'ru', data: str = 'view_ticket_17'):
    callback = _callback(data)
    with (
        patch.object(handler.TicketCRUD, 'get_ticket_by_id', AsyncMock(return_value=ticket)),
        patch.object(handler, 'safe_edit_or_resend', AsyncMock()) as send,
    ):
        await handler.view_ticket(callback, _user(language), MagicMock())
    return send.await_args.args[1], send.await_args.args[2]


async def show_list(monkeypatch, open_tickets, closed_count: int, language: str = 'ru'):
    sent = _capture_photo(monkeypatch)
    callback = _callback('my_tickets')

    async def count(db, user_id, statuses):
        return closed_count if statuses == ['closed'] else len(open_tickets)

    with (
        patch.object(handler.TicketCRUD, 'count_user_tickets_by_statuses', AsyncMock(side_effect=count)),
        patch.object(handler.TicketCRUD, 'get_user_tickets_by_statuses', AsyncMock(return_value=open_tickets)),
    ):
        await handler.show_my_tickets(callback, _user(language), MagicMock())
    if 'text' in sent:
        return sent['text'], sent['markup']
    call = callback.message.edit_text.await_args
    return call.args[0], call.kwargs['reply_markup']


async def show_closed(monkeypatch, tickets, language: str = 'ru'):
    sent = _capture_photo(monkeypatch)
    callback = _callback('my_tickets_closed')

    with (
        patch.object(handler.TicketCRUD, 'count_user_tickets_by_statuses', AsyncMock(return_value=len(tickets))),
        patch.object(handler.TicketCRUD, 'get_user_tickets_by_statuses', AsyncMock(return_value=tickets)),
    ):
        await handler.show_my_tickets_closed(callback, _user(language), MagicMock())
    if 'text' in sent:
        return sent['text'], sent['markup']
    call = callback.message.edit_text.await_args
    return call.args[0], call.kwargs['reply_markup']


# --- Без темы: как у апстрима -------------------------------------------------------------


def test_theme_off_support_info_is_the_dynamic_upstream_text(theme_off):
    for language in ('ru', 'en'):
        expected = _DYNAMIC_LANGUAGE_CONFIGS[language]['support_info'].format(
            support_username=settings.SUPPORT_USERNAME
        )
        assert expected == get_texts(language).SUPPORT_INFO
        assert SupportSettingsService.get_support_info_text(language) == expected


@pytest.mark.asyncio
async def test_theme_off_support_screen_matches_upstream(theme_off, monkeypatch):
    text, markup = await show_support(monkeypatch)
    assert text == _DYNAMIC_LANGUAGE_CONFIGS['ru']['support_info'].format(support_username='@support')
    assert _labels(markup) == [['🎫 Создать тикет'], ['📋 Мои тикеты'], ['💬 Связаться с поддержкой'], ['⬅️ Назад']]
    assert all(button.style is None for button in _buttons(markup))


@pytest.mark.asyncio
async def test_theme_off_ticket_prompt_matches_upstream(theme_off):
    text, markup = await start_ticket()
    assert text == 'Введите заголовок тикета:'
    assert _labels(markup) == [['❌ Отменить создание тикета']]


def test_theme_off_created_confirmation_matches_upstream(theme_off):
    texts = get_texts('ru')
    ticket = _ticket()
    text, markup = handler._ticket_created_message(texts, ticket, 'Не <b>работает</b>', 'photo')
    assert text == (
        '✅ <b>Тикет #17 создан</b>\n\n'
        '📝 Заголовок: Не &lt;b&gt;работает&lt;/b&gt;\n'
        '📊 Статус: 🔴 Открыт\n'
        f'📅 Создан: {format_local_datetime(CREATED, "%d.%m.%Y %H:%M")}\n'
        '📎 Вложение: фото\n'
    )
    assert _labels(markup) == [['👁️ Посмотреть тикет'], ['🏠 В главное меню']]
    assert handler._ticket_created_message(texts, ticket, 'Тема', None)[0].count('📎') == 0


@pytest.mark.asyncio
async def test_theme_off_view_ticket_matches_upstream(theme_off):
    text, markup = await open_ticket(_ticket('answered'))
    when_user = format_local_datetime(CREATED, '%d.%m %H:%M')
    when_support = format_local_datetime(REPLIED, '%d.%m %H:%M')
    assert text == (
        '🎫 Тикет #17\n\n'
        '📝 Заголовок: Не подключается VPN\n'
        '📊 Статус: 🟡 Отвечен\n'
        f'📅 Создан: {format_local_datetime(CREATED, "%d.%m.%Y %H:%M")}\n\n'
        '💬 Сообщения (2):\n\n'
        f'👤 Вы ({when_user}):\nНе открывается сайт\n\n'
        f'🛠️ Поддержка ({when_support}):\nПопробуйте &lt;config&gt; заново\n\n'
    )
    assert _labels(markup) == [['💬 Ответить'], ['🔒 Закрыть тикет'], ['⬅️ Назад']]
    assert all(button.style is None for button in _buttons(markup))


@pytest.mark.asyncio
async def test_theme_off_lists_match_upstream(theme_off, monkeypatch):
    text, markup = await show_list(monkeypatch, [], 0)
    assert text == 'У вас пока нет тикетов.'
    assert _labels(markup) == [['🎫 Создать тикет'], ['🟢 Закрытые тикеты'], ['⬅️ Назад']]

    text, markup = await show_list(monkeypatch, [_ticket()], 0)
    assert text == '📋 Ваши тикеты:'
    assert _labels(markup) == [['🟢 Закрытые тикеты'], ['🔴 #17 Не подключается VPN'], ['⬅️ Назад']]

    text, markup = await show_closed(monkeypatch, [_ticket('closed')])
    assert text == '🟢 Закрытые тикеты:'
    assert _labels(markup)[0] == ['🔴 Открытые тикеты']


def test_theme_off_view_keyboard_and_notification_match_upstream(theme_off):
    markup = get_ticket_view_keyboard(17, False, 'ru')
    assert _labels(markup) == [['💬 Ответить'], ['🔒 Закрыть тикет'], ['⬅️ Назад']]
    assert _labels(get_ticket_view_keyboard(17, True, 'ru')) == [['⬅️ Назад']]

    text, markup = handler.build_user_ticket_reply_notification(get_texts('ru'), 17, 'Ответ <b>')
    assert text == ('🎫 Получен ответ по тикету #17\n\nОтвет &lt;b&gt;\n\nНажмите кнопку ниже, чтобы перейти к тикету:')
    assert _labels(markup) == [['👁️ Посмотреть тикет'], ['❌ Закрыть уведомление']]
    assert all(button.style is None for button in _buttons(markup))


# --- Поддержка ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_support_screen_ru(theme_on, monkeypatch):
    text, markup = await show_support(monkeypatch)
    assert text == (f'<b>{_tag("speech")} Поддержка</b>\nНапишите нам — ответим в течение пары часов.')
    assert '{' not in text
    assert text.count('<tg-emoji') == 1
    assert 'RemnaWave' not in text
    assert not caption_exceeds_telegram_limit(text)


@pytest.mark.asyncio
async def test_support_screen_en(theme_on, monkeypatch):
    text, markup = await show_support(monkeypatch, 'en')
    assert text.startswith(f'<b>{_tag("speech")} Support</b>\n')
    assert not CYRILLIC_RE.search(_plain(text))
    assert 'RemnaWave' not in text
    assert not any(CYRILLIC_RE.search(button.text) for button in _themed(markup))


def test_support_info_from_database_text_is_never_touched(theme_on, monkeypatch):
    monkeypatch.setattr(settings, 'SUPPORT_INFO_TEXT_RU', '<b>Наш текст</b> {x}')
    assert SupportSettingsService.get_support_info_text('ru') == '<b>Наш текст</b> {x}'


@pytest.mark.parametrize(
    ('mode', 'green'),
    [('both', 'create_ticket'), ('tickets', 'create_ticket'), ('contact', 'https://t.me/support')],
)
def test_support_keyboard_has_one_green_button(theme_on, monkeypatch, mode, green):
    monkeypatch.setattr(settings, 'SUPPORT_SYSTEM_MODE', mode)
    markup = get_support_keyboard('ru')
    assert _greens(markup) == [green]
    assert markup.inline_keyboard[-1][0].callback_data == 'back_to_menu'
    assert theme_markup(markup).inline_keyboard[-1][0].style is None


def test_support_keyboard_labels(theme_on):
    markup = get_support_keyboard('ru')
    assert _labels(markup) == [
        [f'{_tag("ticket")} Создать обращение'],
        [f'{_tag("inbox")} Мои обращения', f'{_tag("speech")} Написать напрямую'],
        ['⬅️ Назад'],
    ]
    themed = _themed(markup)
    assert [button.text for button in themed] == ['Создать обращение', 'Мои обращения', 'Написать напрямую', '⬅️ Назад']
    assert all(button.icon_custom_emoji_id for button in themed[:3])
    assert themed[-1].icon_custom_emoji_id is None


# --- Создание обращения -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ticket_title_prompt(theme_on):
    text, markup = await start_ticket()
    assert text.startswith(f'<b>{_tag("memo")} Новое обращение</b>\n')
    assert 'тикет' not in text.lower()
    assert _labels(markup) == [[f'{_tag("cross")} Отменить']]
    assert [button.style for button in _themed(markup)] == ['danger']


@pytest.mark.asyncio
async def test_ticket_title_prompt_en(theme_on):
    text, markup = await start_ticket('en')
    assert text.startswith(f'<b>{_tag("memo")} New request</b>\n')
    assert not CYRILLIC_RE.search(_plain(text))
    assert not any(CYRILLIC_RE.search(button.text) for button in _buttons(markup))


@pytest.mark.parametrize('language', ['ru', 'en'])
def test_input_hints_format_with_real_kwargs(theme_on, language):
    texts = get_texts(language)
    limit = handler.TICKET_MESSAGE_MAX_LENGTH
    for key in ('TICKET_MESSAGE_INPUT', 'TICKET_MESSAGE_TOO_LONG'):
        text = texts.t(key, '').format(limit=limit)
        assert str(limit) in text
        assert text.startswith('<b>'), key
        assert '{' not in text, key
        assert not caption_exceeds_telegram_limit(text)
    for key in ('TICKET_TITLE_TOO_SHORT', 'TICKET_TITLE_TOO_LONG', 'TICKET_MESSAGE_TOO_SHORT', 'TICKET_CREATE_ERROR'):
        text = texts.t(key, '')
        assert text.startswith('<b>'), key
        assert '{' not in text, key
    if language == 'en':
        for key in ('TICKET_MESSAGE_INPUT', 'TICKET_TITLE_TOO_SHORT', 'TICKET_CREATE_ERROR'):
            assert not CYRILLIC_RE.search(_plain(texts.t(key, ''))), key


@pytest.mark.parametrize('language', ['ru', 'en'])
def test_created_confirmation(theme_on, language):
    texts = get_texts(language)
    text, markup = handler._ticket_created_message(texts, _ticket(), 'Not <b>working</b>', 'photo')
    assert text.startswith(f'<b>{_tag("check")} ')
    assert '#17' in text
    assert 'Not &lt;b&gt;working&lt;/b&gt;' in text
    assert text.count('<tg-emoji') == 1
    assert '{' not in text
    assert not caption_exceeds_telegram_limit(text)
    no_photo = handler._ticket_created_message(texts, _ticket(), 'Тема', None)[0]
    assert len(no_photo) < len(text)
    assert [button.callback_data for button in _buttons(markup)] == ['view_ticket_17', 'back_to_menu']
    assert _greens(markup) == ['view_ticket_17']
    if language == 'en':
        assert not CYRILLIC_RE.search(_plain(text))
        assert not any(CYRILLIC_RE.search(button.text) for button in _themed(markup))


def test_created_confirmation_long_title_stays_short(theme_on):
    text, _ = handler._ticket_created_message(get_texts('ru'), _ticket(), 'я' * 200, 'photo')
    assert not caption_exceeds_telegram_limit(text)


# --- Списки -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_list_offers_only_creation(theme_on, monkeypatch):
    text, markup = await show_list(monkeypatch, [], 0)
    assert text.startswith(f'<b>{_tag("inbox")} ')
    assert [button.callback_data for button in _buttons(markup)] == ['create_ticket', 'menu_support']
    assert _greens(markup) == ['create_ticket']


@pytest.mark.asyncio
async def test_open_list_moves_closed_button_down_and_hides_it_when_empty(theme_on, monkeypatch):
    text, markup = await show_list(monkeypatch, [_ticket()], 2)
    assert text.startswith(f'<b>{_tag("inbox")} Ваши обращения</b>')
    assert [button.callback_data for button in _buttons(markup)] == [
        'view_ticket_17',
        'my_tickets_closed',
        'menu_support',
    ]
    assert _greens(markup) == []

    _, markup = await show_list(monkeypatch, [_ticket()], 0)
    assert [button.callback_data for button in _buttons(markup)] == ['view_ticket_17', 'menu_support']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('language', 'caption'),
    [
        ('ru', '<b>{inbox} Ваши обращения</b>\nОткрытых обращений нет.'),
        ('en', '<b>{inbox} Your requests</b>\nNo open requests.'),
    ],
)
async def test_only_closed_tickets_offer_creation_first(theme_on, monkeypatch, language, caption):
    text, markup = await show_list(monkeypatch, [], 2, language)
    assert text == caption.format(inbox=_tag('inbox'))
    assert [button.callback_data for button in _buttons(markup)] == [
        'create_ticket',
        'my_tickets_closed',
        'menu_support',
    ]
    assert _greens(markup) == ['create_ticket']
    # Та же подпись, что у «Создать обращение» на экране поддержки.
    assert markup.inline_keyboard[0][0].text == get_texts(language).t('CREATE_TICKET_BUTTON')


@pytest.mark.asyncio
async def test_only_closed_tickets_without_a_fork_locale_keep_the_upstream_caption(theme_on, monkeypatch):
    text, markup = await show_list(monkeypatch, [], 2, 'tr')
    assert text == get_texts('tr').t('MY_TICKETS_TITLE')
    assert text
    assert [button.callback_data for button in _buttons(markup)] == [
        'create_ticket',
        'my_tickets_closed',
        'menu_support',
    ]


@pytest.mark.asyncio
async def test_theme_off_only_closed_tickets_match_upstream(theme_off, monkeypatch):
    text, markup = await show_list(monkeypatch, [], 2)
    assert text == '📋 Ваши тикеты:'
    assert _labels(markup) == [['🟢 Закрытые тикеты'], ['⬅️ Назад']]


@pytest.mark.asyncio
async def test_closed_list(theme_on, monkeypatch):
    text, markup = await show_closed(monkeypatch, [_ticket('closed')])
    assert text.startswith(f'<b>{_tag("check")} Закрытые обращения</b>')
    assert [button.callback_data for button in _buttons(markup)] == ['view_ticket_17', 'my_tickets', 'menu_support']

    text, markup = await show_closed(monkeypatch, [])
    assert text.startswith('<b>')
    assert 'Закрытых' in text


@pytest.mark.asyncio
async def test_lists_in_english(theme_on, monkeypatch):
    for text, markup in (
        await show_list(monkeypatch, [], 0, 'en'),
        await show_list(monkeypatch, [_ticket()], 1, 'en'),
        await show_closed(monkeypatch, [_ticket('closed')], 'en'),
        await show_closed(monkeypatch, [], 'en'),
    ):
        assert not CYRILLIC_RE.search(_plain(text))
        assert not any(CYRILLIC_RE.search(button.text) for button in _themed(markup) if '#' not in button.text)


# --- Карточка обращения -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_view_ticket(theme_on):
    text, markup = await open_ticket(_ticket('answered'))
    when_user = format_local_datetime(CREATED, '%d.%m.%Y %H:%M')
    when_support = format_local_datetime(REPLIED, '%d.%m.%Y %H:%M')
    assert text == (
        f'<b>{_tag("ticket")} Обращение #17</b>\n'
        '<b>Не подключается VPN</b>\n'
        f'Есть ответ · {format_local_datetime(CREATED, "%d.%m.%Y")}\n\n'
        f'<b>Вы</b> · {when_user}\nНе открывается сайт\n\n'
        f'<b>Поддержка</b> · {when_support}\nПопробуйте &lt;config&gt; заново\n\n'
    )
    assert [button.callback_data for button in _buttons(markup)] == ['reply_ticket_17', 'close_ticket_17', 'my_tickets']
    assert _greens(markup) == ['reply_ticket_17']
    assert text.count('<tg-emoji') == 1


@pytest.mark.asyncio
async def test_view_ticket_escapes_user_content_and_marks_photos(theme_on):
    ticket = _ticket(
        title='<i>тема</i>',
        messages=[_message('<script>', mine=True, photo=True)],
    )
    text, _ = await open_ticket(ticket)
    assert '<b>&lt;i&gt;тема&lt;/i&gt;</b>' in text
    assert '&lt;script&gt;' in text and '<script>' not in text
    assert 'Приложено фото' in text


@pytest.mark.asyncio
async def test_view_closed_ticket_has_no_reply_button(theme_on):
    text, markup = await open_ticket(_ticket('closed'))
    assert 'Закрыто' in text
    assert [button.callback_data for button in _buttons(markup)] == ['my_tickets']
    assert _greens(markup) == []


@pytest.mark.asyncio
async def test_view_ticket_statuses_in_both_languages(theme_on):
    for language in ('ru', 'en'):
        statuses = set()
        for status in ('open', 'answered', 'closed', 'pending'):
            messages = [_message('Site does not open', mine=True), _message('Try again', mine=False, created=REPLIED)]
            text, _ = await open_ticket(_ticket(status, messages=messages, title='VPN'), language)
            statuses.add(text.split('\n')[2].split(' · ')[0])
            if language == 'en':
                assert not CYRILLIC_RE.search(_plain(text))
        assert len(statuses) == 4


@pytest.mark.asyncio
async def test_view_ticket_pagination_keeps_working(theme_on):
    ticket = _ticket(messages=[_message('очень длинное сообщение ' * 40 + str(i), mine=i % 2 == 0) for i in range(40)])
    text, markup = await open_ticket(ticket)
    callbacks = [button.callback_data for button in _buttons(markup)]
    assert callbacks[0].startswith('ticket_view_page_17_')
    assert text.startswith(f'<b>{_tag("ticket")} Обращение #17</b>')
    first, _ = await open_ticket(ticket, data='ticket_view_page_17_1')
    assert first.startswith(f'<b>{_tag("ticket")} Обращение #17</b>')
    assert len(first) <= 4096


# --- Ответ --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_prompt(theme_on):
    callback = _callback('reply_ticket_17')
    await handler.reply_to_ticket(callback, _state(), _user())
    call = callback.message.edit_text.await_args
    assert call.args[0].startswith(f'<b>{_tag("speech")} Ваш ответ</b>')
    assert _labels(call.kwargs['reply_markup']) == [[f'{_tag("cross")} Отменить']]
    assert [button.style for button in _themed(call.kwargs['reply_markup'])] == ['danger']


def _reply_message(text: str) -> MagicMock:
    message = MagicMock()
    message.text, message.caption, message.photo = text, None, None
    message.answer = AsyncMock()
    message.chat.id, message.message_id = 42, 7
    message.bot = MagicMock()
    return message


async def send_reply(text: str, *, ticket=None, language: str = 'ru', state_data: dict | None = None):
    message = _reply_message(text)
    state = _state()
    state.get_state = AsyncMock(return_value=handler.TicketStates.waiting_for_reply)
    state.get_data = AsyncMock(return_value=state_data if state_data is not None else {'ticket_id': 17})
    with (
        patch.object(handler.RateLimitCache, 'is_rate_limited', AsyncMock(return_value=False)),
        patch.object(handler.TicketCRUD, 'get_ticket_by_id', AsyncMock(return_value=ticket or _ticket())),
        patch.object(handler.TicketMessageCRUD, 'add_message', AsyncMock()),
        patch.object(handler, 'notify_admins_about_ticket_reply', AsyncMock()),
        patch.object(handler, '_try_delete_message_later', AsyncMock()),
    ):
        await handler.handle_ticket_reply(message, state, _user(language), MagicMock())
    call = message.answer.await_args
    return call.args[0], call.kwargs.get('reply_markup')


@pytest.mark.asyncio
async def test_reply_sent(theme_on):
    text, markup = await send_reply('Спасибо, всё заработало')
    assert text.startswith(f'<b>{_tag("check")} Ответ отправлен</b>')
    assert [button.callback_data for button in _buttons(markup)] == ['view_ticket_17', 'back_to_menu']
    assert _greens(markup) == ['view_ticket_17']


@pytest.mark.asyncio
async def test_reply_errors_are_themed_and_format(theme_on):
    text, _ = await send_reply('abc')
    assert text.startswith('<b>') and '5' in text
    text, _ = await send_reply('я' * (handler.TICKET_MESSAGE_MAX_LENGTH + 1))
    assert str(handler.TICKET_MESSAGE_MAX_LENGTH) in text and text.startswith('<b>')
    text, _ = await send_reply('Достаточно длинный ответ', state_data={})
    assert text.startswith('<b>')


@pytest.mark.asyncio
async def test_reply_to_closed_ticket(theme_on):
    text, markup = await send_reply('Достаточно длинный ответ', ticket=_ticket('closed'))
    assert text.startswith(f'<b>{_tag("lock_key")} ')
    assert [button.callback_data for button in _buttons(markup)] == ['close_ticket_notification_17']
    assert _greens(markup) == []

    blocked = _ticket(is_user_reply_blocked=True)
    text, _ = await send_reply('Достаточно длинный ответ', ticket=blocked)
    assert text.startswith(f'<b>{_tag("lock_key")} ')


@pytest.mark.asyncio
async def test_reply_texts_in_english(theme_on):
    for text, markup in (
        await send_reply('Thanks, works now', language='en'),
        await send_reply('abc', language='en'),
        await send_reply('Long enough reply', ticket=_ticket('closed'), language='en'),
    ):
        assert not CYRILLIC_RE.search(_plain(text))
        if markup:
            assert not any(CYRILLIC_RE.search(button.text) for button in _themed(markup))


@pytest.mark.asyncio
async def test_cancels(theme_on):
    callback = _callback('cancel_ticket_reply')
    await handler.cancel_ticket_reply(callback, _state(), _user())
    assert callback.message.edit_text.await_args.args[0].startswith('<b>')
    callback = _callback('cancel_ticket_creation')
    await handler.cancel_ticket_creation(callback, _state(), _user())
    assert callback.message.edit_text.await_args.args[0].startswith('<b>')


# --- Уведомление об ответе поддержки ------------------------------------------------------


@pytest.mark.parametrize('language', ['ru', 'en'])
def test_reply_notification(theme_on, language):
    texts = get_texts(language)
    preview = 'Откройте <config> и нажмите "Обновить"' if language == 'ru' else 'Open <config> and press "Update"'
    text, markup = handler.build_user_ticket_reply_notification(texts, 17, preview)
    assert text.startswith(f'<b>{_tag("speech")} ')
    assert '#17' in text
    assert '&lt;config&gt;' in text and '<config>' not in text
    assert text.count('<tg-emoji') == 1
    assert '{' not in text
    assert [button.callback_data for button in _buttons(markup)] == ['view_ticket_17', 'close_ticket_notification_17']
    assert _greens(markup) == ['view_ticket_17']
    if language == 'en':
        assert not CYRILLIC_RE.search(_plain(text))
        assert not any(CYRILLIC_RE.search(button.text) for button in _themed(markup))


def test_reply_notification_with_long_reply_fits_photo_caption(theme_on):
    from app.utils.ticket_text import preview_text

    text, _ = handler.build_user_ticket_reply_notification(get_texts('ru'), 12345, preview_text('ы' * 3000))
    assert not caption_exceeds_telegram_limit(text)


@pytest.mark.asyncio
async def test_notify_user_sends_the_built_notification(theme_on):
    bot = MagicMock()
    bot.send_message = AsyncMock()
    ticket = SimpleNamespace(id=17, user=SimpleNamespace(telegram_id=42, language='ru'))
    db = MagicMock()
    with (
        patch.object(admin_tickets.TicketMessageCRUD, 'get_last_message', AsyncMock(return_value=None)),
        patch.object(admin_tickets.SupportSettingsService, 'get_user_ticket_notifications_enabled', lambda: True),
    ):
        await admin_tickets.notify_user_about_ticket_reply(bot, ticket, 'Ответ', db)
    call = bot.send_message.await_args
    assert call.kwargs['text'].startswith(f'<b>{_tag("speech")} ')
    assert _greens(call.kwargs['reply_markup']) == ['view_ticket_17']


# --- Общие ключи с админкой остаются апстримными ------------------------------------------


@pytest.mark.parametrize('language', ['ru', 'en'])
def test_keys_shared_with_admin_keep_upstream_values_under_theme(theme_on, language):
    # Админские экраны вне задачи: их ключи форк не переопределяет, свои тексты лежат в FORK_TICKET_*.
    fork = loader.fork_keys(language)
    shared = (
        'VIEW_TICKET',
        'CLOSE_NOTIFICATION',
        'CANCEL_REPLY',
        'REPLY_TO_TICKET',
        'CLOSE_TICKET',
        'TICKET_CLOSED',
        'TICKET_REPLY_TOO_SHORT',
        'TICKET_REPLY_TOO_LONG',
        'TICKET_REPLY_ERROR',
        'TICKET_REPLY_CANCELLED',
        'TICKET_STATUS_OPEN',
        'TICKET_ATTACHMENTS',
    )
    assert not fork.intersection(shared)


@pytest.mark.asyncio
async def test_title_input_moves_on_to_the_description_prompt(theme_on):
    message = _reply_message('Не подключается VPN')
    state = _state()
    state.get_state = AsyncMock(return_value=handler.TicketStates.waiting_for_title)
    state.get_data = AsyncMock(return_value={'prompt_chat_id': 42, 'prompt_message_id': 5})
    message.bot.edit_message_text = AsyncMock()
    with (
        patch.object(handler.TicketCRUD, 'is_user_globally_blocked', AsyncMock(return_value=None)),
        patch.object(handler, '_try_delete_message_later', AsyncMock()),
    ):
        await handler.handle_ticket_title_input(message, state, _user(), MagicMock())
    text = message.bot.edit_message_text.await_args.kwargs['text']
    assert text.startswith(f'<b>{_tag("memo")} Что случилось?</b>')
    assert str(handler.TICKET_MESSAGE_MAX_LENGTH) in text
