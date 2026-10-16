"""Rich-меню с темой форка: без <h4>/<h6> (засечки на мобильном), реферальная подсказка."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.utils import rich_menu
from tests.utils.test_rich_menu import DummyTexts, _make_subscription, _make_user, _patch_content_sources


HINT = '<tg-emoji emoji-id="5372926953978341366">👥</tg-emoji> Пригласите друга — {percent}% с его оплат.'


class ForkTexts(DummyTexts):
    def get(self, key, default=None):
        return HINT if key == 'FORK_MENU_REFERRAL_HINT' else default


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
    yield
    rich_menu._reset_rich_menu_availability()


@pytest.mark.asyncio
async def test_rich_menu_has_no_serif_headings(theme):
    user = _make_user(_make_subscription(datetime.now(UTC)))
    html_out = await rich_menu.build_main_menu_rich_html(user, ForkTexts(), AsyncMock())
    assert '<h4>' not in html_out and '<h6>' not in html_out
    assert html_out.startswith('<p><b>Егор &lt;script&gt;</b></p>')
    assert '20%' in html_out
    assert '<tg-emoji emoji-id="5372926953978341366">' in html_out


@pytest.mark.asyncio
async def test_no_referral_hint_when_commission_zero(theme, monkeypatch):
    monkeypatch.setattr(settings, 'REFERRAL_COMMISSION_PERCENT', 0)
    user = _make_user(_make_subscription(datetime.now(UTC)))
    html_out = await rich_menu.build_main_menu_rich_html(user, ForkTexts(), AsyncMock())
    assert 'Пригласите друга' not in html_out
