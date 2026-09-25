"""Бесплатные слоты устройств до базового лимита.

Регрессия (2026-09-25): подписка без тарифа с лимитом 1 при DEFAULT_DEVICE_LIMIT=3.
Цена в кабинете (`GET /devices/price`) честно показывала «бесплатно», а покупка
(`POST /devices/purchase`) навязывала минимальную цену 1₽ и при пустом балансе
отвечала 402 — кабинет уводил пользователя на пополнение.

Вторая половина той же истории: админское создание подписки без выбранного тарифа
ставило захардкоженный лимит 1 вместо DEFAULT_DEVICE_LIMIT.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cabinet.routes import admin_users
from app.cabinet.routes.subscription_modules import devices
from app.cabinet.schemas.subscription import DevicePurchaseRequest
from app.cabinet.schemas.users import UpdateSubscriptionRequest
from app.config import settings


def _subscription(device_limit: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=132,
        user_id=1,
        status='active',
        tariff_id=None,
        device_limit=device_limit,
        remnawave_id=None,
        end_date=datetime.now(UTC) + timedelta(days=30),
    )


@pytest.fixture
def purchase_env(monkeypatch):
    monkeypatch.setattr(settings, 'DEFAULT_DEVICE_LIMIT', 3)
    monkeypatch.setattr(settings, 'PRICE_PER_DEVICE', 10000)
    monkeypatch.setattr(settings, 'MAX_DEVICES_LIMIT', 0)
    monkeypatch.setattr(settings, 'ADMIN_NOTIFICATIONS_ENABLED', False)

    subscription = _subscription(device_limit=1)
    user = SimpleNamespace(id=1, balance_kopeks=0, restriction_subscription=False)

    result = MagicMock()
    result.scalar_one_or_none.return_value = subscription
    result.scalar_one.return_value = subscription
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    monkeypatch.setattr(devices, 'resolve_subscription', AsyncMock(return_value=subscription))
    monkeypatch.setattr(
        devices,
        '_apply_addon_discount',
        lambda _user, _category, amount, _days: {'discounted': amount, 'percent': 0, 'discount': 0},
    )
    monkeypatch.setattr('app.database.crud.user.lock_user_for_pricing', AsyncMock(return_value=user))
    subtract = AsyncMock(return_value=True)
    monkeypatch.setattr('app.database.crud.user.subtract_user_balance', subtract)
    monkeypatch.setattr(devices, 'SubscriptionService', MagicMock(return_value=AsyncMock()))
    save_cart = AsyncMock()
    monkeypatch.setattr(devices.user_cart_service, 'save_user_cart', save_cart)

    return SimpleNamespace(db=db, user=user, subscription=subscription, subtract=subtract, save_cart=save_cart)


async def test_free_slots_up_to_default_limit_are_not_charged(purchase_env):
    response = await devices.purchase_devices(
        request=DevicePurchaseRequest(devices=2),
        subscription_id=None,
        user=purchase_env.user,
        db=purchase_env.db,
    )

    assert response['price_kopeks'] == 0
    assert response['new_device_limit'] == 3
    purchase_env.save_cart.assert_not_awaited()
    assert purchase_env.subtract.await_args.kwargs['amount_kopeks'] == 0


async def test_paid_slot_beyond_default_limit_keeps_one_ruble_floor(purchase_env, monkeypatch):
    purchase_env.user.balance_kopeks = 1_000_000
    # Почти истёкшая подписка: прорейт даёт копейки, минимум 1₽ обязан сработать.
    purchase_env.subscription.end_date = datetime.now(UTC) + timedelta(hours=1)
    purchase_env.subscription.device_limit = 3

    response = await devices.purchase_devices(
        request=DevicePurchaseRequest(devices=1),
        subscription_id=None,
        user=purchase_env.user,
        db=purchase_env.db,
    )

    assert response['price_kopeks'] == 333  # 100₽ × 1 день / 30

    purchase_env.subscription.device_limit = 3  # первая покупка подняла лимит до 4
    monkeypatch.setattr(settings, 'PRICE_PER_DEVICE', 100)
    response = await devices.purchase_devices(
        request=DevicePurchaseRequest(devices=1),
        subscription_id=None,
        user=purchase_env.user,
        db=purchase_env.db,
    )
    assert response['price_kopeks'] == 100


async def test_admin_create_without_tariff_uses_default_device_limit(monkeypatch):
    monkeypatch.setattr(settings, 'DEFAULT_DEVICE_LIMIT', 3)
    user = SimpleNamespace(id=1, subscriptions=[])
    monkeypatch.setattr(admin_users, 'get_user_by_id', AsyncMock(return_value=user))
    create = AsyncMock(return_value=SimpleNamespace(id=132))
    monkeypatch.setattr('app.database.crud.subscription.create_paid_subscription', create)
    monkeypatch.setattr(admin_users, '_sync_subscription_to_panel', AsyncMock())
    monkeypatch.setattr(admin_users, '_build_subscription_info_async', AsyncMock(return_value=None))

    await admin_users.update_user_subscription(
        user_id=1,
        request=UpdateSubscriptionRequest(action='create', days=30),
        admin=SimpleNamespace(id=99),
        db=MagicMock(),
    )

    assert create.await_args.kwargs['device_limit'] == 3
