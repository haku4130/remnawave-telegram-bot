"""Цвет инлайн-кнопки по её callback_data — для кнопок, которым код не задал style.

Смысл цветов один на весь бот: success — главное действие экрана (пробный,
подключиться, оплатить, продлить), primary — второстепенное, danger — отмена и
необратимое, навигация — без цвета. Первое совпавшее правило побеждает, поэтому
«назад» стоит раньше всего: back_to_tariffs не должен стать синим из-за «tariff».
Применяет PremiumThemeMiddleware; явный style= в коде таблица не перекрывает.
"""

from __future__ import annotations

import re

from aiogram.types import InlineKeyboardButton


# Подпись начинается со стрелки «назад» — это навигация, даже если callback ведёт в раздел.
# U+FE0F (вариационный селектор) не учитываем: «⬅️» и «⬅» — одна и та же стрелка.
_NAV_ARROWS = ('⬅', '◀', '↩', '🔙')
_VARIATION_SELECTOR = '\ufe0f'
_LEADING_TG_EMOJI_RE = re.compile(r'^\s*<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', re.DOTALL)
# Кнопки «вперёд» к оформлению: подпись может начинаться со стрелки, но это не возврат назад,
# и они должны оставаться зелёными главными действиями.
_FORWARD_CTA_CALLBACKS = frozenset({'return_to_saved_cart', 'subscription_resume_checkout', 'return_to_gift_cart'})


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


def _label_is_navigation(text: str | None) -> bool:
    if not text:
        return False
    head = text.strip()
    # Видимый символ первого <tg-emoji> — его подпись-запасной вариант.
    tag = _LEADING_TG_EMOJI_RE.match(head)
    if tag:
        head = tag.group(1) + head[tag.end() :]
    return head.replace(_VARIATION_SELECTOR, '').lstrip().startswith(_NAV_ARROWS)


def is_navigation(button: InlineKeyboardButton) -> bool:
    """«Назад», «в меню», стрелка в подписи и заглушки: такие кнопки без цвета и без анимированной иконки."""
    data = button.callback_data
    if data and (data in ('back', 'back_to_menu', 'noop') or data.startswith('back_')):
        return True
    if data in _FORWARD_CTA_CALLBACKS:
        return False
    return _label_is_navigation(button.text)


def style_for_button(button: InlineKeyboardButton) -> str | None:
    if is_navigation(button):
        return None
    data = button.callback_data
    if not data:
        return None
    for kind, pattern, style in _RULES:
        if _matches(kind, pattern, data):
            return style
    return None
