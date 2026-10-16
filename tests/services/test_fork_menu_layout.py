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


class _RaisingDailySub:
    """Как ORM-подписка с незагруженным tariff: чтение is_daily_tariff в async падает (MissingGreenlet)."""

    actual_status = 'active'
    is_trial = False
    days_left = 2
    is_pending_trial = False

    @property
    def is_daily_tariff(self):
        raise RuntimeError('MissingGreenlet')


def test_resolve_primary_action_survives_unreadable_daily_flag(monkeypatch):
    monkeypatch.setattr(fork_layout, 'is_trial_available', lambda user: False)
    assert fork_layout.resolve_primary_action(SimpleNamespace(), _RaisingDailySub()) == 'extend'


def test_resolve_primary_action_does_not_lazy_load_tariff(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.orm import make_transient_to_detached

    from app.database.models import Subscription, Tariff

    monkeypatch.setattr(fork_layout, 'is_trial_available', lambda user: False)
    expiring = Subscription(id=1, status='active', is_trial=False, end_date=datetime.now(UTC) + timedelta(days=2))
    # Подписка вне сессии с незагруженным tariff: ленивая загрузка упала бы (в async — MissingGreenlet).
    make_transient_to_detached(expiring)
    assert fork_layout.resolve_primary_action(SimpleNamespace(), expiring) == 'extend'
    expiring.tariff = Tariff(name='day', is_daily=True)
    assert fork_layout._is_daily_tariff(expiring) is True
    assert fork_layout.resolve_primary_action(SimpleNamespace(), expiring) == 'connect'


def test_fork_primary_action_returns_none_when_resolve_fails(monkeypatch):
    from app.keyboards import inline

    def boom(user, subscription):
        raise RuntimeError('boom')

    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    monkeypatch.setattr(fork_layout, 'resolve_primary_action', boom)
    assert inline._fork_primary_action(SimpleNamespace(), SimpleNamespace()) is None


def _visible(context: MenuContext, rows_key: str = 'rows') -> list[tuple[str, str | None]]:
    config = fork_layout.build_fork_menu_config()
    result = []
    for row in config[rows_key]:
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


def _greens(context, rows_key: str = 'rows'):
    return [button_id for button_id, style in _visible(context, rows_key) if style == 'success']


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
        assert _greens(context, 'more_rows') == [], name


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


def _row_ids_and_buttons(rows):
    return [(row['id'], row['buttons']) for row in rows]


def test_main_menu_last_row_is_friends_support_more():
    rows = fork_layout.build_fork_menu_config()['rows']
    without_staff = [row for row in rows if row['id'] not in ('admin_row', 'moderator_row')]
    assert without_staff[-1]['buttons'] == ['referrals', 'support', 'more']
    assert without_staff[-1]['max_per_row'] == 3
    assert [row['id'] for row in rows[-3:]] == ['friends_support_more_row', 'admin_row', 'moderator_row']


def test_main_menu_no_longer_holds_buttons_moved_to_more():
    config = fork_layout.build_fork_menu_config()
    on_main = {button_id for row in config['rows'] for button_id in row['buttons']}
    assert not on_main & {'promocode', 'info', 'language', 'contests'}
    assert 'more' in on_main
    assert not {'referral_promo_row', 'contests_row', 'support_info_row', 'language_row'} & {
        row['id'] for row in config['rows']
    }


def test_more_submenu_rows():
    config = fork_layout.build_fork_menu_config()
    assert _row_ids_and_buttons(config['more_rows']) == [
        ('more_promo_info_row', ['promocode', 'info']),
        ('language_row', ['language']),
        ('contests_row', ['contests']),
        ('more_back_row', ['more_back']),
    ]
    by_id = {row['id']: row for row in config['more_rows']}
    assert by_id['language_row']['conditions'] == {'language_selection_enabled': True}
    assert by_id['contests_row']['conditions'] == {'contests_visible': True}
    assert by_id['more_back_row']['max_per_row'] == 1


def test_more_buttons_are_plain_navigation():
    buttons = fork_layout.build_fork_menu_config()['buttons']
    assert buttons['more']['action'] == 'menu_more'
    assert buttons['more_back']['action'] == 'back_to_menu'
    assert 'style' not in buttons['more']
    assert 'style' not in buttons['more_back']
    assert buttons['more']['text'] == {'ru': '➕ Ещё', 'en': '➕ More'}
    assert buttons['more_back']['text'] == {'ru': '⬅️ Назад', 'en': '⬅️ Back'}


def test_more_submenu_visible_buttons(monkeypatch):
    monkeypatch.setattr(settings, 'LANGUAGE_SELECTION_ENABLED', True)
    monkeypatch.setattr(MenuLayoutService, '_evaluate_conditions', staticmethod(lambda conditions, context: True))
    visible = [button_id for button_id, _ in _visible(MenuContext(), 'more_rows')]
    assert visible == ['promocode', 'info', 'language', 'contests', 'more_back']


def test_more_submenu_hides_language_and_contests_when_conditions_fail(monkeypatch):
    def evaluate(conditions, context):
        return not conditions or not ({'language_selection_enabled', 'contests_visible'} & set(conditions))

    monkeypatch.setattr(MenuLayoutService, '_evaluate_conditions', staticmethod(evaluate))
    visible = [button_id for button_id, _ in _visible(MenuContext(), 'more_rows')]
    assert visible == ['promocode', 'info', 'more_back']


@pytest.mark.asyncio
async def test_build_keyboard_reads_rows_key(monkeypatch):
    config = fork_layout.build_fork_menu_config()

    async def fake_get_config(db):
        return config

    monkeypatch.setattr(MenuLayoutService, 'get_config', fake_get_config)
    monkeypatch.setattr(MenuLayoutService, '_evaluate_conditions', staticmethod(lambda conditions, context: True))
    context = MenuContext(language='ru')

    submenu = await MenuLayoutService.build_keyboard(None, context, rows_key='more_rows')
    callbacks = [button.callback_data for row in submenu.inline_keyboard for button in row]
    assert callbacks == ['menu_promocode', 'menu_info', 'menu_language', 'contests_menu', 'back_to_menu']
    assert submenu.inline_keyboard[-1][0].text == '⬅️ Назад'
    assert all(button.style is None for row in submenu.inline_keyboard for button in row)

    main = await MenuLayoutService.build_keyboard(None, context)
    main_callbacks = [button.callback_data for row in main.inline_keyboard for button in row]
    assert 'menu_more' in main_callbacks
    assert not {'menu_promocode', 'menu_info', 'menu_language', 'contests_menu'} & set(main_callbacks)
