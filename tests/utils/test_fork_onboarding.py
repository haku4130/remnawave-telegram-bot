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
