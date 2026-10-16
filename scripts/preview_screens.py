"""Предпросмотр оформления: шлёт ключевые экраны бота ОДНОМУ адресату.

Экраны собираются настоящими текстами, клавиатурами и PremiumThemeMiddleware на
фиктивных данных — пользователи ничего не видят. Главное меню уходит rich-сообщением
тем же путём, что у бота (без БД и без эффекта). Запуск на msk3 из копии ветки
в образе бота (токен и прокси — из прод-.env), см. задачи выкатки в плане:

    docker compose run --rm --no-deps \
      -v /root/preview-premium/app:/app/app:ro -v /root/preview-premium/scripts:/app/scripts:ro \
      bot python -m scripts.preview_screens --to <telegram_id> --lang ru
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import NamedTuple
from unittest.mock import MagicMock, patch

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, WebAppInfo

from app.bot_factory import create_bot
from app.config import settings
from app.handlers.subscription import devices, purchase, revoke
from app.keyboards.inline import (
    get_back_keyboard,
    get_change_devices_keyboard,
    get_confirm_change_devices_keyboard,
    get_devices_management_keyboard,
    get_insufficient_balance_keyboard,
    get_subscription_keyboard,
    get_updated_subscription_settings_keyboard,
)
from app.localization.emoji_catalog import CATALOG, tag
from app.localization.fork_text import fork_period
from app.localization.loader import clear_locale_cache
from app.localization.texts import get_texts
from app.services.menu_layout_service import MenuContext, MenuLayoutService
from app.utils import fork_onboarding, rich_menu
from app.utils.pagination import paginate_list


# Лимит Telegram на текст сообщения — 4096 символов; тег эмодзи длинный, поэтому
# лист каталога набираем с запасом.
_CATALOG_PAGE_BUDGET = 3800
_CATALOG_TITLE = 'Каталог эмодзи'
_NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)
# Баланс выдуманного подписчика: один и для текста меню, и для контекста клавиатуры.
_SUBSCRIBER_BALANCE_KOPEKS = 15000


class PreviewScreen(NamedTuple):
    title: str
    text: str
    markup: InlineKeyboardMarkup | None
    # rich=True — text это rich-HTML главного меню, уходит через sendRichMessage, как на проде.
    rich: bool = False
    # Клавиатура, на которую сразу после отправки меняют кнопки этого сообщения (editMessageReplyMarkup),
    # как делает обработчик «Ещё». Только для rich-экранов; ошибка Telegram роняет предпросмотр.
    then_markup: InlineKeyboardMarkup | None = None


def _catalog_screens() -> list[PreviewScreen]:
    pages: list[list[str]] = [[]]
    size = 0
    for name in CATALOG:
        line = f'{tag(name)} <code>{name}</code>'
        if pages[-1] and size + len(line) + 1 > _CATALOG_PAGE_BUDGET:
            pages.append([])
            size = 0
        pages[-1].append(line)
        size += len(line) + 1
    total = len(pages)
    screens = []
    for number, lines in enumerate(pages, start=1):
        title = f'{_CATALOG_TITLE} {number}/{total}'
        screens.append(PreviewScreen(title, f'<b>{title}</b>\n' + '\n'.join(lines), None))
    return screens


def _menu_keyboard(language: str, rows_key: str = 'rows', **context_fields) -> InlineKeyboardMarkup:
    config = MenuLayoutService.get_default_config()
    context = MenuContext(language=language, **context_fields)
    texts = get_texts(language)
    buttons_config = config.get('buttons', {})
    rows = []
    # Та же сборка, что в MenuLayoutService.build_keyboard, но по раскладке из кода (без БД).
    for row_config in config.get(rows_key, []):
        if not MenuLayoutService._evaluate_conditions(row_config.get('conditions'), context):
            continue
        row = []
        for button_id in row_config.get('buttons', []):
            cfg = buttons_config.get(button_id)
            if not cfg or not cfg.get('enabled', True):
                continue
            if not MenuLayoutService._check_visibility(cfg.get('visibility', 'all'), context):
                continue
            if not MenuLayoutService._evaluate_conditions(cfg.get('conditions'), context):
                continue
            button = MenuLayoutService._build_button(cfg, context, texts, button_id=button_id)
            if button:
                row.append(button)
        max_per_row = row_config.get('max_per_row', 2)
        rows.extend(row[i : i + max_per_row] for i in range(0, len(row), max_per_row))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _fake_subscription(days_left: int) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=1,
        actual_status='active',
        is_trial=False,
        # Час запаса: иначе к моменту подсчёта «осталось 2 дня» уже округлится до одного.
        end_date=now + timedelta(days=days_left, hours=1),
        start_date=now - timedelta(days=30 - days_left),
        # Без tariff_id rich-меню не ходит в БД за тарифом.
        tariff_id=None,
        tariff=SimpleNamespace(name='Стандарт'),
        traffic_used_gb=12.5,
        traffic_limit_gb=0,
        device_limit=3,
        subscription_url='https://example.com/sub',
        subscription_crypto_link=None,
    )


def _fake_user(language: str, subscription: SimpleNamespace | None, *, trial_used: bool) -> SimpleNamespace:
    name = 'Алексей' if language == 'ru' else 'Alex'
    return SimpleNamespace(
        id=0,
        username=None,
        first_name=name,
        last_name=None,
        full_name=name,
        language=language,
        auth_type='telegram',
        balance_kopeks=_SUBSCRIBER_BALANCE_KOPEKS if subscription else 0,
        subscription=subscription,
        subscriptions=[subscription] if subscription else [],
        is_trial_already_used=lambda: trial_used,
    )


@contextmanager
def _offline_menu_sources(user: SimpleNamespace) -> Iterator[None]:
    """Источники rich-меню, которым нужна БД, — заглушки: предпросмотр не подключается к базе."""

    async def nothing(*args, **kwargs):
        return None

    async def own_subscriptions(db, user_id):
        return list(user.subscriptions)

    with (
        patch.object(rich_menu, 'build_promo_offer_hint', nothing),
        patch.object(rich_menu, 'build_test_access_hint', nothing),
        patch.object(rich_menu, 'get_random_active_message', nothing),
        patch.object(rich_menu, 'get_all_subscriptions_by_user_id', own_subscriptions),
    ):
        yield


async def _rich_menu_screen(
    title: str,
    language: str,
    user: SimpleNamespace,
    keyboard: InlineKeyboardMarkup,
    then_markup: InlineKeyboardMarkup | None = None,
) -> PreviewScreen:
    with _offline_menu_sources(user):
        rich_html = await rich_menu.build_main_menu_rich_html(user, get_texts(language), None)
    return PreviewScreen(title, rich_html, keyboard, rich=True, then_markup=then_markup)


async def _stage_two_screens(language: str) -> list[PreviewScreen]:
    """Путь новичка: приветствие, «пробный включён», rich-меню в четырёх состояниях и подменю «Ещё»."""
    texts = get_texts(language)
    newcomer = SimpleNamespace(
        language=language,
        subscriptions=[],
        auth_type='telegram',
        is_trial_already_used=MagicMock(return_value=False),
    )
    tg_user = SimpleNamespace(first_name='Андрей' if language == 'ru' else 'Andrew', username=None)
    welcome_text, welcome_kb = fork_onboarding.build_welcome_screen(newcomer, tg_user)
    # Вариант приветствия зависит от настроек пробного на сервере — подпись берём по факту.
    with_trial = welcome_kb.inline_keyboard[0][0].callback_data == 'trial_activate'
    welcome_title = 'Приветствие — пробный' if with_trial else 'Приветствие — без пробного'
    connect_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.CONNECT_BUTTON, web_app=WebAppInfo(url='https://example.com/sub'))],
            [InlineKeyboardButton(text=texts.BACK_TO_MAIN_MENU_BUTTON, callback_data='back_to_menu')],
        ]
    )
    ready_text, ready_kb = fork_onboarding.build_trial_ready_screen(texts, '19.10.2026', connect_kb)
    # Баланс клавиатуры и текста меню один и тот же (см. _fake_user).
    active = {
        'has_active_subscription': True,
        'subscription_is_active': True,
        'balance_kopeks': _SUBSCRIBER_BALANCE_KOPEKS,
    }
    newcomer_menu = _fake_user(language, None, trial_used=False)
    subscriber = _fake_user(language, _fake_subscription(30), trial_used=True)
    expiring = _fake_user(language, _fake_subscription(2), trial_used=True)
    trial_was_used = _fake_user(language, None, trial_used=True)
    return [
        PreviewScreen(welcome_title, welcome_text, welcome_kb),
        PreviewScreen('Пробный включён', ready_text, ready_kb),
        await _rich_menu_screen(
            'Меню — пробный', language, newcomer_menu, _menu_keyboard(language, primary_action='trial')
        ),
        await _rich_menu_screen(
            'Меню — подключиться', language, subscriber, _menu_keyboard(language, primary_action='connect', **active)
        ),
        await _rich_menu_screen(
            'Меню — продлить', language, expiring, _menu_keyboard(language, primary_action='extend', **active)
        ),
        await _rich_menu_screen(
            'Меню — тариф', language, trial_was_used, _menu_keyboard(language, primary_action='buy')
        ),
        # Подменю «Ещё»: то же меню, что «подключиться», а сразу после отправки кнопки меняются на подменю —
        # так предпросмотр проверяет, что Telegram принимает editMessageReplyMarkup у rich-сообщения.
        await _rich_menu_screen(
            'Меню — Ещё',
            language,
            subscriber,
            _menu_keyboard(language, primary_action='connect', **active),
            then_markup=_menu_keyboard(language, rows_key='more_rows'),
        ),
    ]


def _subscription_screens(language: str) -> list[PreviewScreen]:
    """Экран «Подписка» (кнопка меню): активна, скоро истекает, подписки нет — тем же сборщиком, что у бота."""
    texts = get_texts(language)
    # Как прод-тариф: не суточный, 3 устройства, докупка устройства 55 ₽ — кнопка «Добавить устройство» видна.
    tariff = SimpleNamespace(
        name='Стандартный', is_daily=False, is_free=False, device_price_kopeks=5500, can_topup_traffic=lambda: False
    )
    devices = [{'platform': 'macOS', 'deviceModel': 'MacBook Air'}, {'platform': 'iOS', 'deviceModel': 'iPhone 15'}]
    screens = []
    for title, days_left in (('Подписка — активна', 30), ('Подписка — скоро истекает', 2)):
        subscription = _fake_subscription(days_left)
        subscription.tariff = tariff
        link = None if settings.should_hide_subscription_link() else subscription.subscription_url
        text = purchase._build_fork_subscription_text(
            texts,
            subscription,
            tariff=tariff,
            show_devices=settings.is_devices_selection_enabled(),
            devices_used=len(devices),
            devices=devices,
            subscription_link=link,
        )
        markup = get_subscription_keyboard(
            language, has_subscription=True, subscription=subscription, gift_enabled=True
        )
        screens.append(PreviewScreen(title, text, markup))
    screens.append(
        PreviewScreen(
            'Подписка — нет подписки',
            texts.FORK_SUB_NONE_SCREEN,
            get_subscription_keyboard(language, has_subscription=False, gift_enabled=True),
        )
    )
    return screens


def _device_screens(language: str) -> list[PreviewScreen]:
    """Настройки подписки, смена количества устройств, список устройств, перевыпуск — теми же сборщиками, что у бота."""
    texts = get_texts(language)
    price_per_month = 5500
    tariff = SimpleNamespace(
        name='Стандартный',
        is_daily=False,
        is_free=False,
        device_limit=3,
        max_device_limit=None,
        device_price_kopeks=price_per_month,
        can_topup_traffic=lambda: False,
    )
    subscription = _fake_subscription(30)
    subscription.tariff = tariff
    panel_devices = [
        {'platform': 'macOS', 'deviceModel': 'MacBook Air'},
        {'platform': 'iOS', 'deviceModel': 'iPhone 15'},
        {'platform': 'Android', 'deviceModel': 'Pixel 8'},
    ]
    days_left = 8
    price = price_per_month * days_left // 30
    period = fork_period(texts, days_left)
    extra_cost = texts.DEVICE_CHANGE_EXTRA_COST.format(amount=texts.format_price(price), period=period, months=period)
    pagination = paginate_list(panel_devices, page=1, per_page=5)
    end_date = datetime.now(UTC) + timedelta(days=days_left, hours=-1)
    missing = price - 100

    return [
        PreviewScreen(
            'Настройки подписки',
            purchase._build_fork_settings_text(texts, subscription, show_devices=True, devices_used=len(panel_devices)),
            get_updated_subscription_settings_keyboard(language, False, tariff=tariff, subscription=subscription),
        ),
        PreviewScreen(
            'Устройства — выбор количества',
            texts.CHANGE_DEVICES_PROMPT_TARIFF.format(
                current_devices=subscription.device_limit, price=texts.format_price(price_per_month)
            ),
            get_change_devices_keyboard(subscription.device_limit, language, end_date, 0, tariff=tariff),
        ),
        PreviewScreen(
            'Устройства — подтверждение (увеличить)',
            devices._build_fork_devices_confirm_text(texts, subscription.device_limit, 4, extra_cost),
            get_confirm_change_devices_keyboard(4, price, language),
        ),
        PreviewScreen(
            'Устройства — подтверждение (уменьшить)',
            devices._build_fork_devices_confirm_text(texts, 4, 3, texts.DEVICE_CHANGE_NO_REFUND)
            + texts.DEVICE_CHANGE_RESET_WARNING.format(connected=4, new=3),
            get_confirm_change_devices_keyboard(3, 0, language),
        ),
        PreviewScreen(
            'Устройства — недостаточно средств',
            texts.FORK_DEVICES_INSUFFICIENT.format(
                required=texts.FORK_DEVICES_COST_FOR.format(amount=texts.format_price(price), period=period),
                balance=texts.format_price(100),
                missing=texts.format_price(missing),
            ),
            get_insufficient_balance_keyboard(language, amount_kopeks=missing, has_saved_cart=True),
        ),
        PreviewScreen(
            'Устройства — готово',
            texts.DEVICE_CHANGE_INCREASE_SUCCESS
            + texts.DEVICE_CHANGE_RESULT_LINE.format(old=3, new=4)
            + texts.DEVICE_CHANGE_CHARGED.format(amount=texts.format_price(price)),
            get_back_keyboard(language),
        ),
        PreviewScreen(
            'Устройства — список',
            devices._build_fork_devices_text(texts, len(panel_devices), pagination.page, pagination.total_pages),
            get_devices_management_keyboard(pagination.items, pagination, language),
        ),
        PreviewScreen(
            'Перевыпуск — вопрос',
            texts.SUBSCRIPTION_REVOKE_WARNING,
            revoke._build_revoke_confirm_keyboard(language),
        ),
        PreviewScreen(
            'Перевыпуск — готово',
            texts.SUBSCRIPTION_REVOKE_SUCCESS,
            revoke._build_revoke_success_keyboard(language),
        ),
    ]


async def build_screens(language: str) -> list[PreviewScreen]:
    return [
        *_catalog_screens(),
        *(await _stage_two_screens(language) if settings.PREMIUM_THEME_ENABLED else []),
        *(_subscription_screens(language) if settings.PREMIUM_THEME_ENABLED else []),
        *(_device_screens(language) if settings.PREMIUM_THEME_ENABLED else []),
    ]


@contextmanager
def _without_server_locales() -> Iterator[None]:
    """Тексты — как после выкатки: без файлов LOCALES_PATH сервера.

    На msk3 LOCALES_PATH — нетронутый апрельский снимок апстрима, а он по порядку слияния
    (апстрим → форк → сервер) перекрывает слой форка. Ключи, которые в снимке есть, приходили
    апстримными (подвал меню «Choose an option:» с двоеточием), а более новые, которых там нет
    (MAIN_MENU_RICH_BALANCE), — форковыми. Выкатка заменяет снимок на {}; предпросмотр сразу
    показывает это состояние, а после выхода возвращает настройку и сбрасывает кэш локалей.
    """
    saved = settings.LOCALES_PATH
    with tempfile.TemporaryDirectory(prefix='preview-locales-') as empty_dir:
        settings.LOCALES_PATH = empty_dir
        clear_locale_cache()
        try:
            yield
        finally:
            settings.LOCALES_PATH = saved
            clear_locale_cache()


async def send_screens(chat_id: int, language: str) -> int:
    # Тексты форка читаются при включённой теме и кэшируются — сбрасываем кэш после включения.
    settings.PREMIUM_THEME_ENABLED = True
    # Эффект сообщения в предпросмотре не нужен — каждое меню взрывалось бы конфетти.
    settings.MAIN_MENU_RICH_EFFECT_ID = ''
    clear_locale_cache()
    bot = create_bot()
    sent = 0
    try:
        with _without_server_locales():
            for screen in await build_screens(language):
                await bot.send_message(chat_id, f'<i>{screen.title}</i>', link_preview_options=_NO_LINK_PREVIEW)
                if screen.rich:
                    # Тот же путь, что у бота: перенос кнопок в полотно, повтор без tg-time.
                    sent_message = await rich_menu._send_rich_menu(bot, chat_id, screen.text, screen.markup, language)
                    if screen.then_markup is not None:
                        await bot.edit_message_reply_markup(
                            chat_id=chat_id, message_id=sent_message.message_id, reply_markup=screen.then_markup
                        )
                else:
                    await bot.send_message(
                        chat_id, screen.text, reply_markup=screen.markup, link_preview_options=_NO_LINK_PREVIEW
                    )
                sent += 1
    finally:
        await bot.session.close()
    return sent


def main() -> None:
    parser = argparse.ArgumentParser(description='Предпросмотр экранов бота одному адресату')
    parser.add_argument('--to', type=int, required=True, help='telegram_id получателя (владелец)')
    parser.add_argument('--lang', default='ru', choices=('ru', 'en'))
    args = parser.parse_args()
    count = asyncio.run(send_screens(args.to, args.lang))
    print(f'Отправлено экранов: {count}')


if __name__ == '__main__':
    main()
