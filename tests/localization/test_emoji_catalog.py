"""Каталог анимированных эмодзи: ID из официальных наборов Telegram, запасной символ — один эмодзи."""

import re

import pytest

from app.localization import emoji_catalog
from app.localization.emoji_catalog import CATALOG, by_unicode, tag


def test_ids_are_numeric_and_names_are_snake_case():
    for name, entry in CATALOG.items():
        assert entry.name == name
        assert re.fullmatch(r'[a-z0-9_]+', name), name
        assert re.fullmatch(r'\d{15,25}', entry.emoji_id), name


def test_fallbacks_are_unique_single_emoji():
    seen = {}
    for name, entry in CATALOG.items():
        normalized = entry.fallback.replace('️', '')
        assert normalized, name
        assert ' ' not in entry.fallback, name
        assert normalized not in seen, f'{name} и {seen.get(normalized)} делят {entry.fallback}'
        seen[normalized] = name


def test_tag_renders_tg_emoji_with_fallback():
    assert tag('rocket') == '<tg-emoji emoji-id="5445284980978621387">🚀</tg-emoji>'


def test_tag_unknown_name_raises_value_error():
    with pytest.raises(ValueError, match='nope'):
        tag('nope')


def test_by_unicode_ignores_variation_selector():
    assert by_unicode('⚙️').name == 'gear'
    assert by_unicode('⚙').name == 'gear'
    assert by_unicode('🚀').name == 'rocket'
    assert by_unicode('🛟') is None
    assert by_unicode('') is None


def test_module_exposes_only_public_api():
    assert {'CATALOG', 'CatalogEmoji', 'tag', 'by_unicode'} <= set(dir(emoji_catalog))
