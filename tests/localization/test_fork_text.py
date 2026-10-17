"""Инструменты форка: fork_text, форматтеры трафика/устройств/срока, SUPPORT_INFO слоя форка."""

import json

import pytest

from app.config import settings
from app.localization import loader
from app.localization.fork_text import (
    fork_devices,
    fork_period,
    fork_text,
    fork_traffic,
    fork_traffic_usage,
)
from app.localization.texts import get_texts


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


def _fork_dir(monkeypatch, tmp_path, data_by_lang):
    for lang, data in data_by_lang.items():
        (tmp_path / f'{lang}.json').write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setattr(loader, 'FORK_LOCALES_DIR', tmp_path)
    loader.clear_locale_cache()


def test_fork_text_returns_fork_value_with_theme(theme_on, monkeypatch, tmp_path):
    _fork_dir(monkeypatch, tmp_path, {'ru': {'FORK_HELLO': 'Привет'}})
    assert fork_text(get_texts('ru'), 'FORK_HELLO', 'upstream') == 'Привет'


def test_fork_text_returns_default_without_theme(theme_off, monkeypatch, tmp_path):
    _fork_dir(monkeypatch, tmp_path, {'ru': {'FORK_HELLO': 'Привет'}})
    assert fork_text(get_texts('ru'), 'FORK_HELLO', 'upstream') == 'upstream'


def test_fork_text_returns_default_for_missing_key(theme_on, monkeypatch, tmp_path):
    _fork_dir(monkeypatch, tmp_path, {'ru': {'FORK_HELLO': 'Привет'}})
    assert fork_text(get_texts('ru'), 'FORK_NOPE', 'upstream') == 'upstream'


def test_fork_text_ignores_upstream_only_keys(theme_on, monkeypatch, tmp_path):
    # BACK есть в апстриме, но не в слое форка: «ключ форка» — только то, что дал форк.
    _fork_dir(monkeypatch, tmp_path, {'ru': {'FORK_HELLO': 'Привет'}})
    assert fork_text(get_texts('ru'), 'BACK', 'upstream') == 'upstream'


def test_fork_text_keeps_server_override(theme_on, monkeypatch, tmp_path):
    fork_dir = tmp_path / 'fork'
    user_dir = tmp_path / 'user'
    fork_dir.mkdir()
    user_dir.mkdir()
    (fork_dir / 'ru.json').write_text(json.dumps({'FORK_HELLO': 'форк'}), encoding='utf-8')
    (user_dir / 'ru.json').write_text(json.dumps({'FORK_HELLO': 'сервер'}), encoding='utf-8')
    monkeypatch.setattr(loader, 'FORK_LOCALES_DIR', fork_dir)
    monkeypatch.setattr(settings, 'LOCALES_PATH', str(user_dir))
    loader.clear_locale_cache()
    assert fork_text(get_texts('ru'), 'FORK_HELLO', 'upstream') == 'сервер'


def test_fork_traffic(theme_on):
    en, ru = get_texts('en'), get_texts('ru')
    assert fork_traffic(en, 12.5) == '12.5 GB'
    assert fork_traffic(ru, 12.5) == '12.5 ГБ'
    assert fork_traffic(ru, 100.0) == '100 ГБ'
    assert fork_traffic(ru, 0) == '∞'
    assert fork_traffic(ru, None) == '∞'


def test_fork_traffic_usage(theme_on):
    assert fork_traffic_usage(get_texts('ru'), 1.234, 50) == '1.2 ГБ из 50 ГБ'
    assert fork_traffic_usage(get_texts('en'), 0, 50) == '0 GB of 50 GB'
    assert fork_traffic_usage(get_texts('ru'), 3, 0) == '3 ГБ из ∞'
    assert fork_traffic_usage(get_texts('en'), None, None) == '0 GB of ∞'
    assert fork_traffic_usage(get_texts('ru'), 12.5, 0, bold_used=True) == '<b>12.5 ГБ</b> из ∞'
    assert fork_traffic_usage(get_texts('en'), 1.234, 50, bold_used=True) == '<b>1.2 GB</b> of 50 GB'


def test_fork_period(theme_on):
    assert fork_period(get_texts('ru'), 30) == '30 дней'
    assert fork_period(get_texts('ru'), 1) == '1 день'
    assert fork_period(get_texts('ru'), 3) == '3 дня'
    assert fork_period(get_texts('en'), 30) == '30 days'
    assert fork_period(get_texts('en'), 1) == '1 day'


def test_fork_devices(theme_on):
    assert fork_devices(get_texts('ru'), 1, 3) == '1 из 3'
    assert fork_devices(get_texts('en'), 1, 3) == '1 of 3'
    assert fork_devices(get_texts('ru'), 2, 0) == '2 из ∞'
    assert fork_devices(get_texts('en'), '2', None) == '2 of ∞'
    assert fork_devices(get_texts('ru'), None, 3) == '0 из 3'


def test_fork_support_info_beats_dynamic(theme_on, monkeypatch, tmp_path):
    _fork_dir(monkeypatch, tmp_path, {'ru': {'SUPPORT_INFO': 'Поддержка форка'}})
    assert get_texts('ru').SUPPORT_INFO == 'Поддержка форка'


def test_dynamic_support_info_stays_without_fork_key(theme_on, monkeypatch, tmp_path):
    _fork_dir(monkeypatch, tmp_path, {'ru': {'FORK_X': 'x'}})
    assert 'центр тикетов' in get_texts('ru').SUPPORT_INFO


def test_dynamic_support_info_stays_without_theme(theme_off, monkeypatch, tmp_path):
    _fork_dir(monkeypatch, tmp_path, {'ru': {'SUPPORT_INFO': 'Поддержка форка'}})
    assert 'центр тикетов' in get_texts('ru').SUPPORT_INFO


def test_fork_keys_empty_without_theme(theme_off, monkeypatch, tmp_path):
    _fork_dir(monkeypatch, tmp_path, {'ru': {'FORK_X': 'x'}})
    assert loader.fork_keys('ru') == frozenset()


@pytest.mark.parametrize('lang', ['ru', 'en'])
def test_checkout_return_button_is_not_an_arrow_when_theme_on(theme_on, lang):
    label = get_texts(lang).RETURN_TO_SUBSCRIPTION_CHECKOUT
    assert not label.startswith(('⬅', '◀', '↩', '🔙'))
    assert '{e:' not in label
    assert '<tg-emoji emoji-id=' in label
