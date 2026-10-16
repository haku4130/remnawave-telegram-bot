"""Раскладка главного меню форка: одна зелёная кнопка по состоянию пользователя.

Пробный доступен → «Попробовать бесплатно»; подписка активна → «Подключиться»;
платная истекает (≤ EXPIRING_DAYS) или истекла → «Продлить»; иначе → «Выбрать
тариф». Раскладка строится от DEFAULT_MENU_CONFIG апстрима, поэтому новые
кнопки апстрима (simple_subscription, contests…) остаются на месте.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import NoInspectionAvailable

from app.services.menu_layout.constants import DEFAULT_MENU_CONFIG
from app.utils.fork_onboarding import is_trial_available


EXPIRING_DAYS = 3
_ACTIVE_STATUSES = frozenset({'active', 'trial', 'limited'})
_ENDED_STATUSES = frozenset({'expired', 'disabled'})


def _is_daily_tariff(subscription) -> bool:
    """Как Subscription.is_daily_tariff, но без ленивой загрузки tariff (в async она даёт MissingGreenlet):
    у ORM-объекта берём только уже загруженную связь."""
    try:
        state = sa_inspect(subscription)
    except NoInspectionAvailable:
        try:
            return bool(getattr(subscription, 'is_daily_tariff', False))
        except Exception:
            return False
    tariff = state.dict.get('tariff')
    return bool(tariff and getattr(tariff, 'is_daily', False))


def resolve_primary_action(user, subscription) -> str:
    if subscription is not None and not getattr(subscription, 'is_pending_trial', False):
        status = getattr(subscription, 'actual_status', None)
        is_trial = bool(getattr(subscription, 'is_trial', False))
        if status in _ACTIVE_STATUSES:
            # У суточного тарифа days_left всегда ≤ 1 — «заканчивается» он каждый день, продлевать нечего.
            expiring = getattr(subscription, 'days_left', EXPIRING_DAYS + 1) <= EXPIRING_DAYS
            if not is_trial and expiring and not _is_daily_tariff(subscription):
                return 'extend'
            return 'connect'
        if status in _ENDED_STATUSES and not is_trial:
            return 'extend'
    if user is not None and is_trial_available(user):
        return 'trial'
    return 'buy'


def _row(row_id: str, buttons: list[str], conditions: dict[str, Any] | None = None, max_per_row: int = 2):
    return {'id': row_id, 'buttons': buttons, 'conditions': conditions, 'max_per_row': max_per_row}


def build_fork_menu_config() -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_MENU_CONFIG)
    buttons = config['buttons']

    buttons['trial'].update(
        text={'ru': '🎁 Попробовать бесплатно', 'en': '🎁 Try for free'},
        action='trial_activate',
        conditions={'primary_action': 'trial'},
        style='success',
    )
    buttons['connect'].update(
        text={'ru': '🚀 Подключиться', 'en': '🚀 Connect'},
        conditions={'primary_action': 'connect', 'has_active_subscription': True, 'subscription_is_active': True},
        style='success',
    )
    buttons['connect_secondary'] = {
        **copy.deepcopy(buttons['connect']),
        'conditions': {'primary_action': 'extend', 'has_active_subscription': True, 'subscription_is_active': True},
        'style': 'primary',
    }
    buttons['extend'] = {
        'type': 'builtin',
        'builtin_id': 'extend',
        'text': {'ru': '🔄 Продлить', 'en': '🔄 Renew'},
        'action': 'subscription_extend',
        'enabled': True,
        'visibility': 'all',
        'conditions': {'primary_action': 'extend'},
        'dynamic_text': False,
        'style': 'success',
    }
    buttons['buy_primary'] = {
        **copy.deepcopy(buttons['buy_subscription']),
        'builtin_id': 'buy_primary',
        'text': {'ru': '💎 Выбрать тариф', 'en': '💎 Choose a plan'},
        'conditions': {'primary_action': 'buy'},
        'style': 'success',
    }
    buttons['buy_subscription'].update(
        text={'ru': '💎 Тарифы', 'en': '💎 Plans'},
        conditions={'show_buy': True, 'primary_action': ['trial', 'extend']},
        style='primary',
    )
    buttons['subscription']['style'] = 'primary'
    buttons['balance']['style'] = 'primary'
    # Без явного стиля таблица button_theme красит return_to_saved_cart в зелёный — вторая зелёная кнопка.
    buttons['resume_checkout']['style'] = 'primary'
    buttons['referrals']['text'] = {'ru': '👥 Друзья', 'en': '👥 Friends'}
    # Серые кнопки второго плана уходят в подменю «Ещё»: на главном экране остаются друзья и поддержка.
    buttons['more'] = {
        'type': 'builtin',
        'builtin_id': 'more',
        'text': {'ru': '➕ Ещё', 'en': '➕ More'},
        'action': 'menu_more',
        'enabled': True,
        'visibility': 'all',
        'conditions': None,
        'dynamic_text': False,
    }
    buttons['more_back'] = {
        'type': 'builtin',
        'builtin_id': 'more_back',
        'text': {'ru': '⬅️ Назад', 'en': '⬅️ Back'},
        'action': 'back_to_menu',
        'enabled': True,
        'visibility': 'all',
        'conditions': None,
        'dynamic_text': False,
    }

    by_id = {row['id']: row for row in config['rows']}
    config['rows'] = [
        _row('fork_primary_row', ['trial', 'extend', 'buy_primary'], max_per_row=1),
        _row('connect_row', ['connect', 'connect_secondary'], max_per_row=1),
        by_id['happ_row'],
        _row('subscription_balance_row', ['subscription', 'balance']),
        _row('tariffs_row', ['buy_subscription'], max_per_row=1),
        _row('buy_traffic_row', ['buy_traffic'], {'has_active_subscription': True}, max_per_row=1),
        by_id['simple_subscription_row'],
        by_id['resume_row'],
        _row('friends_support_more_row', ['referrals', 'support', 'more'], max_per_row=3),
        by_id['admin_row'],
        by_id['moderator_row'],
    ]
    config['more_rows'] = [
        _row('more_promo_info_row', ['promocode', 'info']),
        by_id['language_row'],
        by_id['contests_row'],
        _row('more_back_row', ['more_back'], max_per_row=1),
    ]
    return config
