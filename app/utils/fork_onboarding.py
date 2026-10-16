"""Путь новичка форка: автоязык, доступность пробного, экраны приветствия и пробного.

Экраны используются только при PREMIUM_THEME_ENABLED: их тексты — ключи FORK_*
из app/localization/fork/ (читаются атрибутом, см. test_locale_integrity).
"""

from __future__ import annotations

import html

import structlog
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.database.crud.rules import get_current_rules_content
from app.localization.texts import get_rules, get_texts
from app.utils.telegram_html import stored_html_to_telegram_pages


logger = structlog.get_logger(__name__)

# Telegram отдаёт язык интерфейса клиента. Русскоязычной считаем и аудиторию с
# украинским, белорусским и казахским интерфейсом — ua-локаль форк не ведёт.
_RUSSIAN_SPEAKING = frozenset({'ru', 'uk', 'be', 'kk'})


def resolve_auto_language(language_code: str | None, available: list[str]) -> str | None:
    if not language_code:
        return None
    base = language_code.split('-')[0].lower()
    wanted = 'ru' if base in _RUSSIAN_SPEAKING else 'en'
    normalized = {code.lower() for code in available}
    return wanted if wanted in normalized else None


def is_trial_available(user) -> bool:
    """Бесплатный пробный, который пользователь может включить прямо сейчас."""
    if settings.TRIAL_DURATION_DAYS <= 0 or settings.TRIAL_DISABLED_FOR == 'all':
        return False
    if settings.is_trial_paid_activation_enabled():
        return False
    try:
        if settings.is_trial_disabled_for_user(getattr(user, 'auth_type', 'telegram')):
            return False
        return not user.is_trial_already_used()
    except Exception as error:
        logger.warning('Не удалось проверить доступность пробного', error=str(error))
        return False


def format_days(texts, days: int) -> str:
    """«3 дня» / «5 дней» / «3 days» по ключам FORK_DAYS_*."""
    if getattr(texts, 'language', 'ru') == 'ru':
        last_two, last = days % 100, days % 10
        if last == 1 and last_two != 11:
            template = texts.FORK_DAYS_ONE
        elif 2 <= last <= 4 and not 12 <= last_two <= 14:
            template = texts.FORK_DAYS_FEW
        else:
            template = texts.FORK_DAYS_MANY
    else:
        template = texts.FORK_DAYS_ONE if days == 1 else texts.FORK_DAYS_MANY
    return template.format(n=days)


def rules_url() -> str:
    username = settings.get_bot_username()
    return f'https://t.me/{username}?start=rules' if username else ''


async def send_rules_screen(message, db, language: str, *, with_menu_button: bool) -> None:
    """Правила сервиса по ссылке из приветствия. Новичку кнопки не нужно — приветствие
    с кнопкой пробного остаётся сообщением выше; зарегистрированному — «В меню»."""
    texts = get_texts(language)
    rules_text = await get_current_rules_content(db, language) or await get_rules(language)
    pages = stored_html_to_telegram_pages(rules_text, max_length=3500) or ['']
    header = texts.t('RULES_HEADER', '📋 <b>Правила сервиса</b>')
    for index, page in enumerate(pages):
        text = f'{header}\n\n{page}' if index == 0 else page
        if index == len(pages) - 1 and with_menu_button:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=texts.t('BACK_TO_MAIN_MENU_BUTTON', '⬅️ В главное меню'),
                            callback_data='back_to_menu',
                        )
                    ]
                ]
            )
            await message.answer(text, reply_markup=keyboard)
        else:
            await message.answer(text)


def _user_name(tg_user) -> str:
    name = getattr(tg_user, 'first_name', None) or getattr(tg_user, 'username', None) or ''
    return html.escape(name)


def _button(text: str, callback_data: str, style: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, callback_data=callback_data, style=style)]


def build_welcome_screen(user, tg_user) -> tuple[str, InlineKeyboardMarkup]:
    """Приветствие нового пользователя: пробный одной зелёной кнопкой, правила — ссылкой строкой."""
    texts = get_texts(getattr(user, 'language', None) or settings.DEFAULT_LANGUAGE)
    user_name = _user_name(tg_user)
    url = rules_url()
    has_active = any(getattr(sub, 'is_active', False) for sub in (getattr(user, 'subscriptions', None) or []))

    if has_active:
        text = texts.FORK_WELCOME_HAS_SUBSCRIPTION.format(user_name=user_name)
        rows = [_button(texts.FORK_BUTTON_CONNECT, 'subscription_connect', 'success')]
    elif is_trial_available(user):
        text = texts.FORK_WELCOME_TRIAL.format(
            user_name=user_name, trial_days=format_days(texts, settings.TRIAL_DURATION_DAYS), rules_url=url
        )
        rows = [
            _button(texts.FORK_BUTTON_TRY_FREE, 'trial_activate', 'success'),
            _button(texts.FORK_BUTTON_TARIFFS, 'menu_buy', 'primary'),
        ]
    else:
        text = texts.FORK_WELCOME_NO_TRIAL.format(user_name=user_name, rules_url=url)
        rows = [_button(texts.FORK_BUTTON_CHOOSE_TARIFF, 'menu_buy', 'success')]

    if not url and 'href=""' in text:
        # Без username бота ссылку на правила не построить — убираем последний абзац целиком.
        text = text.rsplit('\n\n', 1)[0]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)
