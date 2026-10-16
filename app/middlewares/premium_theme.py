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
отправке — запрос уходит как есть. Если Telegram отверг оформленный запрос
(TelegramBadRequest), он один раз повторяется без оформления.
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from aiogram import Bot
from aiogram.client.default import Default
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, TelegramMethod
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.localization.emoji_catalog import CATALOG, by_unicode
from app.utils.button_theme import is_navigation, style_for_button
from app.utils.miniapp_buttons import strip_leading_emoji


logger = structlog.get_logger(__name__)

_TG_EMOJI_RE = re.compile(r'<tg-emoji emoji-id="(\d+)">(.*?)</tg-emoji>', re.DOTALL)
_LEADING_TG_EMOJI_RE = re.compile(r'^\s*<tg-emoji emoji-id="(\d+)">.*?</tg-emoji>\s*', re.DOTALL)
_ESCAPED_TG_EMOJI_RE = re.compile(
    r'&lt;tg-emoji emoji-id=(?:&quot;|")(\d+)(?:&quot;|")&gt;(.*?)&lt;/tg-emoji&gt;', re.DOTALL
)
# Восстанавливаем только свои эмодзи: тексты форка несут id из каталога, а чужой
# экранированный тег — это пользовательский контент, и оживлять его нельзя.
_KNOWN_IDS = frozenset(entry.emoji_id for entry in CATALOG.values())
# Поле текста и поле его сущностей: при готовых сущностях текст менять нельзя —
# их смещения посчитаны по исходной строке.
_TEXT_FIELDS = (('text', 'entities'), ('caption', 'caption_entities'))
# Всё, что меняет _apply. Он не мутирует значения, а подменяет их новыми объектами,
# поэтому снимка этих полей достаточно, чтобы восстановить исходный запрос (без
# глубокой копии всего метода — в нём бывают файлы).
_THEMED_FIELDS = ('reply_markup', 'text', 'caption')
_NOT_MODIFIED = 'message is not modified'


def to_plain(value: str) -> str:
    return _TG_EMOJI_RE.sub(lambda match: match.group(2), value)


def restore_escaped(value: str) -> str:
    def revive(match: re.Match[str]) -> str:
        if match.group(1) not in _KNOWN_IDS:
            return match.group(0)
        return f'<tg-emoji emoji-id="{match.group(1)}">{match.group(2)}</tg-emoji>'

    return _ESCAPED_TG_EMOJI_RE.sub(revive, value)


def theme_button(button: InlineKeyboardButton, allow_icons: bool = True) -> InlineKeyboardButton:
    text = button.text or ''
    update: dict[str, Any] = {}
    navigation = is_navigation(button)
    # Навигация — без иконки и без цвета; вне личных чатов иконки тоже не выносим.
    icons = allow_icons and not navigation

    leading_tag = _LEADING_TG_EMOJI_RE.match(text) if icons else None
    if leading_tag and text[leading_tag.end() :].strip():
        if not button.icon_custom_emoji_id:
            update['icon_custom_emoji_id'] = leading_tag.group(1)
        text = text[leading_tag.end() :]
    elif icons and not leading_tag and not button.icon_custom_emoji_id:
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
    if button.style is None and not navigation:
        style = style_for_button(button)
        if style:
            update['style'] = style
    return button.model_copy(update=update) if update else button


def theme_markup(markup: InlineKeyboardMarkup, allow_icons: bool = True) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[theme_button(button, allow_icons) for button in row] for row in markup.inline_keyboard]
    )


def _is_html(bot: Bot, parse_mode: Any) -> bool:
    if isinstance(parse_mode, Default):
        parse_mode = bot.default[parse_mode.name]
    return isinstance(parse_mode, str) and parse_mode.lower() == 'html'


def _apply(bot: Bot, method: TelegramMethod[Any]) -> None:
    markup = getattr(method, 'reply_markup', None)
    if isinstance(markup, InlineKeyboardMarkup):
        # Анимированные иконки — только в личных чатах; канал и inline-сообщения не проверены.
        chat_id = getattr(method, 'chat_id', None)
        allow_icons = isinstance(chat_id, int) and chat_id > 0
        method.reply_markup = theme_markup(markup, allow_icons=allow_icons)

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
                    restored=len(_ESCAPED_TG_EMOJI_RE.findall(value)) - len(_ESCAPED_TG_EMOJI_RE.findall(restored)),
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
        if not settings.PREMIUM_THEME_ENABLED:
            return await make_request(bot, method)

        fields = type(method).model_fields
        original = {name: getattr(method, name) for name in _THEMED_FIELDS if name in fields}
        try:
            _apply(bot, method)
        except Exception:
            logger.warning('Тема Premium не применена', method=type(method).__name__, exc_info=True)
        changed = {name: value for name, value in original.items() if getattr(method, name) != value}
        if not changed:
            return await make_request(bot, method)

        try:
            return await make_request(bot, method)
        except TelegramBadRequest as error:
            # «Не изменено» — не вина оформления; повтор без темы заменил бы оформленное сообщение голым.
            if _NOT_MODIFIED in error.message.lower():
                raise
            # Оформление могло сломать запрос (иконка, стиль, тег) — повторяем один раз как было.
            logger.warning(
                'Telegram отверг оформленный запрос, повтор без темы',
                method=type(method).__name__,
                error=error.message,
            )
            return await make_request(bot, method.model_copy(update=changed))
