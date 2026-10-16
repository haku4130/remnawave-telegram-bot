# app/middlewares/premium_theme.py
"""Оформление исходящих запросов под Telegram Premium владельца бота.

Тексты форка несут <tg-emoji> (см. app/localization/fork/), но одни и те же
строки уходят в сообщения, подписи кнопок и всплывающие алерты, а HTML работает
только в первых. Переписывать сотни мест под каждый контекст — значит воевать
с апстримом на каждом синке, поэтому всё делается на выходе, в middleware сессии:

* инлайн-кнопки: ведущий <tg-emoji> или известный каталогу обычный эмодзи →
  icon_custom_emoji_id (анимированная иконка), прочие теги → обычный символ,
  цвет — по таблице button_theme, если код не задал свой;
* алерты и сообщения без HTML → теги заменяются обычным символом;
* HTML-сообщения → экранированный кем-то тег восстанавливается (с warning в лог).

Reply-клавиатуры не трогаем: текст reply-кнопки бот получает обратно сообщением,
и срезанный эмодзи сломал бы обработчики по тексту. Ошибка оформления не мешает
отправке — запрос уходит как есть.
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from aiogram import Bot
from aiogram.client.default import Default
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.methods import AnswerCallbackQuery, TelegramMethod
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.localization.emoji_catalog import by_unicode
from app.utils.button_theme import style_for_button
from app.utils.miniapp_buttons import strip_leading_emoji


logger = structlog.get_logger(__name__)

_TG_EMOJI_RE = re.compile(r'<tg-emoji emoji-id="(\d+)">(.*?)</tg-emoji>', re.DOTALL)
_LEADING_TG_EMOJI_RE = re.compile(r'^\s*<tg-emoji emoji-id="(\d+)">.*?</tg-emoji>\s*', re.DOTALL)
_ESCAPED_TG_EMOJI_RE = re.compile(
    r'&lt;tg-emoji emoji-id=(?:&quot;|")(\d+)(?:&quot;|")&gt;(.*?)&lt;/tg-emoji&gt;', re.DOTALL
)
# Поле текста и поле его сущностей: при готовых сущностях текст менять нельзя —
# их смещения посчитаны по исходной строке.
_TEXT_FIELDS = (('text', 'entities'), ('caption', 'caption_entities'))


def to_plain(value: str) -> str:
    return _TG_EMOJI_RE.sub(lambda match: match.group(2), value)


def restore_escaped(value: str) -> str:
    return _ESCAPED_TG_EMOJI_RE.sub(
        lambda match: f'<tg-emoji emoji-id="{match.group(1)}">{match.group(2)}</tg-emoji>', value
    )


def theme_button(button: InlineKeyboardButton) -> InlineKeyboardButton:
    text = button.text or ''
    update: dict[str, Any] = {}

    leading_tag = _LEADING_TG_EMOJI_RE.match(text)
    if leading_tag and text[leading_tag.end() :].strip():
        if not button.icon_custom_emoji_id:
            update['icon_custom_emoji_id'] = leading_tag.group(1)
        text = text[leading_tag.end() :]
    elif not leading_tag and not button.icon_custom_emoji_id:
        rest = strip_leading_emoji(text)
        leading = text[: len(text) - len(rest)].strip()
        entry = by_unicode(leading) if leading else None
        if entry is not None and rest.strip():
            update['icon_custom_emoji_id'] = entry.emoji_id
            text = rest

    if '<tg-emoji' in text:
        text = to_plain(text)
    if text != button.text:
        update['text'] = text
    if button.style is None:
        style = style_for_button(button)
        if style:
            update['style'] = style
    return button.model_copy(update=update) if update else button


def theme_markup(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[theme_button(button) for button in row] for row in markup.inline_keyboard]
    )


def _is_html(bot: Bot, parse_mode: Any) -> bool:
    if isinstance(parse_mode, Default):
        parse_mode = bot.default[parse_mode.name]
    return isinstance(parse_mode, str) and parse_mode.lower() == 'html'


def _apply(bot: Bot, method: TelegramMethod[Any]) -> None:
    markup = getattr(method, 'reply_markup', None)
    if isinstance(markup, InlineKeyboardMarkup):
        method.reply_markup = theme_markup(markup)

    if isinstance(method, AnswerCallbackQuery):
        if method.text and '<tg-emoji' in method.text:
            method.text = to_plain(method.text)
        return

    fields = type(method).model_fields
    if 'parse_mode' not in fields:
        return  # rich-сообщения и прочие методы без parse_mode: <tg-emoji> там законен
    for text_field, entities_field in _TEXT_FIELDS:
        value = getattr(method, text_field, None) if text_field in fields else None
        if not isinstance(value, str) or 'tg-emoji' not in value:
            continue
        if entities_field in fields and getattr(method, entities_field, None):
            continue
        if _is_html(bot, getattr(method, 'parse_mode', None)):
            restored = restore_escaped(value)
            if restored != value:
                logger.warning(
                    'Восстановлен экранированный <tg-emoji> — место экранирует наш текст',
                    method=type(method).__name__,
                    text=value[:80],
                )
                setattr(method, text_field, restored)
        else:
            setattr(method, text_field, to_plain(value))


class PremiumThemeMiddleware(BaseRequestMiddleware):
    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[Any],
        bot: Bot,
        method: TelegramMethod[Any],
    ) -> Any:
        if settings.PREMIUM_THEME_ENABLED:
            try:
                _apply(bot, method)
            except Exception as error:
                logger.warning('Тема Premium не применена', method=type(method).__name__, error=str(error))
        return await make_request(bot, method)
