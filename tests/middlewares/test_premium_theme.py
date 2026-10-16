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
        text=f'{ROCKET} Купить', callback_data='back_to_menu', style='danger', icon_custom_emoji_id='111'
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
