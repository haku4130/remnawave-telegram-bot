"""Анимированный баннер вместо статичного логотипа.

Баннер — короткий MP4 без звука, Telegram показывает его как анимацию (GIF):
бесконечный цикл, без кнопки воспроизведения. Он заменяет логотип в двух местах:

* фото-сообщения экранов (``answer_photo``/``edit_media``) становятся
  ``answer_animation``/``InputMediaAnimation``;
* шапка rich-сообщений: вместо ``<img src="https://…">`` идёт
  ``<video src="tg://video?id=…">`` плюс сам файл в ``InputRichMessage.media``.

Файл загружается с диска один раз, дальше переиспользуется его file_id: так
Telegram не нужно ничего качать с нашего сервера (с msk3 он и не может).

Запасной вариант — статичный логотип. Если Telegram отклонил баннер, флаг
``_unavailable`` выключает его до рестарта, и вызывающий повторяет отправку уже
со статичным логотипом.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from aiogram.types import FSInputFile, InputMediaAnimation, InputRichMessageMedia, TelegramObject

from app.config import settings


logger = structlog.get_logger(__name__)

RICH_MEDIA_ID = 'zanity_banner'
RICH_BANNER_TAG = f'<video src="tg://video?id={RICH_MEDIA_ID}"/>'

# Признаки того, что Telegram отверг именно медиа, а не чат, сообщение или
# разметку. Без такой проверки рассылка выключила бы баннер на первом же
# «chat not found» у удалившегося пользователя.
_MEDIA_ERROR_MARKERS = ('media', 'file', 'animation', 'video', 'image', 'photo', 'document')

_file_id: str | None = None
_unavailable = False


def _banner_path() -> Path:
    return Path(settings.ANIMATED_BANNER_FILE)


def is_banner_enabled() -> bool:
    """Баннер включён, файл на месте и Telegram его ещё не отверг."""
    if not settings.ANIMATED_BANNER_ENABLED or _unavailable:
        return False
    return _file_id is not None or _banner_path().is_file()


def get_banner_media() -> str | FSInputFile:
    """file_id после первой загрузки, до неё — сам файл."""
    return _file_id or FSInputFile(_banner_path())


def _find_file_id(value: Any) -> str | None:
    if isinstance(value, dict):
        animation = value.get('animation')
        if isinstance(animation, dict) and animation.get('file_id'):
            return animation['file_id']
        values = value.values()
    elif isinstance(value, list):
        values = value
    else:
        return None
    for item in values:
        found = _find_file_id(item)
        if found:
            return found
    return None


def cache_banner_file_id(message: Any) -> None:
    """Запоминает file_id баннера из ответа Telegram (анимация или rich-сообщение)."""
    global _file_id
    if _file_id or message is None:
        return
    animation = getattr(message, 'animation', None)
    file_id = getattr(animation, 'file_id', None)
    if not isinstance(file_id, str):
        rich_message = getattr(message, 'rich_message', None)
        file_id = _find_file_id(rich_message.model_dump()) if isinstance(rich_message, TelegramObject) else None
    if isinstance(file_id, str) and file_id:
        _file_id = file_id


def is_banner_media_error(error: Exception) -> bool:
    text = str(error).lower()
    # MEDIA_CAPTION_TOO_LONG и прочие ошибки подписи — про текст, а не про файл.
    if 'caption' in text:
        return False
    return any(marker in text for marker in _MEDIA_ERROR_MARKERS)


def mark_banner_unavailable(error: Exception) -> bool:
    """Выключает баннер до рестарта. True — если выключил только что (можно повторить)."""
    global _unavailable
    if _unavailable:
        return False
    _unavailable = True
    logger.warning(
        'Telegram отклонил анимированный баннер — до рестарта используется статичный логотип',
        error=str(error)[:300],
    )
    return True


def rich_banner_media(rich_html: str) -> list[InputRichMessageMedia] | None:
    """Файл баннера для InputRichMessage.media, если разметка на него ссылается."""
    if RICH_BANNER_TAG not in rich_html:
        return None
    return [InputRichMessageMedia(id=RICH_MEDIA_ID, media=InputMediaAnimation(media=get_banner_media()))]


def _reset_banner_state() -> None:
    """Сбрасывает кеш и флаг (для тестов)."""
    global _file_id, _unavailable
    _file_id = None
    _unavailable = False
