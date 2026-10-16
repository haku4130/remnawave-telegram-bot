"""Цвет кнопки по смыслу: главное действие — зелёный, второстепенное — синий,
отмена — красный, навигация — без цвета. Явный style в коде сюда не доходит
(его не трогает middleware), тут только правила таблицы."""

import pytest
from aiogram.types import InlineKeyboardButton, WebAppInfo

from app.utils.button_theme import style_for_button


def _cb(data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text='x', callback_data=data)


@pytest.mark.parametrize(
    ('callback_data', 'expected'),
    [
        ('trial_activate', 'success'),
        ('menu_trial', 'success'),
        ('subscription_connect', 'success'),
        ('subscription_extend', 'success'),
        ('subscription_confirm', 'success'),
        ('tariff_confirm:3:30', 'success'),
        ('tariff_ext_confirm:3:30', 'success'),
        ('balance_topup', 'success'),
        ('rules_accept', 'success'),
        ('menu_buy', 'primary'),
        ('menu_subscription', 'primary'),
        ('menu_balance', 'primary'),
        ('rules_decline', 'danger'),
        ('gift_cancel', 'danger'),
        ('cancel_rename_5', 'danger'),
        ('admin_user_delete_5', 'danger'),
        ('back_to_menu', None),
        ('back_to_tariffs', None),
        ('noop', None),
        ('menu_info', None),
        ('something_unknown', None),
    ],
)
def test_callback_rules(callback_data, expected):
    assert style_for_button(_cb(callback_data)) == expected


def test_non_callback_buttons_have_no_theme_color():
    assert style_for_button(InlineKeyboardButton(text='x', url='https://example.com')) is None
    assert style_for_button(InlineKeyboardButton(text='x', web_app=WebAppInfo(url='https://example.com'))) is None
