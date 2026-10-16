# tests/middlewares/test_premium_theme.py
"""Тема Premium на выходе: иконки и цвета кнопок, теги там, где HTML не работает."""

from unittest.mock import MagicMock

import pytest
from aiogram.client.default import DefaultBotProperties
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, SendPhoto
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MessageEntity,
    ReplyKeyboardMarkup,
)

from app.config import settings
from app.middlewares import premium_theme as mw


ROCKET = '<tg-emoji emoji-id="5445284980978621387">🚀</tg-emoji>'


@pytest.fixture(autouse=True)
def theme_on(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)


def _bot():
    bot = MagicMock()
    bot.default = DefaultBotProperties(parse_mode='HTML')
    return bot


async def _run(method):
    seen = {}

    async def make_request(_bot, sent):
        seen['method'] = sent
        return True

    await mw.PremiumThemeMiddleware()(make_request, _bot(), method)
    return seen['method']


def test_leading_tag_becomes_icon():
    out = mw.theme_button(InlineKeyboardButton(text=f'{ROCKET} Подключиться', callback_data='subscription_connect'))
    assert out.text == 'Подключиться'
    assert out.icon_custom_emoji_id == '5445284980978621387'
    assert out.style == 'success'


def test_leading_unicode_from_catalog_becomes_icon():
    out = mw.theme_button(InlineKeyboardButton(text='⚙️ Админ панель', callback_data='admin_panel'))
    assert out.text == 'Админ панель'
    assert out.icon_custom_emoji_id == '5341715473882955310'


def test_unicode_outside_catalog_is_left_alone():
    out = mw.theme_button(InlineKeyboardButton(text='🛟 Помощь', callback_data='menu_support'))
    assert out.text == '🛟 Помощь'
    assert out.icon_custom_emoji_id is None


def test_emoji_only_label_is_not_emptied():
    out = mw.theme_button(InlineKeyboardButton(text='🔄', callback_data='refresh_x'))
    assert out.text == '🔄'
    assert out.icon_custom_emoji_id is None
    tagged = mw.theme_button(InlineKeyboardButton(text=ROCKET, callback_data='x'))
    assert tagged.text == '🚀'
    assert tagged.icon_custom_emoji_id is None


def test_explicit_style_and_icon_win():
    button = InlineKeyboardButton(
        text=f'{ROCKET} Купить', callback_data='trial_activate', style='danger', icon_custom_emoji_id='111'
    )
    out = mw.theme_button(button)
    assert out.style == 'danger'
    assert out.icon_custom_emoji_id == '111'
    assert out.text == 'Купить'


def test_inner_tags_fall_back_to_unicode():
    out = mw.theme_button(InlineKeyboardButton(text=f'Купить {ROCKET} сейчас', callback_data='x'))
    assert out.text == 'Купить 🚀 сейчас'


def test_source_markup_is_not_mutated():
    button = InlineKeyboardButton(text=f'{ROCKET} Go', callback_data='trial_activate')
    markup = InlineKeyboardMarkup(inline_keyboard=[[button]])
    themed = mw.theme_markup(markup)
    assert markup.inline_keyboard[0][0].text == f'{ROCKET} Go'
    assert themed.inline_keyboard[0][0].text == 'Go'


@pytest.mark.asyncio
async def test_send_message_markup_themed_and_html_text_kept():
    method = SendMessage(
        chat_id=1,
        text=f'{ROCKET} привет',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text='🎁 Попробовать', callback_data='trial_activate')]]
        ),
    )
    sent = await _run(method)
    assert sent.text == f'{ROCKET} привет'
    assert sent.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id == '5199749070830197566'
    assert sent.reply_markup.inline_keyboard[0][0].style == 'success'


@pytest.mark.asyncio
async def test_plain_text_message_gets_fallback():
    sent = await _run(SendMessage(chat_id=1, text=f'{ROCKET} привет', parse_mode=None))
    assert sent.text == '🚀 привет'


@pytest.mark.asyncio
async def test_text_with_entities_is_left_untouched():
    text = f'{ROCKET} привет'
    method = SendMessage(
        chat_id=1, text=text, parse_mode=None, entities=[MessageEntity(type='bold', offset=0, length=3)]
    )
    sent = await _run(method)
    assert sent.text == text


@pytest.mark.asyncio
async def test_escaped_tag_is_restored_in_html():
    escaped = '&lt;tg-emoji emoji-id=&quot;5445284980978621387&quot;&gt;🚀&lt;/tg-emoji&gt; привет'
    sent = await _run(EditMessageText(chat_id=1, message_id=2, text=escaped))
    assert sent.text == f'{ROCKET} привет'


@pytest.mark.asyncio
async def test_caption_is_handled_like_text():
    sent = await _run(SendPhoto(chat_id=1, photo='x', caption=f'{ROCKET} фото', parse_mode=None))
    assert sent.caption == '🚀 фото'


@pytest.mark.asyncio
async def test_callback_alert_gets_fallback():
    sent = await _run(AnswerCallbackQuery(callback_query_id='1', text=f'{ROCKET} готово', show_alert=True))
    assert sent.text == '🚀 готово'


@pytest.mark.asyncio
async def test_reply_keyboard_is_not_touched():
    reply = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='🏠 Главное меню')]])
    sent = await _run(SendMessage(chat_id=1, text='x', reply_markup=reply))
    assert sent.reply_markup.keyboard[0][0].text == '🏠 Главное меню'
    assert sent.reply_markup.keyboard[0][0].icon_custom_emoji_id is None


@pytest.mark.asyncio
async def test_disabled_flag_passes_through(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    sent = await _run(SendMessage(chat_id=1, text=f'{ROCKET} x', parse_mode=None))
    assert sent.text == f'{ROCKET} x'


@pytest.mark.asyncio
async def test_theme_error_does_not_block_sending(monkeypatch):
    def boom(_markup):
        raise RuntimeError('boom')

    monkeypatch.setattr(mw, 'theme_markup', boom)
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='a', callback_data='b')]])
    sent = await _run(SendMessage(chat_id=1, text='x', reply_markup=markup))
    assert sent.reply_markup is markup


def test_registered_in_bot_factory():
    from app.bot_factory import create_bot

    bot = create_bot(token='123456:' + 'A' * 35)
    names = [type(m).__name__ for m in bot.session.middleware._middlewares]
    assert 'PremiumThemeMiddleware' in names


HOME = '<tg-emoji emoji-id="5312486108309757006">🏠</tg-emoji>'


def test_navigation_button_gets_no_icon_and_no_color():
    out = mw.theme_button(InlineKeyboardButton(text='🏠 В главное меню', callback_data='back_to_menu'))
    assert out.text == '🏠 В главное меню'
    assert out.icon_custom_emoji_id is None
    assert out.style is None


def test_navigation_button_with_tag_falls_back_to_plain():
    out = mw.theme_button(InlineKeyboardButton(text=f'{HOME} В главное меню', callback_data='back_to_menu'))
    assert out.text == '🏠 В главное меню'
    assert out.icon_custom_emoji_id is None
    assert out.style is None


@pytest.mark.asyncio
async def test_unknown_escaped_tag_stays_escaped():
    escaped = '&lt;tg-emoji emoji-id=&quot;1234567890123456789&quot;&gt;🚀&lt;/tg-emoji&gt; привет'
    sent = await _run(EditMessageText(chat_id=1, message_id=2, text=escaped))
    assert sent.text == escaped


def test_restore_escaped_only_known_ids():
    known = '&lt;tg-emoji emoji-id=&quot;5445284980978621387&quot;&gt;🚀&lt;/tg-emoji&gt;'
    assert mw.restore_escaped(known) == ROCKET


@pytest.mark.asyncio
async def test_group_chat_gets_color_but_no_icon():
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text='🎁 Попробовать', callback_data='trial_activate')]]
    )
    sent = await _run(SendMessage(chat_id=-1001234567890, text='x', reply_markup=markup))
    button = sent.reply_markup.inline_keyboard[0][0]
    assert button.icon_custom_emoji_id is None
    assert button.text == '🎁 Попробовать'
    assert button.style == 'success'


@pytest.mark.asyncio
async def test_inline_message_edit_gets_no_icon():
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text='🎁 Попробовать', callback_data='trial_activate')]]
    )
    sent = await _run(EditMessageText(inline_message_id='x', text='y', reply_markup=markup))
    assert sent.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id is None


def test_tag_label_without_icons_falls_back_to_plain():
    out = mw.theme_button(InlineKeyboardButton(text=f'{ROCKET} Go', callback_data='x'), allow_icons=False)
    assert out.text == '🚀 Go'
    assert out.icon_custom_emoji_id is None


def _bad_request(method, message='Bad Request: invalid custom emoji identifier'):
    from aiogram.exceptions import TelegramBadRequest

    return TelegramBadRequest(method=method, message=message)


async def _run_failing(method, failures):
    """make_request падает TelegramBadRequest первые `failures` раз; возвращает (результат|ошибка, вызовы)."""
    calls = []

    async def make_request(_bot, sent):
        calls.append(sent.model_copy(deep=True))
        if len(calls) <= failures:
            raise _bad_request(sent, f'Bad Request: attempt {len(calls)}')
        return 'ok'

    try:
        result = await mw.PremiumThemeMiddleware()(make_request, _bot(), method)
    except Exception as error:
        result = error
    return result, calls


@pytest.mark.asyncio
async def test_rejected_themed_request_is_retried_unthemed():
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=f'{ROCKET} Подключиться', callback_data='subscription_connect')]]
    )
    result, calls = await _run_failing(SendMessage(chat_id=1, text='x', reply_markup=markup), failures=1)
    assert result == 'ok'
    assert len(calls) == 2
    assert calls[0].reply_markup.inline_keyboard[0][0].icon_custom_emoji_id == '5445284980978621387'
    retried = calls[1].reply_markup.inline_keyboard[0][0]
    assert retried.icon_custom_emoji_id is None
    assert retried.text == f'{ROCKET} Подключиться'
    assert retried.style is None


@pytest.mark.asyncio
async def test_unthemed_request_error_is_not_retried():
    from aiogram.exceptions import TelegramBadRequest

    result, calls = await _run_failing(SendMessage(chat_id=1, text='plain'), failures=1)
    assert isinstance(result, TelegramBadRequest)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retry_failure_raises_retry_error():
    from aiogram.exceptions import TelegramBadRequest

    result, calls = await _run_failing(SendMessage(chat_id=1, text=f'{ROCKET} x', parse_mode=None), failures=2)
    assert isinstance(result, TelegramBadRequest)
    assert 'attempt 2' in result.message
    assert len(calls) == 2
    assert calls[1].text == f'{ROCKET} x'


@pytest.mark.asyncio
async def test_not_modified_error_is_not_retried_unthemed():
    from aiogram.exceptions import TelegramBadRequest

    calls = []

    async def make_request(_bot, sent):
        calls.append(sent)
        raise _bad_request(sent, 'Bad Request: message is not modified: specified new message content is the same')

    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=f'{ROCKET} Подключиться', callback_data='subscription_connect')]]
    )
    method = EditMessageText(chat_id=1, message_id=1, text='x', reply_markup=markup)
    with pytest.raises(TelegramBadRequest):
        await mw.PremiumThemeMiddleware()(make_request, _bot(), method)
    assert len(calls) == 1
