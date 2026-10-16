from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.utils import fork_onboarding as fo


@pytest.mark.parametrize(
    ('code', 'expected'),
    [('ru', 'ru'), ('ru-RU', 'ru'), ('uk', 'ru'), ('be', 'ru'), ('kk', 'ru'), ('en', 'en'), ('de', 'en'), (None, None)],
)
def test_resolve_auto_language(code, expected):
    assert fo.resolve_auto_language(code, ['ru', 'en']) == expected


def test_resolve_auto_language_respects_available():
    assert fo.resolve_auto_language('de', ['ru']) is None
    assert fo.resolve_auto_language('uk', ['en']) is None


def _user(used=False, auth_type='telegram'):
    return SimpleNamespace(auth_type=auth_type, is_trial_already_used=MagicMock(return_value=used))


def test_trial_available(monkeypatch):
    monkeypatch.setattr(settings, 'TRIAL_DURATION_DAYS', 3)
    monkeypatch.setattr(settings, 'TRIAL_DISABLED_FOR', 'none')
    monkeypatch.setattr(type(settings), 'is_trial_disabled_for_user', lambda self, auth_type: False)
    monkeypatch.setattr(type(settings), 'is_trial_paid_activation_enabled', lambda self: False)
    assert fo.is_trial_available(_user()) is True
    assert fo.is_trial_available(_user(used=True)) is False
    monkeypatch.setattr(type(settings), 'is_trial_paid_activation_enabled', lambda self: True)
    assert fo.is_trial_available(_user()) is False


def test_trial_unavailable_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, 'TRIAL_DURATION_DAYS', 0)
    assert fo.is_trial_available(_user()) is False


def test_trial_check_error_means_unavailable(monkeypatch):
    monkeypatch.setattr(settings, 'TRIAL_DURATION_DAYS', 3)
    monkeypatch.setattr(settings, 'TRIAL_DISABLED_FOR', 'none')
    monkeypatch.setattr(type(settings), 'is_trial_disabled_for_user', lambda self, auth_type: False)
    monkeypatch.setattr(type(settings), 'is_trial_paid_activation_enabled', lambda self: False)
    broken = SimpleNamespace(auth_type='telegram', is_trial_already_used=MagicMock(side_effect=RuntimeError))
    assert fo.is_trial_available(broken) is False


@pytest.mark.parametrize(
    ('days', 'ru', 'en'),
    [
        (1, '1 день', '1 day'),
        (3, '3 дня', '3 days'),
        (5, '5 дней', '5 days'),
        (11, '11 дней', '11 days'),
        (21, '21 день', '21 days'),
        (22, '22 дня', '22 days'),
    ],
)
def test_format_days(days, ru, en):
    ru_texts = SimpleNamespace(
        language='ru', FORK_DAYS_ONE='{n} день', FORK_DAYS_FEW='{n} дня', FORK_DAYS_MANY='{n} дней'
    )
    en_texts = SimpleNamespace(
        language='en', FORK_DAYS_ONE='{n} day', FORK_DAYS_FEW='{n} days', FORK_DAYS_MANY='{n} days'
    )
    assert fo.format_days(ru_texts, days) == ru
    assert fo.format_days(en_texts, days) == en


@pytest.fixture
def fork_texts(monkeypatch):
    from app.localization import loader

    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    monkeypatch.setattr(settings, 'BOT_USERNAME', 'zanity_vpn_bot')
    monkeypatch.setattr(settings, 'TRIAL_DURATION_DAYS', 3)
    loader.clear_locale_cache()
    yield
    loader.clear_locale_cache()


def _tg(first_name='Андрей <b>'):
    return SimpleNamespace(first_name=first_name, username='andrey')


def _reg_user(language='ru', active=False):
    sub = SimpleNamespace(is_active=active)
    return SimpleNamespace(
        language=language,
        subscriptions=[sub] if active else [],
        auth_type='telegram',
        is_trial_already_used=MagicMock(return_value=active),
    )


def test_welcome_with_trial(fork_texts, monkeypatch):
    monkeypatch.setattr(fo, 'is_trial_available', lambda user: True)
    text, kb = fo.build_welcome_screen(_reg_user(), _tg())
    assert 'Андрей &lt;b&gt;' in text  # имя экранировано
    assert '3 дня' in text
    assert 'https://t.me/zanity_vpn_bot?start=rules' in text
    assert '<tg-emoji' in text
    first = kb.inline_keyboard[0][0]
    assert first.callback_data == 'trial_activate' and first.style == 'success'
    assert kb.inline_keyboard[1][0].callback_data == 'menu_buy' and kb.inline_keyboard[1][0].style == 'primary'


def test_welcome_with_active_subscription(fork_texts, monkeypatch):
    monkeypatch.setattr(fo, 'is_trial_available', lambda user: False)
    text, kb = fo.build_welcome_screen(_reg_user(active=True), _tg('Андрей'))
    assert kb.inline_keyboard[0][0].callback_data == 'subscription_connect'
    assert len(kb.inline_keyboard) == 1


def test_welcome_without_trial(fork_texts, monkeypatch):
    monkeypatch.setattr(fo, 'is_trial_available', lambda user: False)
    text, kb = fo.build_welcome_screen(_reg_user(), _tg('Андрей'))
    assert kb.inline_keyboard[0][0].callback_data == 'menu_buy'
    assert kb.inline_keyboard[0][0].style == 'success'


def test_welcome_english(fork_texts, monkeypatch):
    monkeypatch.setattr(fo, 'is_trial_available', lambda user: True)
    text, _ = fo.build_welcome_screen(_reg_user(language='en'), _tg('Ann'))
    assert '3 days' in text and 'Hi, Ann' in text


def test_welcome_without_bot_username_drops_rules_paragraph(fork_texts, monkeypatch):
    monkeypatch.setattr(settings, 'BOT_USERNAME', None)
    monkeypatch.setattr(fo, 'is_trial_available', lambda user: True)
    text, _ = fo.build_welcome_screen(_reg_user(), _tg('Андрей'))
    assert 'href' not in text
    assert '3 дня' in text
