"""Rich-меню с темой форка: без <h4>/<h6> (засечки на мобильном), реферальная подсказка,
блок подписки — таблица «ключ — значение» без цитаты, полоски и ссылки «Подключить»."""

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
    table = html_out.index('<table>')
    second_line = html_out.index('<hr/>', table)
    balance = html_out.index('Баланс:')
    hint = html_out.index('Пригласите друга')
    third_line = html_out.index('<hr/>', hint)
    footer = html_out.index('<footer>')
    assert header < first_line < table < second_line < balance < hint < third_line < footer
    assert html_out.count('<hr/>') == 3
    assert '<h4>' not in html_out and '<h6>' not in html_out


def _subscription_part(html_out: str) -> str:
    """Раздел подписки — между первой и второй линией."""
    first_line = html_out.index('<hr/>')
    return html_out[first_line : html_out.index('<hr/>', first_line + 1)]


def _rows(table_html: str) -> list[tuple[str, str]]:
    return re.findall(r'<tr><td>(.*?)</td><td>(.*?)</td></tr>', table_html)


@pytest.mark.asyncio
async def test_subscription_block_is_clean_table(theme):
    now = datetime.now(UTC)
    subscription = _make_subscription(now)
    subscription.traffic_limit_gb = 0
    html_out = await rich_menu.build_main_menu_rich_html(_make_user(subscription), get_texts('ru'), AsyncMock())
    part = _subscription_part(html_out)

    assert '<blockquote>' not in part
    assert '<code>[' not in part
    assert '📱' not in html_out and tag('phone') not in html_out
    assert 'sub.example.com' not in html_out
    assert '<a ' not in part
    assert part.count('<table>') == 1 and '<th>' not in part

    rows = _rows(part)
    assert [label for label, _ in rows] == ['Подписка', 'Действует до', 'Трафик', 'Устройства']
    values = dict(rows)
    assert values['Подписка'] == f'{tag("check")} Активна'
    end_date = subscription.end_date
    assert values['Действует до'] == (
        f'{format_local_datetime(end_date, "%d.%m.%Y")} · '
        f'<tg-time unix="{int(end_date.timestamp())}" format="r">11 дней</tg-time>'
    )
    assert values['Трафик'] == '12.5 ГБ из ∞'
    assert values['Устройства'] == '3'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('status', 'is_trial', 'days_left', 'expected'),
    [
        ('active', False, 12, ('check', 'Активна')),
        ('active', False, 2, ('hourglass', 'Скоро истекает')),
        ('trial', True, 10, ('gift', 'Пробная')),
        ('trial', True, 2, ('hourglass', 'Скоро истекает')),
        ('expired', False, -3, ('cross', 'Истекла')),
        ('limited', False, 12, ('warning', 'Лимит трафика')),
        ('disabled', False, 12, ('no_entry', 'Отключена')),
        ('pending', False, 12, ('hourglass', 'Ожидает')),
    ],
)
async def test_status_row_per_state(theme, status, is_trial, days_left, expected):
    subscription = _make_subscription(datetime.now(UTC), status=status, is_trial=is_trial, days_left=days_left)
    html_out = await rich_menu.build_main_menu_rich_html(_make_user(subscription), get_texts('ru'), AsyncMock())
    rows = _rows(_subscription_part(html_out))
    emoji, label = expected
    assert rows[0] == ('Подписка', f'{tag(emoji)} {label}')


@pytest.mark.asyncio
async def test_daily_tariff_is_not_expiring(theme):
    subscription = _make_subscription(datetime.now(UTC), days_left=1)
    subscription.is_daily_tariff = True
    html_out = await rich_menu.build_main_menu_rich_html(_make_user(subscription), get_texts('ru'), AsyncMock())
    assert _rows(_subscription_part(html_out))[0] == ('Подписка', f'{tag("check")} Активна')


@pytest.mark.asyncio
async def test_expired_subscription_shows_only_status_and_date(theme):
    subscription = _make_subscription(datetime.now(UTC), status='expired', days_left=-3)
    html_out = await rich_menu.build_main_menu_rich_html(_make_user(subscription), get_texts('ru'), AsyncMock())
    rows = _rows(_subscription_part(html_out))
    assert [label for label, _ in rows] == ['Подписка', 'Действует до']
    assert rows[1][1] == format_local_datetime(subscription.end_date, '%d.%m.%Y')


@pytest.mark.asyncio
async def test_rows_without_end_date_and_device_limit_are_omitted(theme):
    subscription = _make_subscription(datetime.now(UTC))
    subscription.end_date = None
    subscription.device_limit = None
    html_out = await rich_menu.build_main_menu_rich_html(_make_user(subscription), get_texts('ru'), AsyncMock())
    rows = _rows(_subscription_part(html_out))
    assert [label for label, _ in rows] == ['Подписка', 'Трафик']
    assert rows[1][1] == '12.5 ГБ из 100 ГБ'


@pytest.mark.asyncio
async def test_unlimited_devices_shown_as_infinity(theme):
    subscription = _make_subscription(datetime.now(UTC))
    subscription.device_limit = 0
    html_out = await rich_menu.build_main_menu_rich_html(_make_user(subscription), get_texts('ru'), AsyncMock())
    assert dict(_rows(_subscription_part(html_out)))['Устройства'] == '∞'


@pytest.mark.asyncio
async def test_english_table(theme):
    now = datetime.now(UTC)
    subscription = _make_subscription(now, days_left=2)
    subscription.traffic_used_gb = 12.04
    subscription.traffic_limit_gb = 0
    user = _make_user(subscription)
    user.language = 'en'
    html_out = await rich_menu.build_main_menu_rich_html(user, get_texts('en'), AsyncMock())
    rows = _rows(_subscription_part(html_out))
    assert [label for label, _ in rows] == ['Subscription', 'Valid until', 'Traffic', 'Devices']
    values = dict(rows)
    assert values['Subscription'] == f'{tag("hourglass")} Expires soon'
    assert values['Valid until'].endswith('format="r">1 day</tg-time>')
    assert values['Traffic'] == '12 GB of ∞'
    assert 'ГБ' not in html_out and 'безлимит' not in html_out


@pytest.mark.asyncio
@pytest.mark.parametrize(('language', 'expected'), [('ru', 'Подписки пока нет'), ('en', 'No subscription yet')])
async def test_no_subscription(theme, language, expected):
    html_out = await rich_menu.build_main_menu_rich_html(_make_user(None), get_texts(language), AsyncMock())
    part = _subscription_part(html_out)
    assert f'<p>{expected}</p>' in part
    assert '<table' not in part


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
