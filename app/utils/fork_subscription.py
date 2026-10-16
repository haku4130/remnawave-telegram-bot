"""Статус подписки для экранов форка: одна и та же фраза в rich-меню и на экране «Подписка».

Фразы — ключи FORK_SUB_STATUS_* слоя форка, поэтому вызывать только при включённой теме.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.menu_layout.fork_layout import EXPIRING_DAYS, _is_daily_tariff


def _fork_status_label(subscription, texts) -> str:
    actual_status = (getattr(subscription, 'actual_status', None) or '').lower()
    if actual_status == 'limited':
        return texts.FORK_SUB_STATUS_LIMITED
    if actual_status == 'expired':
        return texts.FORK_SUB_STATUS_EXPIRED
    if actual_status == 'disabled':
        return texts.FORK_SUB_STATUS_DISABLED
    is_trial = bool(getattr(subscription, 'is_trial', False)) or actual_status == 'trial'
    if actual_status in {'active', 'trial'}:
        # У суточного тарифа дней всегда ≤ 1 — «скоро истекает» он каждый день, это шум.
        if _fork_days_left(subscription) <= EXPIRING_DAYS and not _is_daily_tariff(subscription):
            return texts.FORK_SUB_STATUS_EXPIRING
        return texts.FORK_SUB_STATUS_TRIAL if is_trial else texts.FORK_SUB_STATUS_ACTIVE
    return texts.FORK_SUB_STATUS_PENDING


def _fork_days_left(subscription) -> int:
    days_left = getattr(subscription, 'days_left', None)
    if isinstance(days_left, int):
        return days_left
    end_date = getattr(subscription, 'end_date', None)
    if not end_date:
        return 0
    return max((end_date - datetime.now(UTC)).days, 0)
