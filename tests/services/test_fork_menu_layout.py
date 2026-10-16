"""Главное меню форка: одна зелёная кнопка по состоянию пользователя."""

from types import SimpleNamespace

import pytest
from aiogram.types import InlineKeyboardButton

from app.config import settings
from app.services.menu_layout import fork_layout
from app.services.menu_layout.context import MenuContext
from app.services.menu_layout.service import MenuLayoutService
from app.utils.button_theme import style_for_button


def _sub(status='active', is_trial=False, days_left=20, pending=False, daily=False):
    return SimpleNamespace(
        actual_status=status,
        is_trial=is_trial,
        days_left=days_left,
        is_pending_trial=pending,
        is_daily_tariff=daily,
    )


@pytest.mark.parametrize(
    ('subscription', 'trial_ok', 'expected'),
    [
        (None, True, 'trial'),
        (None, False, 'buy'),
        (_sub(pending=True), True, 'trial'),
        (_sub('active', days_left=20), False, 'connect'),
        (_sub('trial', is_trial=True, days_left=1), False, 'connect'),
        (_sub('limited', days_left=20), False, 'connect'),
        (_sub('active', days_left=3), False, 'extend'),
        (_sub('active', days_left=0, daily=True), False, 'connect'),
        (_sub('expired'), False, 'extend'),
        (_sub('disabled'), False, 'extend'),
        (_sub('expired', is_trial=True), False, 'buy'),
        (_sub('expired', is_trial=True), True, 'trial'),
    ],
)
def test_resolve_primary_action(monkeypatch, subscription, trial_ok, expected):
    monkeypatch.setattr(fork_layout, 'is_trial_available', lambda user: trial_ok)
    assert fork_layout.resolve_primary_action(SimpleNamespace(), subscription) == expected


def _visible(context: MenuContext) -> list[tuple[str, str | None]]:
    config = fork_layout.build_fork_menu_config()
    result = []
    for row in config['rows']:
        if not MenuLayoutService._evaluate_conditions(row.get('conditions'), context):
            continue
        for button_id in row['buttons']:
            cfg = config['buttons'][button_id]
            if not cfg.get('enabled', True):
                continue
            if not MenuLayoutService._check_visibility(cfg.get('visibility', 'all'), context):
                continue
            if not MenuLayoutService._evaluate_conditions(cfg.get('conditions'), context):
                continue
            # Исходящий middleware красит кнопки без явного стиля по таблице — считаем и их.
            themed = style_for_button(InlineKeyboardButton(text='x', callback_data=cfg['action']))
            result.append((button_id, cfg.get('style') or themed))
    return result


def _greens(context):
    return [button_id for button_id, style in _visible(context) if style == 'success']


def test_exactly_one_green_button_in_every_state(monkeypatch):
    monkeypatch.setattr(settings, 'SUPPORT_MENU_ENABLED', True)
    states = {
        'trial': MenuContext(primary_action='trial'),
        'buy': MenuContext(primary_action='buy'),
        'connect': MenuContext(primary_action='connect', has_active_subscription=True, subscription_is_active=True),
        'extend_active': MenuContext(
            primary_action='extend', has_active_subscription=True, subscription_is_active=True
        ),
        'extend_expired': MenuContext(primary_action='extend'),
        'trial_cart': MenuContext(primary_action='trial', has_saved_cart=True),
        'buy_cart': MenuContext(primary_action='buy', has_saved_cart=True),
        'connect_cart': MenuContext(
            primary_action='connect', has_active_subscription=True, subscription_is_active=True, has_saved_cart=True
        ),
    }
    expected = {
        'trial': ['trial'],
        'buy': ['buy_primary'],
        'connect': ['connect'],
        'extend_active': ['extend'],
        'extend_expired': ['extend'],
        'trial_cart': ['trial'],
        'buy_cart': ['buy_primary'],
        'connect_cart': ['connect'],
    }
    for name, context in states.items():
        assert _greens(context) == expected[name], name


def test_extend_while_active_keeps_blue_connect():
    context = MenuContext(primary_action='extend', has_active_subscription=True, subscription_is_active=True)
    assert ('connect_secondary', 'primary') in _visible(context)


def test_tariffs_button_blue_next_to_trial():
    assert ('buy_subscription', 'primary') in _visible(MenuContext(primary_action='trial'))


def test_default_config_switches_with_theme(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    assert 'extend' in MenuLayoutService.get_default_config()['buttons']
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    assert 'extend' not in MenuLayoutService.get_default_config()['buttons']


def test_build_button_passes_style():
    button = MenuLayoutService._build_button(
        {'type': 'builtin', 'text': {'ru': 'Продлить'}, 'action': 'subscription_extend', 'style': 'success'},
        MenuContext(),
        SimpleNamespace(),
    )
    assert button.style == 'success'


def test_primary_action_condition_accepts_list():
    context = MenuContext(primary_action='trial')
    assert MenuLayoutService._evaluate_conditions({'primary_action': ['trial', 'extend']}, context)
    assert not MenuLayoutService._evaluate_conditions({'primary_action': 'buy'}, context)
