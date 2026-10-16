"""Предпросмотр оформления: шлёт ключевые экраны бота ОДНОМУ адресату.

Экраны собираются настоящими текстами, клавиатурами и PremiumThemeMiddleware на
фиктивных данных — пользователи ничего не видят. Запуск на msk3 из копии ветки
в образе бота (токен и прокси — из прод-.env), см. задачи выкатки в плане:

    docker compose run --rm --no-deps \
      -v /root/preview-premium/app:/app/app:ro -v /root/preview-premium/scripts:/app/scripts:ro \
      bot python -m scripts.preview_screens --to <telegram_id> --lang ru
"""

from __future__ import annotations

import argparse
import asyncio
from typing import NamedTuple

from aiogram.types import InlineKeyboardMarkup

from app.bot_factory import create_bot
from app.config import settings
from app.localization.emoji_catalog import CATALOG, tag
from app.localization.loader import clear_locale_cache
from app.localization.texts import get_texts
from app.services.menu_layout_service import MenuContext, MenuLayoutService


# Лимит Telegram на текст сообщения — 4096 символов; тег эмодзи длинный, поэтому
# лист каталога набираем с запасом.
_CATALOG_PAGE_BUDGET = 3800
_CATALOG_TITLE = 'Каталог эмодзи'


class PreviewScreen(NamedTuple):
    title: str
    text: str
    markup: InlineKeyboardMarkup | None


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


def _menu_keyboard(language: str, **context_fields) -> InlineKeyboardMarkup:
    config = MenuLayoutService.get_default_config()
    context = MenuContext(language=language, **context_fields)
    texts = get_texts(language)
    buttons_config = config.get('buttons', {})
    rows = []
    # Та же сборка, что в MenuLayoutService.build_keyboard, но по раскладке из кода (без БД).
    for row_config in config.get('rows', []):
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


def _menu_text(language: str, subscription_status: str) -> str:
    return get_texts(language).MAIN_MENU.format(user_name='Алексей', subscription_status=subscription_status)


def build_screens(language: str) -> list[PreviewScreen]:
    texts = get_texts(language)
    return [
        *_catalog_screens(),
        PreviewScreen(
            'Главное меню — новичок',
            _menu_text(language, texts.t('SUB_STATUS_NONE', '❌ Отсутствует')),
            _menu_keyboard(language, balance_kopeks=0),
        ),
        PreviewScreen(
            'Главное меню — подписчик',
            _menu_text(
                language, texts.t('SUB_STATUS_ACTIVE_LONG', '💎 Активна').format(end_date='15.11.2026', days=30)
            ),
            _menu_keyboard(language, has_active_subscription=True, subscription_is_active=True, balance_kopeks=15000),
        ),
    ]


async def send_screens(chat_id: int, language: str) -> int:
    # Тексты форка читаются при включённой теме и кэшируются — сбрасываем кэш после включения.
    settings.PREMIUM_THEME_ENABLED = True
    clear_locale_cache()
    bot = create_bot()
    sent = 0
    try:
        for screen in build_screens(language):
            await bot.send_message(chat_id, f'<i>{screen.title}</i>')
            await bot.send_message(chat_id, screen.text, reply_markup=screen.markup)
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
