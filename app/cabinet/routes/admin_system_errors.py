"""Admin system errors routes — журнал ошибок приложения и их доставки.

Второй, независимый от Telegram канал: кабинет живёт на том же хосте, что и
бот, и доступен напрямую, минуя прокси-пул. Поэтому когда все пути до
Telegram лежат, ошибки всё равно видно здесь.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.system_errors import (
    get_error_event,
    get_error_summary,
    list_error_events,
)
from app.database.models import User

from ..dependencies import get_cabinet_db, require_permission


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/admin/system-errors', tags=['Admin System Errors'])


# ============ Schemas ============


class SystemErrorListItem(BaseModel):
    """Строка списка — без трейсбека, чтобы не раздувать ответ."""

    id: int
    created_at: datetime | None = None
    level: str
    logger_name: str | None = None
    event: str
    error_type: str | None = None
    user_id: int | None = None
    delivery_status: str
    delivery_attempts: int
    delivered_at: datetime | None = None
    has_traceback: bool = False


class SystemErrorDetail(SystemErrorListItem):
    """Полная запись, включая трейсбек и контекст."""

    traceback: str | None = None
    context: dict[str, Any] | None = None
    last_attempt_at: datetime | None = None
    delivery_error: str | None = None
    dedup_hash: str | None = None


class SystemErrorListResponse(BaseModel):
    items: list[SystemErrorListItem]
    total: int
    limit: int
    offset: int


class SystemErrorSummary(BaseModel):
    undelivered_total: int
    last_24h: int
    last_7d: int
    by_status_7d: dict[str, int]
    top_errors_7d: list[dict[str, Any]]


# ============ Helpers ============


def _to_list_item(event) -> SystemErrorListItem:
    return SystemErrorListItem(
        id=event.id,
        created_at=event.created_at,
        level=event.level,
        logger_name=event.logger_name,
        event=event.event,
        error_type=event.error_type,
        user_id=event.user_id,
        delivery_status=event.delivery_status,
        delivery_attempts=event.delivery_attempts,
        delivered_at=event.delivered_at,
        has_traceback=bool(event.traceback),
    )


# ============ Routes ============


@router.get('/summary', response_model=SystemErrorSummary)
async def system_errors_summary(
    admin: User = Depends(require_permission('system_errors:read')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Сводка для бейджа в шапке и верхнего блока страницы."""
    summary = await get_error_summary(db)
    return SystemErrorSummary(**summary)


@router.get('', response_model=SystemErrorListResponse)
async def list_system_errors(
    admin: User = Depends(require_permission('system_errors:read')),
    db: AsyncSession = Depends(get_cabinet_db),
    level: str | None = Query(default=None),
    delivery_status: str | None = Query(default=None),
    logger_name: str | None = Query(default=None),
    search: str | None = Query(default=None),
    undelivered_only: bool = Query(default=False),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Список ошибок с фильтрами по уровню, статусу доставки и периоду."""
    events, total = await list_error_events(
        db,
        level=level,
        delivery_status=delivery_status,
        logger_name=logger_name,
        search=search,
        undelivered_only=undelivered_only,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )

    return SystemErrorListResponse(
        items=[_to_list_item(event) for event in events],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get('/{event_id}', response_model=SystemErrorDetail)
async def get_system_error(
    event_id: int,
    admin: User = Depends(require_permission('system_errors:read')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Полная запись с трейсбеком и контекстом."""
    event = await get_error_event(db, event_id)
    if not event:
        raise HTTPException(status_code=404, detail='Error event not found')

    base = _to_list_item(event)
    return SystemErrorDetail(
        **base.model_dump(),
        traceback=event.traceback,
        context=event.context,
        last_attempt_at=event.last_attempt_at,
        delivery_error=event.delivery_error,
        dedup_hash=event.dedup_hash,
    )
