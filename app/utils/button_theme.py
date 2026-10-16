"""Цвет инлайн-кнопки по её callback_data — для кнопок, которым код не задал style.

Смысл цветов один на весь бот: success — главное действие экрана (пробный,
подключиться, оплатить, продлить), primary — второстепенное, danger — отмена и
необратимое, навигация — без цвета. Первое совпавшее правило побеждает, поэтому
«назад» стоит раньше всего: back_to_tariffs не должен стать синим из-за «tariff».
Применяет PremiumThemeMiddleware; явный style= в коде таблица не перекрывает.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton


# (вид совпадения, значение, стиль). Вид: exact | prefix | contains.
_RULES: tuple[tuple[str, str, str | None], ...] = (
    ('exact', 'back_to_menu', None),
    ('exact', 'back', None),
    ('prefix', 'back_', None),
    ('exact', 'noop', None),
    ('exact', 'rules_decline', 'danger'),
    ('exact', 'privacy_policy_decline', 'danger'),
    ('exact', 'gift_cancel', 'danger'),
    ('exact', 'gift_activation_cancel', 'danger'),
    ('exact', 'referral_withdrawal_cancel', 'danger'),
    ('prefix', 'cancel', 'danger'),
    ('contains', 'delete', 'danger'),
    ('exact', 'trial_activate', 'success'),
    ('exact', 'menu_trial', 'success'),
    ('exact', 'subscription_connect', 'success'),
    ('exact', 'open_subscription_link', 'success'),
    ('exact', 'subscription_extend', 'success'),
    ('exact', 'subscription_confirm', 'success'),
    ('exact', 'simple_subscription_pay_with_balance', 'success'),
    ('exact', 'return_to_saved_cart', 'success'),
    ('exact', 'subscription_resume_checkout', 'success'),
    ('exact', 'balance_topup', 'success'),
    ('exact', 'sub_channel_check', 'success'),
    ('exact', 'rules_accept', 'success'),
    ('exact', 'privacy_policy_accept', 'success'),
    ('prefix', 'tariff_confirm', 'success'),
    ('prefix', 'tariff_ext_confirm', 'success'),
    ('prefix', 'tariff_sw_confirm', 'success'),
    ('prefix', 'instant_sw_confirm', 'success'),
    ('prefix', 'daily_tariff_confirm', 'success'),
    ('prefix', 'daily_tariff_switch_confirm', 'success'),
    ('prefix', 'custom_confirm', 'success'),
    ('exact', 'menu_buy', 'primary'),
    ('exact', 'menu_subscription', 'primary'),
    ('exact', 'menu_balance', 'primary'),
)


def _matches(kind: str, pattern: str, data: str) -> bool:
    if kind == 'exact':
        return data == pattern
    if kind == 'prefix':
        return data.startswith(pattern)
    return pattern in data


def is_navigation(button: InlineKeyboardButton) -> bool:
    """«Назад», «в меню» и заглушки: такие кнопки без цвета и без анимированной иконки."""
    data = button.callback_data
    return bool(data) and (data in ('back', 'back_to_menu', 'noop') or data.startswith('back_'))


def style_for_button(button: InlineKeyboardButton) -> str | None:
    data = button.callback_data
    if not data:
        return None
    for kind, pattern, style in _RULES:
        if _matches(kind, pattern, data):
            return style
    return None
