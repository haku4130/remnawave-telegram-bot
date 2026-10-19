"""Доп. устройства тарифа за период считаются по дням, а не по округлённым месяцам.

Месяцы брались как max(1, round(days / 30)) (в кабинете — days // 30), и на
14-дневном периоде устройство стоило как за полный месяц: 109 ₽ за тариф и
ещё 55 ₽ за каждое устройство сверх включённых. Теперь цена устройства за
период — месячная ставка × days / 30; для 30/90/180/360 дней ничего не
меняется.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.pricing_utils import calculate_period_device_price


class TestCalculatePeriodDevicePrice:
    @pytest.mark.parametrize(
        ('days', 'expected'),
        [(14, 2100), (30, 4500), (90, 13500), (180, 27000), (360, 54000)],
    )
    def test_prorated_by_days(self, days, expected):
        assert calculate_period_device_price(4500, days) == expected

    def test_rounds_to_kopeks(self):
        assert calculate_period_device_price(5500, 14) == 2567

    def test_zero_price_or_days(self):
        assert calculate_period_device_price(0, 14) == 0
        assert calculate_period_device_price(4500, 0) == 0


def _tariff(**overrides):
    tariff = MagicMock()
    tariff.id = 1
    tariff.is_daily = False
    tariff.period_prices = {'14': 10900, '30': 19900, '360': 199900}
    tariff.device_limit = 3
    tariff.device_price_kopeks = 4500
    for key, value in overrides.items():
        setattr(tariff, key, value)
    return tariff


def _subscription(tariff, device_limit):
    subscription = MagicMock()
    subscription.tariff_id = tariff.id
    subscription.tariff = tariff
    subscription.device_limit = device_limit
    return subscription


def _user():
    user = MagicMock()
    user.promo_group = None
    user.get_primary_promo_group.return_value = None
    return user


class TestPricingEngineTariffDevices:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ('days', 'devices_price', 'total'),
        [(14, 2 * 2100, 10900 + 4200), (30, 2 * 4500, 19900 + 9000), (360, 2 * 54000, 199900 + 108000)],
    )
    async def test_extra_devices_prorated_by_days(self, days, devices_price, total):
        from app.services.pricing_engine import PricingEngine

        tariff = _tariff()
        with patch('app.services.pricing_engine.get_user_active_promo_discount_percent', return_value=0):
            result = await PricingEngine().calculate_renewal_price(
                AsyncMock(), _subscription(tariff, 5), days, user=_user()
            )
        assert result.devices_price == devices_price
        assert result.final_total == total


class TestBotRenewalKeyboardDevices:
    def test_extra_devices_cost_14_days(self):
        from app.handlers.subscription.tariff_purchase import _calc_extra_devices_cost

        assert _calc_extra_devices_cost(_tariff(), 4, 14) == 2100
        assert _calc_extra_devices_cost(_tariff(), 4, 30) == 4500
        assert _calc_extra_devices_cost(_tariff(), 4, 360) == 54000


class TestCabinetTariffPeriodsDevices:
    @pytest.mark.asyncio
    async def test_extra_devices_cost_per_period(self):
        from app.cabinet.routes.subscription_modules.purchase import _build_tariff_response
        from app.database.models import Tariff

        tariff = Tariff(
            id=1,
            name='Стандартный',
            description='',
            is_active=True,
            is_daily=False,
            period_prices={'14': 10900, '30': 19900, '360': 199900},
            traffic_limit_gb=0,
            device_limit=3,
            device_price_kopeks=4500,
            allowed_squads=[],
            display_order=1,
        )
        subscription = SimpleNamespace(tariff_id=1, device_limit=4)
        user = SimpleNamespace(
            id=1,
            language='ru',
            promo_group=None,
            get_primary_promo_group=lambda: None,
            promo_offer_discount_percent=0,
            promo_offer_discount_expires_at=None,
        )
        data = await _build_tariff_response(SimpleNamespace(), tariff, user=user, subscription=subscription)
        by_days = {p['days']: p for p in data['periods']}
        assert by_days[14]['extra_devices_cost_kopeks'] == 2100
        assert by_days[14]['price_kopeks'] == 10900 + 2100
        assert by_days[30]['extra_devices_cost_kopeks'] == 4500
        assert by_days[360]['extra_devices_cost_kopeks'] == 54000
