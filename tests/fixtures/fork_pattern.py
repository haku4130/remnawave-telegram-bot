"""Образец экрана подписки (pattern.md) — одна проверка для всех тестов экранов форка.

``<b>заголовок</b>`` с одной иконкой каталога, пустая строка, блоки через пустую строку. Блок после
заголовка — либо строки данных (у каждой своя иконка), либо одна короткая строка-пояснение без иконки;
смешанных блоков не бывает.
"""

from __future__ import annotations

from app.utils.message_patch import caption_exceeds_telegram_limit


_BANNED = ('<blockquote', '<h1', '<h2', '<h3', '<p>', '▰', '▓', '━')
_MAX_ANIMATED = 5


def assert_screen_pattern(name: str, text: str, *, list_screen: bool = False) -> None:
    """Проверяет текст экрана или уведомления по образцу.

    ``list_screen`` — экран-список (история операций): строки списка начинаются обычным эмодзи, а не анимированным
    (анимированных на сообщение не больше пяти, а строк десять), поэтому блоки списка не проверяются.
    """
    lines = text.splitlines()
    assert text.startswith('<b>') and lines[0].endswith('</b>'), name
    assert lines[0].count('<tg-emoji') == 1, name
    assert text.count('<tg-emoji') <= _MAX_ANIMATED, name
    assert '\n\n\n' not in text and not text.endswith('\n'), name
    assert not caption_exceeds_telegram_limit(text), name
    for banned in _BANNED:
        assert banned not in text, (name, banned)
    blocks = text.split('\n\n')
    assert len(blocks) >= 2, name
    assert '{' not in text and '}' not in text, (name, 'неподставленный плейсхолдер')
    if list_screen:
        return
    for block in blocks[1:]:
        block_lines = block.split('\n')
        if all(line.startswith('<tg-emoji') for line in block_lines):
            continue
        assert not any(line.startswith('<tg-emoji') for line in block_lines), (name, block)
        assert len(block_lines) == 1, (name, block)
