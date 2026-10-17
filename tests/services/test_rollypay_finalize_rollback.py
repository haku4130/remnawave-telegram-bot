"""Зачисление RollyPay переживает откат сессии в автопокупке после пополнения.

Автопокупка (``send_cart_notification_after_topup``) работает на той же сессии и
в своих ветках ошибок делает ``db.rollback()``. Откат истекает все объекты
сессии, и чтение их полей в async-сессии падает (MissingGreenlet). Отметка
``balance_credited`` раньше писалась ПОСЛЕ автопокупки и в этом случае терялась,
а обработчик вебхука отвечал провайдеру ошибкой. Теперь отметка уходит одним
коммитом с начислением.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.database.models import (
    PromoGroup,
    RollyPayPayment,
    Subscription,
    Tariff,
    Transaction,
    User,
    UserPromoGroup,
)
from app.services.payment_service import PaymentService
from tests.fixtures.sqlite_memory import memory_session


_TABLES = [
    PromoGroup.__table__,
    Tariff.__table__,
    User.__table__,
    UserPromoGroup.__table__,
    Subscription.__table__,
    Transaction.__table__,
    RollyPayPayment.__table__,
]

_AMOUNT = 70_000_00
_START_BALANCE = 500_00


async def _seed(db) -> tuple[int, str]:
    user = User(telegram_id=777000111, username='buyer', balance_kopeks=_START_BALANCE, has_made_first_topup=True)
    db.add(user)
    await db.flush()
    payment = RollyPayPayment(
        user_id=user.id,
        order_id='rp_order_1',
        rollypay_payment_id='pay_uuid_1',
        amount_kopeks=_AMOUNT,
        status='pending',
        is_paid=False,
        metadata_json={'source': 'bot'},
    )
    db.add(payment)
    await db.commit()
    return user.id, payment.order_id


def _webhook_payload(order_id: str) -> dict:
    return {
        'event_type': 'payment.paid',
        'payment_id': 'pay_uuid_1',
        'order_id': order_id,
        'status': 'paid',
        'amount': f'{_AMOUNT / 100:.2f}',
        'currency': 'RUB',
    }


def _make_service(events: list[str]) -> PaymentService:
    service = PaymentService.__new__(PaymentService)  # type: ignore[call-arg]
    bot = MagicMock()

    async def _send_message(*args, **kwargs):
        events.append('success_message')

    bot.send_message = AsyncMock(side_effect=_send_message)
    service.bot = bot
    service.build_topup_success_keyboard = AsyncMock(return_value=None)  # type: ignore[method-assign]
    return service


@pytest.fixture
def _quiet_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('app.database.crud.transaction.emit_transaction_side_effects', AsyncMock())
    monkeypatch.setattr('app.services.referral_service.process_referral_topup', AsyncMock())
    monkeypatch.setattr(
        'app.services.admin_notification_service.AdminNotificationService.send_balance_topup_notification',
        AsyncMock(),
    )
    monkeypatch.setattr(type(settings), 'is_notifications_enabled', lambda self: True)


def _rollback_like_failed_auto_purchase(events: list[str]):
    async def _fake(user, amount_kopeks, db, bot, **kwargs):
        events.append('auto_purchase')
        # Так выходят ветки ошибок subscription_auto_purchase_service.
        await db.rollback()
        return False

    return _fake


@pytest.mark.asyncio
@pytest.mark.usefixtures('_quiet_side_effects')
async def test_balance_credited_survives_rollback_in_auto_purchase(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        'app.services.payment.common.send_cart_notification_after_topup',
        _rollback_like_failed_auto_purchase(events),
    )

    async with memory_session(monkeypatch, _TABLES) as db:
        user_id, order_id = await _seed(db)
        service = _make_service(events)

        assert await service.process_rollypay_webhook(db, _webhook_payload(order_id)) is True

        # Порядок сообщений прежний: сначала «Пополнение успешно!», потом автопокупка.
        assert events == ['success_message', 'auto_purchase']

        db.expire_all()
        user = await db.get(User, user_id)
        payment = (await db.execute(select(RollyPayPayment).where(RollyPayPayment.order_id == order_id))).scalar_one()
        assert user.balance_kopeks == _START_BALANCE + _AMOUNT
        assert payment.is_paid is True
        assert payment.transaction_id is not None
        assert payment.metadata_json['balance_credited'] is True
        assert payment.metadata_json['balance_change']['old_balance'] == _START_BALANCE
        assert payment.metadata_json['balance_change']['new_balance'] == _START_BALANCE + _AMOUNT
        assert payment.metadata_json['source'] == 'bot'


@pytest.mark.asyncio
@pytest.mark.usefixtures('_quiet_side_effects')
async def test_repeated_webhook_after_rollback_does_not_credit_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        'app.services.payment.common.send_cart_notification_after_topup',
        _rollback_like_failed_auto_purchase(events),
    )

    async with memory_session(monkeypatch, _TABLES) as db:
        user_id, order_id = await _seed(db)
        service = _make_service(events)

        await service.process_rollypay_webhook(db, _webhook_payload(order_id))
        db.expire_all()
        assert await service.process_rollypay_webhook(db, _webhook_payload(order_id)) is True

        db.expire_all()
        user = await db.get(User, user_id)
        assert user.balance_kopeks == _START_BALANCE + _AMOUNT
        deposits = await db.scalar(select(func.count()).select_from(Transaction).where(Transaction.user_id == user_id))
        assert deposits == 1
        # Второй вебхук не дошёл до побочных действий.
        assert events == ['success_message', 'auto_purchase']


@pytest.mark.asyncio
@pytest.mark.usefixtures('_quiet_side_effects')
async def test_balance_credited_is_committed_before_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отметка уже в БД, когда стартуют уведомления и автопокупка."""
    seen: dict = {}

    async def _peek(user, amount_kopeks, db, bot, **kwargs):
        stored = await db.scalar(select(RollyPayPayment.metadata_json).where(RollyPayPayment.order_id == 'rp_order_1'))
        seen.update(stored or {})
        return False

    monkeypatch.setattr('app.services.payment.common.send_cart_notification_after_topup', _peek)

    async with memory_session(monkeypatch, _TABLES) as db:
        _, order_id = await _seed(db)
        service = _make_service([])

        assert await service.process_rollypay_webhook(db, _webhook_payload(order_id)) is True

    assert seen.get('balance_credited') is True
    assert seen['balance_change']['new_balance'] == _START_BALANCE + _AMOUNT
