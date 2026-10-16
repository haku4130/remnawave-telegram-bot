"""/cabinet/branding/bot-logo через настоящий роутер: GET и HEAD.

Этот адрес rich-меню и rich-уведомления отдают Telegram как картинку шапки, и
Telegram скачивает её сам. Пока на HEAD эндпоинт отвечал 405 (был только GET),
rich-сообщения с логотипом отклонялись ошибкой RICH_MESSAGE_PHOTO_NO_MEDIA_FOUND,
хотя GET отдавал картинку. HEAD обязан вернуть то же, что GET, только без тела.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.cabinet.routes.branding import router as branding_router
from app.config import settings


URL = '/cabinet/branding/bot-logo'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


def _client() -> AsyncClient:
    app = FastAPI()
    app.include_router(branding_router, prefix='/cabinet')
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


@pytest.fixture
def bot_logo(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / 'vpn_logo.png'
    path.write_bytes(PNG)
    monkeypatch.setattr(settings, 'LOGO_FILE', str(path))
    return path


async def test_head_answers_like_get_without_body(bot_logo: Path) -> None:
    async with _client() as client:
        get = await client.get(URL)
        head = await client.head(URL)

    assert get.status_code == 200
    assert get.content == PNG

    assert head.status_code == 200
    assert head.content == b''
    for header in ('content-type', 'content-length', 'cache-control', 'etag', 'last-modified'):
        assert head.headers[header] == get.headers[header], header
    assert head.headers['content-type'] == 'image/png'
    assert head.headers['content-length'] == str(len(PNG))


async def test_head_without_logo_is_404_not_405(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'LOGO_FILE', str(tmp_path / 'missing.png'))

    async with _client() as client:
        head = await client.head(URL)

    assert head.status_code == 404
