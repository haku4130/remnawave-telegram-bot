"""Тексты форка (app/localization/fork/) поверх апстрима.

Ловят то, что сломает бота после синка с апстримом: ключ форка, который апстрим
переименовал (молча перестанет действовать), разошедшиеся {плейсхолдеры}
(.format() упадёт в рантайме), опечатку в имени эмодзи.
"""

import json
import re
from pathlib import Path

import pytest

from app.config import settings
from app.localization import loader
from app.localization.emoji_catalog import CATALOG


ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_DIR = ROOT / 'app' / 'localization' / 'locales'
FORK_DIR = ROOT / 'app' / 'localization' / 'fork'
FORK_LANGS = ('ru', 'en')
MARKER_RE = re.compile(r'\{e:([^}]*)\}')
PLACEHOLDER_RE = re.compile(r'\{(?!e:)[^}]*\}')
# Апстримный SUPPORT_INFO в локали мёртв для ru/en: бот показывает текст из _DYNAMIC_LANGUAGE_CONFIGS, а
# литерал «{settings.SUPPORT_USERNAME}» из локали никто не форматирует. Текст форка отдаётся как есть,
# без .format(), поэтому плейсхолдеров в нём быть не должно (проверяет test_fork_support_info_has_no_placeholders).
UNFORMATTED_UPSTREAM_KEYS = frozenset({'SUPPORT_INFO'})


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


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


def test_fork_files_have_identical_keys():
    ru, en = (_read(FORK_DIR / f'{lang}.json') for lang in FORK_LANGS)
    assert set(ru) == set(en), f'ru-en: {sorted(set(ru) - set(en))}, en-ru: {sorted(set(en) - set(ru))}'


@pytest.mark.parametrize('lang', FORK_LANGS)
def test_fork_keys_exist_upstream_or_are_prefixed(lang):
    upstream = _read(UPSTREAM_DIR / 'ru.json')
    fork = _read(FORK_DIR / f'{lang}.json')
    stray = sorted(key for key in fork if key not in upstream and not key.startswith('FORK_'))
    assert not stray, f'ключей нет в апстриме (переименованы?), а префикса FORK_ нет: {stray}'


@pytest.mark.parametrize('lang', FORK_LANGS)
def test_fork_values_are_strings_with_known_emoji(lang):
    for key, value in _read(FORK_DIR / f'{lang}.json').items():
        assert isinstance(value, str), key
        for name in MARKER_RE.findall(value):
            assert name in CATALOG, f'{lang}.{key}: неизвестный эмодзи {{e:{name}}}'


@pytest.mark.parametrize('lang', FORK_LANGS)
def test_fork_placeholders_match_upstream(lang):
    upstream = _read(UPSTREAM_DIR / f'{lang}.json')
    problems = []
    for key, value in _read(FORK_DIR / f'{lang}.json').items():
        if key not in upstream or key in UNFORMATTED_UPSTREAM_KEYS:
            continue
        if sorted(PLACEHOLDER_RE.findall(value)) != sorted(PLACEHOLDER_RE.findall(upstream[key])):
            problems.append(key)
    assert not problems, f'{{плейсхолдеры}} разошлись с апстримом: {problems}'


@pytest.mark.parametrize('lang', FORK_LANGS)
def test_fork_support_info_has_no_placeholders(lang):
    # get_support_info_text отдаёт SUPPORT_INFO без .format(): фигурная скобка дошла бы до пользователя.
    assert '{' not in MARKER_RE.sub('', _read(FORK_DIR / f'{lang}.json')['SUPPORT_INFO'])


def test_fork_placeholders_match_between_languages():
    ru, en = (_read(FORK_DIR / f'{lang}.json') for lang in FORK_LANGS)
    mismatched = [k for k in ru if sorted(PLACEHOLDER_RE.findall(ru[k])) != sorted(PLACEHOLDER_RE.findall(en[k]))]
    assert not mismatched, mismatched


def test_resolve_emoji_markers():
    assert loader.resolve_emoji_markers('{e:rocket} Go {name}') == (
        '<tg-emoji emoji-id="5445284980978621387">🚀</tg-emoji> Go {name}'
    )
    with pytest.raises(ValueError):
        loader.resolve_emoji_markers('{e:nope} x')


def test_fork_layer_overrides_upstream_when_enabled(theme_on, monkeypatch, tmp_path):
    (tmp_path / 'ru.json').write_text(json.dumps({'BACK': '{e:home} Домой', 'FORK_X': 'x {n}'}), encoding='utf-8')
    monkeypatch.setattr(loader, 'FORK_LOCALES_DIR', tmp_path)
    data = loader.load_locale('ru')
    assert data['BACK'] == '<tg-emoji emoji-id="5465226866321268133">🏠</tg-emoji> Домой'
    assert data['FORK_X'] == 'x {n}'
    assert 'MAIN_MENU' in data  # апстрим на месте


def test_fork_layer_skipped_when_disabled(theme_off, monkeypatch, tmp_path):
    (tmp_path / 'ru.json').write_text(json.dumps({'BACK': 'Домой'}), encoding='utf-8')
    monkeypatch.setattr(loader, 'FORK_LOCALES_DIR', tmp_path)
    assert loader.load_locale('ru')['BACK'] == _read(UPSTREAM_DIR / 'ru.json')['BACK']


def test_unknown_emoji_keeps_upstream_value_and_does_not_crash(theme_on, monkeypatch, tmp_path):
    (tmp_path / 'ru.json').write_text(json.dumps({'BACK': '{e:nope} Домой'}), encoding='utf-8')
    monkeypatch.setattr(loader, 'FORK_LOCALES_DIR', tmp_path)
    assert loader.load_locale('ru')['BACK'] == _read(UPSTREAM_DIR / 'ru.json')['BACK']


def test_server_overrides_still_win_over_fork(theme_on, monkeypatch, tmp_path):
    fork_dir = tmp_path / 'fork'
    user_dir = tmp_path / 'user'
    fork_dir.mkdir()
    user_dir.mkdir()
    (fork_dir / 'ru.json').write_text(json.dumps({'BACK': 'форк'}), encoding='utf-8')
    (user_dir / 'ru.json').write_text(json.dumps({'BACK': 'сервер'}), encoding='utf-8')
    monkeypatch.setattr(loader, 'FORK_LOCALES_DIR', fork_dir)
    monkeypatch.setattr(settings, 'LOCALES_PATH', str(user_dir))
    assert loader.load_locale('ru')['BACK'] == 'сервер'
