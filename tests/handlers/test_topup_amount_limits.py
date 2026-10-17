"""Границы суммы, набранной вручную: у RollyPay — лимиты провайдера, у остальных — общие.

Подсказка RollyPay обещала «от 75 ₽ до 100 000 ₽» (ROLLYPAY_MIN/MAX_AMOUNT_KOPEKS на проде),
а общая проверка в process_topup_amount отсекала всё выше 50 000 ₽ ещё до провайдера.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import types

from app.config import settings
from app.handlers.balance import main as balance_main


def _message(text: str) -> MagicMock:
    message = MagicMock(spec=types.Message)
    message.text = text
    message.successful_payment = None
    message.answer = AsyncMock()
    return message


def _state(payment_method: str) -> AsyncMock:
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={'payment_method': payment_method})
    return state


@pytest.fixture
def routed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    calls: list[tuple[int, str]] = []

    async def route(message, db_user, amount_kopeks, state, method):
        calls.append((amount_kopeks, method))
        return True

    monkeypatch.setattr(balance_main, 'route_payment_by_method', route)
    monkeypatch.setattr(settings, 'ROLLYPAY_MIN_AMOUNT_KOPEKS', 7500)
    monkeypatch.setattr(settings, 'ROLLYPAY_MAX_AMOUNT_KOPEKS', 10_000_000)
    return calls


async def _submit(text: str, payment_method: str) -> MagicMock:
    message = _message(text)
    await balance_main.process_topup_amount(message, SimpleNamespace(id=1, language='ru'), _state(payment_method))
    return message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('text', 'expected_kopeks'),
    [('75', 7500), ('50000', 5_000_000), ('70000', 7_000_000), ('100000', 10_000_000)],
)
async def test_rollypay_accepts_amounts_within_provider_limits(routed, text: str, expected_kopeks: int) -> None:
    message = await _submit(text, 'rollypay')

    assert routed == [(expected_kopeks, 'rollypay')]
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('text', 'expected_reply'),
    [
        ('74.99', 'Минимальная сумма пополнения: 75 ₽'),
        ('1', 'Минимальная сумма пополнения: 75 ₽'),
        ('100000.01', 'Максимальная сумма пополнения: 100,000 ₽'),
        ('inf', 'Максимальная сумма пополнения: 100,000 ₽'),
    ],
)
async def test_rollypay_rejects_amounts_outside_provider_limits(routed, text: str, expected_reply: str) -> None:
    message = await _submit(text, 'rollypay')

    assert routed == []
    assert message.answer.await_args.args[0] == expected_reply


@pytest.mark.asyncio
@pytest.mark.parametrize(('text', 'expected_kopeks'), [('1', 100), ('50000', 5_000_000)])
async def test_other_methods_keep_general_limits_inside(routed, text: str, expected_kopeks: int) -> None:
    await _submit(text, 'cryptobot')

    assert routed == [(expected_kopeks, 'cryptobot')]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('text', 'expected_reply'),
    [
        ('0.99', 'Минимальная сумма пополнения: 1 ₽'),
        ('50000.01', 'Максимальная сумма пополнения: 50,000 ₽'),
        ('70000', 'Максимальная сумма пополнения: 50,000 ₽'),
    ],
)
async def test_other_methods_keep_general_limits_outside(routed, text: str, expected_reply: str) -> None:
    message = await _submit(text, 'cryptobot')

    assert routed == []
    assert message.answer.await_args.args[0] == expected_reply
