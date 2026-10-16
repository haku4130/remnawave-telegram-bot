"""Цвет кнопки по смыслу: главное действие — зелёный, второстепенное — синий,
отмена — красный, навигация — без цвета. Явный style в коде сюда не доходит
(его не трогает middleware), тут только правила таблицы."""

import pytest
from aiogram.types import InlineKeyboardButton, WebAppInfo

from app.utils.button_theme import is_navigation, style_for_button


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


def test_backup_callbacks_are_not_navigation():
    from app.utils.button_theme import is_navigation

    button = _cb('backup_delete_confirm_1')
    assert style_for_button(button) == 'danger'
    assert is_navigation(button) is False


@pytest.mark.parametrize('callback_data', ['back', 'back_to_menu', 'back_to_tariffs', 'noop'])
def test_is_navigation(callback_data):
    assert is_navigation(_cb(callback_data)) is True


@pytest.mark.parametrize(
    ('text', 'callback_data'),
    [
        ('⬅️ Назад', 'menu_subscription'),
        ('⬅ Назад', 'menu_buy'),
        ('◀️ Назад', 'menu_balance'),
        ('↩️ Вернуться', 'subscription_settings'),
        ('🔙 Back', 'balance_topup'),
        ('<tg-emoji emoji-id="5312486108309757006">⬅️</tg-emoji> Назад', 'menu_subscription'),
    ],
)
def test_arrow_label_is_navigation_whatever_the_callback(text, callback_data):
    button = InlineKeyboardButton(text=text, callback_data=callback_data)
    assert is_navigation(button) is True
    assert style_for_button(button) is None


@pytest.mark.parametrize(
    'text',
    ['Назад', '🏠 Главное меню', '🚀 Подключиться'],
)
def test_label_without_arrow_is_not_navigation(text):
    button = InlineKeyboardButton(text=text, callback_data='menu_subscription')
    assert is_navigation(button) is False
    assert style_for_button(button) == 'primary'


def test_regular_label_keeps_callback_colour():
    button = InlineKeyboardButton(text='🚀 Подключиться', callback_data='subscription_connect')
    assert is_navigation(button) is False
    assert style_for_button(button) == 'success'


@pytest.mark.parametrize(
    ('callback_data', 'expected_style'),
    [('return_to_saved_cart', 'success'), ('subscription_resume_checkout', 'success')],
)
def test_forward_checkout_with_arrow_label_keeps_colour(callback_data, expected_style):
    button = InlineKeyboardButton(text='⬅️ Вернуться к оформлению подписки', callback_data=callback_data)
    assert is_navigation(button) is False
    assert style_for_button(button) == expected_style


def test_gift_return_with_arrow_label_is_not_navigation():
    button = InlineKeyboardButton(text='⬅️ Вернуться к подарку', callback_data='return_to_gift_cart')
    assert is_navigation(button) is False
