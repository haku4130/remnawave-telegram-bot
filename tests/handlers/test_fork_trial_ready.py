"""Экран «пробный готов» форка и откат на экран апстрима при ошибке сборки."""

from datetime import UTC, datetime
from types import SimpleNamespace

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.handlers.subscription import purchase
from app.utils import fork_onboarding


TEXTS = SimpleNamespace(FORK_TRIAL_READY='Готово до {end_date}', FORK_BUTTON_HELP='Помощь')
SUBSCRIPTION = SimpleNamespace(end_date=datetime(2026, 10, 19, 12, tzinfo=UTC))
NOTE = '\n\nСписано 10 ₽.'


def _keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Подключиться', callback_data='subscription_connect')],
            [InlineKeyboardButton(text='В меню', callback_data='back_to_menu')],
        ]
    )


def test_theme_off_keeps_upstream_screen(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    keyboard = _keyboard()
    text, markup = purchase._maybe_fork_trial_ready(TEXTS, SUBSCRIPTION, 'апстрим' + NOTE, keyboard, NOTE)
    assert (text, markup) == ('апстрим' + NOTE, keyboard)


def test_theme_on_shows_fork_screen_with_payment_note_once(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    text, markup = purchase._maybe_fork_trial_ready(TEXTS, SUBSCRIPTION, 'апстрим' + NOTE, _keyboard(), NOTE)
    assert text.startswith('Готово до ') and text.endswith(NOTE)
    assert text.count(NOTE) == 1
    assert markup.inline_keyboard[0][0].style == 'success'
    assert [row[0].callback_data for row in markup.inline_keyboard] == [
        'subscription_connect',
        'menu_support',
        'back_to_menu',
    ]


def test_builder_failure_keeps_upstream_screen(monkeypatch):
    def boom(texts, end_date_text, keyboard):
        raise RuntimeError('boom')

    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    monkeypatch.setattr(fork_onboarding, 'build_trial_ready_screen', boom)
    keyboard = _keyboard()
    text, markup = purchase._maybe_fork_trial_ready(TEXTS, SUBSCRIPTION, 'апстрим' + NOTE, keyboard, NOTE)
    assert (text, markup) == ('апстрим' + NOTE, keyboard)
