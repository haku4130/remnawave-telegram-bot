"""Rich-меню с темой форка: без <h4>/<h6> (засечки на мобильном), реферальная подсказка,
блок подписки — три короткие строки без таблицы, цитаты, полоски и ссылки «Подключить»."""

import re
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.localization import loader
from app.localization.emoji_catalog import tag
from app.localization.texts import get_texts
from app.utils import rich_menu
from app.utils.timezone import format_local_datetime
from tests.utils.test_rich_menu import DummyTexts, _make_subscription, _make_user, _patch_content_sources


@pytest.fixture
def theme(monkeypatch):
    rich_menu._reset_rich_menu_availability()
    monkeypatch.setattr(settings, 'MAIN_MENU_RICH_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'MAIN_MENU_RICH_LOGO_URL', '', raising=False)
    monkeypatch.setattr(settings, 'WEBHOOK_URL', None, raising=False)
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    monkeypatch.setattr(settings, 'REFERRAL_COMMISSION_PERCENT', 20)
    monkeypatch.setattr(type(settings), 'is_referral_program_enabled', lambda self: True)
    monkeypatch.setattr(type(settings), 'is_multi_tariff_enabled', lambda self: False)
    monkeypatch.setattr(type(settings), 'is_tariffs_mode', lambda self: False)
    _patch_content_sources(monkeypatch)
    loader.clear_locale_cache()
    yield
    loader.clear_locale_cache()
    rich_menu._reset_rich_menu_availability()


@pytest.mark.asyncio
async def test_rich_menu_has_no_serif_headings(theme):
    user = _make_user(_make_subscription(datetime.now(UTC)))
    html_out = await rich_menu.build_main_menu_rich_html(user, get_texts('ru'), AsyncMock())
    assert '<h4>' not in html_out and '<h6>' not in html_out
    assert html_out.startswith('<p><b>Егор &lt;script&gt;</b></p>')
    assert '20%' in html_out
    assert '<tg-emoji emoji-id="5372926953978341366">' in html_out


@pytest.mark.asyncio
async def test_no_referral_hint_when_commission_zero(theme, monkeypatch):
    monkeypatch.setattr(settings, 'REFERRAL_COMMISSION_PERCENT', 0)
    user = _make_user(_make_subscription(datetime.now(UTC)))
    html_out = await rich_menu.build_main_menu_rich_html(user, get_texts('ru'), AsyncMock())
    assert 'Пригласите друга' not in html_out


@pytest.mark.asyncio
async def test_rich_menu_sections_separated_by_lines(theme):
    user = _make_user(_make_subscription(datetime.now(UTC)))
    html_out = await rich_menu.build_main_menu_rich_html(user, get_texts('ru'), AsyncMock())
    header = html_out.index('<p><b>Егор &lt;script&gt;</b></p>')
    first_line = html_out.index('<hr/>', header)
    block = html_out.index('<p><b>')
    block = html_out.index('<p><b>', block + 1)
    second_line = html_out.index('<hr/>', block)
    balance = html_out.index('Баланс:')
    hint = html_out.index('Пригласите друга')
    third_line = html_out.index('<hr/>', hint)
    footer = html_out.index('<footer>')
    assert header < first_line < block < second_line < balance < hint < third_line < footer
    assert html_out.count('<hr/>') == 3
    assert '<h4>' not in html_out and '<h6>' not in html_out


def _subscription_part(html_out: str) -> str:
    """Раздел подписки — между первой и второй линией."""
    first_line = html_out.index('<hr/>')
    return html_out[first_line : html_out.index('<hr/>', first_line + 1)]


def _lines(part: str) -> list[str]:
    """Строки блока подписки: один абзац, строки через <br>."""
    match = re.fullmatch(r'<hr/>(<p>.*?</p>)', part)
    assert match, part
    paragraph = match.group(1)
    assert paragraph.startswith('<p><b>') and paragraph.endswith('</p>')
    return paragraph[len('<p>') : -len('</p>')].split('<br>')


@pytest.mark.asyncio
async def test_subscription_block_is_three_short_lines(theme):
    now = datetime.now(UTC)
    subscription = _make_subscription(now)
    subscription.traffic_limit_gb = 0
    html_out = await rich_menu.build_main_menu_rich_html(_make_user(subscription), get_texts('ru'), AsyncMock())
    part = _subscription_part(html_out)

    assert '<table' not in html_out
    assert '<blockquote>' not in part
    assert '<code>[' not in part
    assert '📱' not in html_out and tag('phone') not in html_out
    assert 'sub.example.com' not in html_out
    assert '<a ' not in part

    end_date = subscription.end_date
    assert _lines(part) == [
        f'<b>{tag("check")} Подписка активна</b>',
        (
            f'До {format_local_datetime(end_date, "%d.%m.%Y")} · '
            f'<tg-time unix="{int(end_date.timestamp())}" format="r">11 дней</tg-time>'
        ),
        'Трафик: 12.5 ГБ из ∞ · Устройства: 3',
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('status', 'is_trial', 'days_left', 'expected'),
    [
        ('active', False, 12, ('check', 'Подписка активна')),
        ('active', False, 2, ('hourglass', 'Подписка скоро истекает')),
        ('trial', True, 10, ('gift', 'Пробный доступ')),
        # Пробный (на проде 3 дня) — «Пробный доступ» до последних суток, иначе «скоро истекает» с первой минуты.
        ('trial', True, 3, ('gift', 'Пробный доступ')),
        ('trial', True, 2, ('gift', 'Пробный доступ')),
        # end_date = now + 1 день: к подсчёту остаётся чуть меньше суток — последние сутки, «скоро истекает».
        ('trial', True, 1, ('hourglass', 'Подписка скоро истекает')),
        ('trial', True, 0, ('hourglass', 'Подписка скоро истекает')),
        ('active', True, 2, ('gift', 'Пробный доступ')),
        ('active', False, 3, ('hourglass', 'Подписка скоро истекает')),
        ('expired', False, -3, ('cross', 'Подписка истекла')),
        ('limited', False, 12, ('warning', 'Закончился трафик')),
        ('disabled', False, 12, ('no_entry', 'Подписка отключена')),
        ('pending', False, 12, ('hourglass', 'Подписка ожидает активации')),
    ],
)
async def test_status_line_per_state(theme, status, is_trial, days_left, expected):
    subscription = _make_subscription(datetime.now(UTC), status=status, is_trial=is_trial, days_left=days_left)
    html_out = await rich_menu.build_main_menu_rich_html(_make_user(subscription), get_texts('ru'), AsyncMock())
    emoji, label = expected
    assert _lines(_subscription_part(html_out))[0] == f'<b>{tag(emoji)} {label}</b>'


@pytest.mark.asyncio
async def test_daily_tariff_is_not_expiring(theme):
    subscription = _make_subscription(datetime.now(UTC), days_left=1)
    subscription.is_daily_tariff = True
    html_out = await rich_menu.build_main_menu_rich_html(_make_user(subscription), get_texts('ru'), AsyncMock())
    assert _lines(_subscription_part(html_out))[0] == f'<b>{tag("check")} Подписка активна</b>'


@pytest.mark.asyncio
async def test_expired_subscription_shows_only_status_and_date(theme):
    subscription = _make_subscription(datetime.now(UTC), status='expired', days_left=-3)
    html_out = await rich_menu.build_main_menu_rich_html(_make_user(subscription), get_texts('ru'), AsyncMock())
    assert _lines(_subscription_part(html_out)) == [
        f'<b>{tag("cross")} Подписка истекла</b>',
        f'До {format_local_datetime(subscription.end_date, "%d.%m.%Y")}',
    ]


@pytest.mark.asyncio
async def test_lines_without_end_date_and_device_limit_are_omitted(theme):
    subscription = _make_subscription(datetime.now(UTC), days_left=12)
    subscription.end_date = None
    # У настоящей модели days_left — свойство; без end_date фейку нужен явный запас.
    subscription.days_left = 12
    subscription.device_limit = None
    html_out = await rich_menu.build_main_menu_rich_html(_make_user(subscription), get_texts('ru'), AsyncMock())
    assert _lines(_subscription_part(html_out)) == [
        f'<b>{tag("check")} Подписка активна</b>',
        'Трафик: 12.5 ГБ из 100 ГБ',
    ]


@pytest.mark.asyncio
async def test_usage_line_omitted_when_subscription_not_active(theme):
    subscription = _make_subscription(datetime.now(UTC), status='disabled')
    html_out = await rich_menu.build_main_menu_rich_html(_make_user(subscription), get_texts('ru'), AsyncMock())
    lines = _lines(_subscription_part(html_out))
    assert len(lines) == 2 and lines[1].startswith('До ')


@pytest.mark.asyncio
async def test_unlimited_devices_shown_as_infinity(theme):
    subscription = _make_subscription(datetime.now(UTC))
    subscription.device_limit = 0
    html_out = await rich_menu.build_main_menu_rich_html(_make_user(subscription), get_texts('ru'), AsyncMock())
    assert _lines(_subscription_part(html_out))[-1].endswith(' · Устройства: ∞')


@pytest.mark.asyncio
async def test_english_block(theme):
    now = datetime.now(UTC)
    subscription = _make_subscription(now, days_left=2)
    subscription.traffic_used_gb = 12.04
    subscription.traffic_limit_gb = 0
    user = _make_user(subscription)
    user.language = 'en'
    html_out = await rich_menu.build_main_menu_rich_html(user, get_texts('en'), AsyncMock())
    lines = _lines(_subscription_part(html_out))
    assert lines[0] == f'<b>{tag("hourglass")} Subscription expires soon</b>'
    assert lines[1].startswith('Until ') and lines[1].endswith('format="r">1 day</tg-time>')
    assert lines[2] == 'Traffic: 12 GB of ∞ · Devices: 3'
    assert 'ГБ' not in html_out and 'безлимит' not in html_out


@pytest.mark.asyncio
@pytest.mark.parametrize(('language', 'expected'), [('ru', 'Подписки пока нет'), ('en', 'No subscription yet')])
async def test_no_subscription(theme, language, expected):
    html_out = await rich_menu.build_main_menu_rich_html(_make_user(None), get_texts(language), AsyncMock())
    part = _subscription_part(html_out)
    assert f'<p>{expected}</p>' in part
    assert '<table' not in html_out


@pytest.mark.asyncio
@pytest.mark.parametrize(('language', 'expected'), [('ru', 'Выберите действие'), ('en', 'Choose an option')])
async def test_footer_text_comes_from_fork(theme, language, expected):
    user = _make_user(_make_subscription(datetime.now(UTC)))
    html_out = await rich_menu.build_main_menu_rich_html(user, get_texts(language), AsyncMock())
    assert html_out.endswith(f'<hr/><footer>{expected}</footer>')


@pytest.mark.asyncio
async def test_theme_off_keeps_upstream_block(theme, monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    user = _make_user(_make_subscription(datetime.now(UTC)))
    html_out = await rich_menu.build_main_menu_rich_html(user, DummyTexts(), AsyncMock())
    assert '<h6>📱 Подписка</h6><blockquote>' in html_out
    assert '<code>[' in html_out
    assert 'sub.example.com' in html_out
