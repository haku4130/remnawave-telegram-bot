"""Уведомления мониторинга и вебхуков Remnawave — под темой форка.

Тесты гоняют настоящие отправители: у мониторинга подменена только сама отправка
(``_send_message_with_logo``), у вебхуков — служба доставки. Так в шаблон попадают реальные
kwargs вызова: у вебхуков неизвестный плейсхолдер молча гасит сообщение (``KeyError`` ловится
и пишется в лог), поэтому каждое уведомление обязано реально дойти до отправки.
Rich-доставка проверяется тем же ``build_notification_rich_html``, что и у бота: блочная
разметка вернула бы ``None``, и сообщение ушло бы классикой.
Без темы текст и кнопки остаются байт-в-байт как у апстрима.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.localization import loader
from app.localization.emoji_catalog import tag
from app.localization.texts import get_texts
from app.middlewares.premium_theme import theme_markup
from app.services import grace_access_notifications
from app.services.monitoring_service import MonitoringService
from app.services.notification_delivery_service import notification_delivery_service
from app.services.remnawave_webhook_service import RemnaWaveWebhookService
from app.utils.rich_notify import build_notification_rich_html
from app.utils.timezone import format_local_datetime


LINK = 'https://sub.example.com/abc123'
END_DATE = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
UPSTREAM_DIR = Path(__file__).resolve().parents[2] / 'app' / 'localization' / 'locales'

TAG_RE = re.compile(r'<[^>]+>')
CYRILLIC_RE = re.compile('[а-яА-ЯёЁ]')
BLOCK_RE = re.compile(r'</?(?:pre|blockquote|ul|ol|li|table|h[1-6]|p|div)\b')


def _plain(text: str) -> str:
    return TAG_RE.sub('', text)


def _upstream(language: str, key: str) -> str:
    return json.loads((UPSTREAM_DIR / f'{language}.json').read_text(encoding='utf-8'))[key]


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def _labels(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _greens(markup) -> int:
    return sum(1 for button in _buttons(theme_markup(markup)) if button.style == 'success')


def _date(value: datetime) -> str:
    return format_local_datetime(value, '%d.%m.%Y')


def _assert_fork_notification(text: str, language: str) -> None:
    """Общие правила форка: <b>-заголовок с одним эмодзи, уходит rich, без блочной разметки и хвостов {}."""
    first, _, rest = text.partition('\n')
    assert first.startswith('<b><tg-emoji ') and first.endswith('</b>'), first
    assert text.count('<tg-emoji') == 1, text
    assert rest.strip(), text
    assert not BLOCK_RE.search(text), text
    assert '{' not in text and '}' not in text, text
    assert len(_plain(text)) <= 1024
    rich = build_notification_rich_html(text)
    assert rich is not None, text
    # Заголовок жирный ровно один раз: rich видит <b> и второй раз не оборачивает.
    assert rich.startswith('<p><b><tg-emoji '), rich
    assert '<b><b>' not in rich
    if language == 'en':
        assert not CYRILLIC_RE.search(_plain(text)), text


def _assert_en_buttons(markup) -> None:
    for button in _buttons(markup):
        assert not CYRILLIC_RE.search(button.text), button.text


@pytest.fixture(autouse=True)
def _prod_like(monkeypatch):
    monkeypatch.setattr(settings, 'MULTI_TARIFF_ENABLED', False)
    monkeypatch.setattr(settings, 'CONNECT_BUTTON_MODE', 'miniapp_subscription')
    monkeypatch.setattr(settings, 'MAIN_MENU_MODE', 'default')
    monkeypatch.setattr(settings, 'ENABLE_AUTOPAY', False)
    monkeypatch.setattr(settings, 'PRICE_ROUNDING_ENABLED', False)
    monkeypatch.setattr(settings, 'WEBHOOK_NOTIFY_USER_ENABLED', True)


@pytest.fixture
def theme_on(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    loader.clear_locale_cache()
    yield
    loader.clear_locale_cache()


@pytest.fixture
def theme_off(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    yield
    loader.clear_locale_cache()


def _user(language: str = 'ru', balance: int = 5000) -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        telegram_id=555000111,
        language=language,
        balance_kopeks=balance,
        status='active',
        notification_settings=None,
    )


def _subscription(*, trial: bool = False, autopay: bool = True, link: str | None = LINK) -> SimpleNamespace:
    return SimpleNamespace(
        id=42,
        is_trial=trial,
        autopay_enabled=autopay,
        end_date=END_DATE,
        tariff=None,
        status='expired',
        subscription_url=link,
        subscription_crypto_link=None,
    )


# --- мониторинг -------------------------------------------------------------------------------


def _monitoring() -> MonitoringService:
    service = MonitoringService(bot=MagicMock())
    service._send_message_with_logo = AsyncMock()
    return service


def _monitoring_sent(service: MonitoringService):
    service._send_message_with_logo.assert_awaited_once()
    call = service._send_message_with_logo.await_args
    return call.kwargs['text'], call.kwargs.get('reply_markup')


async def _expired(language='ru', **sub):
    service = _monitoring()
    assert await service._send_subscription_expired_notification(_user(language), _subscription(**sub))
    return _monitoring_sent(service)


async def _expiring(language='ru', days=3, balance=5000, **sub):
    service = _monitoring()
    assert await service._send_subscription_expiring_notification(_user(language, balance), _subscription(**sub), days)
    return _monitoring_sent(service)


async def _trial_ending(language='ru'):
    service = _monitoring()
    assert await service._send_trial_ending_notification(_user(language), _subscription(trial=True))
    return _monitoring_sent(service)


async def _day1(monkeypatch, language='ru'):
    from app.services.pricing_engine import pricing_engine

    monkeypatch.setattr(
        pricing_engine, 'calculate_renewal_price', AsyncMock(return_value=SimpleNamespace(final_total=19900))
    )
    service = _monitoring()
    assert await service._send_expired_day1_notification(MagicMock(), _user(language), _subscription())
    return _monitoring_sent(service)


EXPIRES_AT = datetime(2026, 11, 20, 18, 30, tzinfo=UTC)


async def _discount(language='ru', wave='second'):
    service = _monitoring()
    trigger_days = 5 if wave == 'third' else None
    if trigger_days:
        sent = await service._send_expired_discount_notification(
            _user(language), _subscription(), 20, EXPIRES_AT, 900, wave, trigger_days=trigger_days
        )
    else:
        sent = await service._send_expired_discount_notification(
            _user(language), _subscription(), 10, EXPIRES_AT, 900, wave
        )
    assert sent
    return _monitoring_sent(service)


async def _autopay_success(language='ru'):
    service = _monitoring()
    await service._send_autopay_success_notification(_user(language), 19900, 30, subscription=_subscription())
    return _monitoring_sent(service)


async def _autopay_failed(language='ru', balance=5000, required=19900, is_final=False):
    service = _monitoring()
    await service._send_autopay_failed_notification(
        _user(language), balance, required, subscription=_subscription(), is_final=is_final
    )
    return _monitoring_sent(service)


# без темы — байт-в-байт апстрим


async def test_expired_theme_off_is_upstream(theme_off):
    text, markup = await _expired()
    assert text == (
        '\n⛔ <b>Подписка истекла</b>\n\n'
        'Ваша подписка истекла. Для восстановления доступа продлите подписку.\n\n'
        '🔧 Доступ к серверам заблокирован до продления.\n'
    )
    assert _labels(markup) == [['💎 Продлить подписку'], ['💳 Пополнить баланс']]


async def test_expiring_theme_off_is_upstream(theme_off):
    text, markup = await _expiring()
    texts = get_texts('ru')
    assert text == (
        '\n⚠️ <b>Подписка истекает через 3 дня!</b>\n\n'
        f'Ваша платная подписка истекает {format_local_datetime(END_DATE, "%d.%m.%Y %H:%M")}.\n\n'
        '💳 <b>Автоплатеж:</b> ✅ Включен — подписка продлится автоматически\n\n'
        f'💰 Ваш текущий баланс: {texts.format_price(5000)}\n'
    )
    assert _labels(markup) == [['⏰ Продлить подписку'], ['💳 Пополнить баланс'], ['📱 Моя подписка']]


async def test_trial_ending_theme_off_is_upstream(theme_off):
    text, markup = await _trial_ending()
    assert text.startswith(
        '\n🎁 <b>Тестовая подписка скоро закончится!</b>\n\nВаша тестовая подписка истекает через 2 часа.'
    )
    assert _labels(markup) == [['💎 Купить подписку'], ['💰 Пополнить баланс']]


async def test_day1_theme_off_is_upstream(theme_off, monkeypatch):
    text, markup = await _day1(monkeypatch)
    assert text == _upstream('ru', 'SUBSCRIPTION_EXPIRED_1D').format(
        tariff_label='',
        end_date=format_local_datetime(END_DATE, '%d.%m.%Y %H:%M'),
        price=settings.format_price(19900),
    )
    assert _labels(markup) == [['💎 Продлить'], ['💳 Пополнить баланс'], ['🆘 Поддержка']]


async def test_discount_theme_off_is_upstream(theme_off):
    text, markup = await _discount(wave='third')
    assert text == _upstream('ru', 'SUBSCRIPTION_EXPIRED_THIRD_WAVE').format(
        percent=20, tariff_label='', trigger_days=5, expires_at=format_local_datetime(EXPIRES_AT, '%d.%m.%Y %H:%M')
    )
    assert _labels(markup) == [['🎁 Получить скидку'], ['💎 Продлить'], ['💳 Пополнить баланс'], ['🆘 Поддержка']]


async def test_autopay_theme_off_is_upstream(theme_off):
    text, markup = await _autopay_success()
    assert text == _upstream('ru', 'AUTOPAY_SUCCESS').format(days=30, amount=settings.format_price(19900))
    assert markup is None
    text, markup = await _autopay_failed()
    assert text == _upstream('ru', 'AUTOPAY_FAILED').format(
        balance=settings.format_price(5000), required=settings.format_price(19900)
    )
    assert _labels(markup) == [['💳 Пополнить баланс'], ['📱 Моя подписка']]


# под темой


async def test_expired_paid_theme(theme_on):
    text, markup = await _expired()
    assert text == (
        f'<b>{tag("cross")} Подписка закончилась</b>\nVPN отключён. Продлите подписку — доступ вернётся сразу.'
    )
    _assert_fork_notification(text, 'ru')
    extend, topup = _buttons(markup)
    assert extend.callback_data == 'subscription_extend' and extend.style == 'success'
    assert extend.text == get_texts('ru').FORK_BUTTON_EXTEND
    assert topup.callback_data == 'balance_topup' and topup.style == 'primary'
    assert _greens(markup) == 1


async def test_expired_trial_theme_offers_tariff(theme_on):
    text, markup = await _expired(trial=True)
    assert (
        text == f'<b>{tag("hourglass")} Пробный доступ закончился</b>\nЧтобы пользоваться VPN дальше, выберите тариф.'
    )
    _assert_fork_notification(text, 'ru')
    choose, help_button = _buttons(markup)
    assert choose.callback_data == 'menu_buy' and choose.style == 'success'
    assert help_button.callback_data == 'menu_support' and help_button.style is None
    assert _greens(markup) == 1


@pytest.mark.parametrize(
    ('autopay', 'days', 'autopay_text'),
    [
        (True, 3, 'Автоплатёж включён: продлим с баланса, если на нём хватит денег.\nНа балансе: <b>{balance}</b>'),
        (False, 1, 'Автоплатёж выключен — продлите подписку вручную.'),
    ],
)
async def test_expiring_theme(theme_on, autopay, days, autopay_text):
    text, markup = await _expiring(days=days, autopay=autopay)
    days_text = '3 дня' if days == 3 else '1 день'
    assert text == (
        f'<b>{tag("hourglass")} Подписка закончится через {days_text}</b>\n'
        f'Действует до <b>{_date(END_DATE)}</b>\n' + autopay_text.format(balance=settings.format_price(5000))
    )
    _assert_fork_notification(text, 'ru')
    assert [button.callback_data for button in _buttons(markup)] == ['subscription_extend', 'balance_topup']
    assert _greens(markup) == 1


async def test_trial_ending_theme(theme_on):
    text, markup = await _trial_ending()
    assert text == (
        f'<b>{tag("hourglass")} Пробный доступ закончится через 2 часа</b>\nЧтобы VPN не отключился, выберите тариф.'
    )
    _assert_fork_notification(text, 'ru')
    assert [button.callback_data for button in _buttons(markup)] == ['menu_buy', 'menu_support']
    assert _greens(markup) == 1


async def test_day1_theme(theme_on, monkeypatch):
    text, markup = await _day1(monkeypatch)
    assert text == (
        f'<b>{tag("cross")} Подписка закончилась</b>\n'
        f'VPN отключён с {_date(END_DATE)}.\n'
        f'Продление: <b>{settings.format_price(19900)}</b> за 30 дней'
    )
    _assert_fork_notification(text, 'ru')
    assert [button.callback_data for button in _buttons(markup)] == [
        'subscription_extend',
        'balance_topup',
        'menu_support',
    ]
    assert _greens(markup) == 1


async def test_discount_waves_theme(theme_on):
    expires = format_local_datetime(EXPIRES_AT, '%d.%m.%Y %H:%M')
    text, markup = await _discount(wave='second')
    assert text == (
        f'<b>{tag("fire")} Скидка 10% на продление</b>\n'
        f'Активируйте до <b>{expires}</b> — она сложится с другими вашими скидками.'
    )
    _assert_fork_notification(text, 'ru')
    claim, extend, help_button = _buttons(markup)
    assert claim.callback_data == 'claim_discount_900' and claim.style == 'success'
    assert extend.callback_data == 'subscription_extend' and extend.style == 'primary'
    assert help_button.callback_data == 'menu_support'
    assert _greens(markup) == 1

    text, _ = await _discount(wave='third')
    assert text == (
        f'<b>{tag("gift")} Персональная скидка 20%</b>\n'
        f'Вы без VPN уже 5 дней. Активируйте скидку до <b>{expires}</b> — она сложится с другими вашими скидками.'
    )
    _assert_fork_notification(text, 'ru')


async def test_autopay_success_theme(theme_on):
    text, markup = await _autopay_success()
    assert text == (
        f'<b>{tag("check")} Подписка продлена на 30 дней</b>\n'
        f'Автоплатёж: списано <b>{settings.format_price(19900)}</b> с баланса\n'
        f'Действует до <b>{_date(END_DATE)}</b>'
    )
    _assert_fork_notification(text, 'ru')
    assert markup is None


@pytest.mark.parametrize('is_final', [False, True])
async def test_autopay_failed_theme(theme_on, is_final):
    text, markup = await _autopay_failed(is_final=is_final)
    head = (
        f'<b>{tag("alarm")} Подписка скоро отключится</b>\nАвтоплатёж не прошёл — не хватает <b>{{missing}}</b>.'
        if is_final
        else f'<b>{tag("warning")} Автоплатёж не прошёл</b>\nНа продление не хватает <b>{{missing}}</b>.'
    )
    tail = (
        'Пополните баланс сейчас, чтобы не потерять доступ.'
        if is_final
        else 'Пополните баланс, чтобы подписка не отключилась.'
    )
    assert text == (
        head.format(missing=settings.format_price(14900))
        + f'\nНужно: {settings.format_price(19900)}, на балансе: {settings.format_price(5000)}.\n'
        + tail
    )
    _assert_fork_notification(text, 'ru')
    topup, subscription = _buttons(markup)
    assert topup.callback_data == 'balance_topup' and topup.style == 'success'
    assert subscription.callback_data == 'menu_subscription'
    assert _greens(markup) == 1


async def test_autopay_charge_error_theme_offers_manual_renewal(theme_on):
    """Денег хватает, но списание сорвалось: «не хватает 0 ₽» было бы враньём."""
    text, markup = await _autopay_failed(balance=30000)
    assert text == (
        f'<b>{tag("warning")} Автоплатёж не прошёл</b>\nНе удалось списать оплату за продление. '
        'Продлите подписку вручную.'
    )
    _assert_fork_notification(text, 'ru')
    assert [button.callback_data for button in _buttons(markup)] == ['subscription_extend', 'menu_subscription']
    assert _greens(markup) == 1


async def test_monitoring_english_theme(theme_on, monkeypatch):
    results = [
        await _expired('en'),
        await _expired('en', trial=True),
        await _expiring('en', autopay=True),
        await _expiring('en', autopay=False, days=1),
        await _trial_ending('en'),
        await _day1(monkeypatch, 'en'),
        await _discount('en', 'second'),
        await _discount('en', 'third'),
        await _autopay_success('en'),
        await _autopay_failed('en'),
        await _autopay_failed('en', is_final=True),
        await _autopay_failed('en', balance=30000),
    ]
    for text, markup in results:
        _assert_fork_notification(text, 'en')
        if markup is not None:
            _assert_en_buttons(markup)
            assert _greens(markup) == 1
    assert '30 days' in results[5][0] and 'in 1 day' in results[3][0] and '5 days' in results[7][0]


@pytest.mark.parametrize('language', ['ru', 'en'])
def test_traffic_warning_template_with_real_kwargs(theme_on, language):
    """Предупреждение о трафике уходит обычным HTML-сообщением: первая строка — <b>…</b>."""
    text = get_texts(language).get('TRAFFIC_WARNING_ALERT').format(used=45.3, limit=50, percent=90.4)
    _assert_fork_notification(text, language)
    unit = 'ГБ' if language == 'ru' else 'GB'
    assert '45.3 ' in text and f'50 {unit} (90%)' in text


# --- вебхуки Remnawave ------------------------------------------------------------------------


@pytest.fixture
def webhook(monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(notification_delivery_service, 'send_notification', send)
    return RemnaWaveWebhookService(MagicMock()), send


def _webhook_sent(send):
    send.assert_awaited_once()
    kwargs = send.await_args.kwargs
    return kwargs['telegram_message'], kwargs['telegram_markup']


DEVICE = {'hwidUserDevice': {'deviceModel': 'iPhone 15', 'platform': 'iOS'}}

# (ключ, как вызвать: обработчик без БД с настоящими данными события или _notify_user как в обработчике с БД)
WEBHOOK_CASES = [
    (
        'WEBHOOK_USER_NOT_CONNECTED',
        'handler',
        ('_handle_user_not_connected', {'_meta': {'notConnectedAfterHours': 24}}),
    ),
    ('WEBHOOK_USER_NOT_CONNECTED', 'handler', ('_handle_user_not_connected', {})),
    ('WEBHOOK_SUB_FIRST_CONNECTED', 'handler', ('_handle_first_connected', {})),
    ('WEBHOOK_SUB_BANDWIDTH_THRESHOLD', 'handler', ('_handle_bandwidth_threshold', {'lastTriggeredThreshold': 80})),
    ('WEBHOOK_DEVICE_ADDED', 'handler', ('_handle_device_added', DEVICE)),
    ('WEBHOOK_DEVICE_DELETED', 'handler', ('_handle_device_deleted', DEVICE)),
    ('WEBHOOK_TORRENT_DETECTED', 'handler', ('_handle_torrent_detected', {})),
    ('WEBHOOK_SUB_EXPIRES_72H', 'handler', ('_handle_user_expiration', {'_meta': {'expiration': -72}})),
    ('WEBHOOK_SUB_EXPIRES_48H', 'handler', ('_handle_user_expiration', {'_meta': {'expiration': -48}})),
    ('WEBHOOK_SUB_EXPIRES_24H', 'handler', ('_handle_user_expiration', {'_meta': {'expiration': -24}})),
    ('WEBHOOK_SUB_EXPIRED_24H_AGO', 'handler', ('_handle_user_expiration', {'_meta': {'expiration': 24}})),
    ('WEBHOOK_SUB_EXPIRED', 'notify', 'renew'),
    ('WEBHOOK_SUB_DELETED', 'notify', 'renew'),
    ('WEBHOOK_SUB_DISABLED', 'notify', 'subscription'),
    ('WEBHOOK_SUB_TRAFFIC_RESET', 'notify', 'subscription'),
    ('WEBHOOK_SUB_ENABLED', 'notify', 'connect'),
    ('WEBHOOK_SUB_REVOKED', 'notify', 'connect'),
    ('WEBHOOK_SUB_LIMITED', 'notify', 'traffic'),
]


async def _run_webhook(service, key, kind, how, user, subscription):
    if kind == 'handler':
        name, data = how
        await getattr(service, name)(None, user, subscription, data)
        return
    # Обработчики с БД зовут _notify_user ровно так (без format_kwargs).
    markup = {
        'renew': lambda: service._get_renew_keyboard(user, subscription.id, subscription=subscription),
        'subscription': lambda: service._get_subscription_keyboard(user),
        'connect': lambda: service._get_connect_keyboard(user, subscription),
        'traffic': lambda: service._get_traffic_keyboard(user),
    }[how]()
    await service._notify_user(user, key, reply_markup=markup, subscription=subscription)


@pytest.mark.parametrize('language', ['ru', 'en'])
@pytest.mark.parametrize(('key', 'kind', 'how'), WEBHOOK_CASES)
async def test_webhook_notification_theme(theme_on, webhook, language, key, kind, how):
    service, send = webhook
    await _run_webhook(service, key, kind, how, _user(language), _subscription())
    text, markup = _webhook_sent(send)
    assert send.await_args.kwargs['context']['text_key'] == key
    _assert_fork_notification(text, language)
    # Закрыть — навигация: без цвета и без иконки.
    close = markup.inline_keyboard[-1][0]
    assert close.callback_data == 'webhook:close'
    themed_close = theme_markup(markup).inline_keyboard[-1][0]
    assert themed_close.style is None and themed_close.icon_custom_emoji_id is None
    assert _greens(markup) == 1
    if language == 'en':
        _assert_en_buttons(markup)


@pytest.mark.parametrize(('key', 'kind', 'how'), WEBHOOK_CASES)
async def test_webhook_notification_theme_off_is_upstream(theme_off, webhook, key, kind, how):
    service, send = webhook
    await _run_webhook(service, key, kind, how, _user(), _subscription())
    text, markup = _webhook_sent(send)
    data = how[1] if kind == 'handler' else {}
    kwargs = {'tariff_label': ''}
    if key == 'WEBHOOK_SUB_BANDWIDTH_THRESHOLD':
        kwargs['percent'] = '80'
    if key.startswith('WEBHOOK_DEVICE_'):
        kwargs['device'] = 'iPhone 15 (iOS)'
    if data.get('_meta', {}).get('notConnectedAfterHours'):
        kwargs['hours'] = '24'
    assert text == _upstream('ru', key).format(**kwargs)
    assert markup.inline_keyboard[-1][0].text == '✖️ Закрыть'


async def test_webhook_texts_ru(theme_on, webhook):
    service, send = webhook
    await service._handle_user_not_connected(None, _user(), _subscription(), {})
    text, _ = _webhook_sent(send)
    assert text == (
        f'<b>{tag("rocket")} Вы ещё не подключились</b>\n'
        'Подписка уже работает — осталось настроить приложение. Это займёт пару минут.\n'
        'Не получается? Напишите нам — поможем.'
    )
    send.reset_mock()
    await service._handle_device_added(None, _user(), _subscription(), DEVICE)
    text, _ = _webhook_sent(send)
    assert text == (
        f'<b>{tag("phone")} Новое устройство</b>\n'
        'К подписке подключено: <code>iPhone 15 (iOS)</code>\n'
        'Если это не вы, сбросьте устройство в настройках подписки.'
    )


async def test_not_connected_keyboard_theme(theme_on, webhook):
    """Конверсия: зелёная «Подключиться» сразу открывает подписку, рядом «Помощь»."""
    service, send = webhook
    await service._handle_user_not_connected(None, _user(), _subscription(), {})
    _, markup = _webhook_sent(send)
    texts = get_texts('ru')
    connect, help_button, close = _buttons(markup)
    assert connect.text == texts.FORK_BUTTON_CONNECT
    assert connect.web_app is not None and connect.web_app.url == LINK
    assert connect.style == 'success'
    assert help_button.text == texts.FORK_BUTTON_HELP and help_button.callback_data == 'menu_support'
    assert close.text == 'Закрыть'
    themed = _buttons(theme_markup(markup))
    assert [button.style for button in themed] == ['success', None, None]
    assert themed[0].icon_custom_emoji_id and themed[1].icon_custom_emoji_id


async def test_not_connected_without_link_falls_back_to_callback(theme_on, webhook):
    service, send = webhook
    await service._handle_user_not_connected(None, _user(), _subscription(link=None), {})
    _, markup = _webhook_sent(send)
    assert markup.inline_keyboard[0][0].callback_data == 'subscription_connect'


async def test_not_connected_keyboard_theme_off_is_upstream(theme_off, webhook):
    service, send = webhook
    await service._handle_user_not_connected(None, _user(), _subscription(), {})
    _, markup = _webhook_sent(send)
    assert _labels(markup) == [['🔗 Подключиться'], ['✖️ Закрыть']]
    assert markup.inline_keyboard[0][0].callback_data == 'subscription_connect'


async def test_renew_keyboard_theme(theme_on, webhook):
    service, send = webhook
    await service._handle_user_expiration(None, _user(), _subscription(), {'_meta': {'expiration': -24}})
    _, markup = _webhook_sent(send)
    renew = markup.inline_keyboard[0][0]
    assert renew.callback_data == 'subscription_extend' and renew.style == 'success'
    assert renew.text == get_texts('ru').FORK_BUTTON_EXTEND
    assert _greens(markup) == 1


async def test_renew_keyboard_for_trial_offers_tariff(theme_on, webhook):
    service, send = webhook
    await service._handle_user_expiration(None, _user(), _subscription(trial=True), {'_meta': {'expiration': 24}})
    _, markup = _webhook_sent(send)
    choose = markup.inline_keyboard[0][0]
    assert choose.callback_data == 'menu_buy' and choose.style == 'success'
    assert _greens(markup) == 1


async def test_traffic_keyboard_theme(theme_on, webhook):
    service, send = webhook
    await service._handle_bandwidth_threshold(None, _user(), _subscription(), {'lastTriggeredThreshold': 90})
    text, markup = _webhook_sent(send)
    assert '90%' in text
    buy, subscription, _close = _buttons(markup)
    assert buy.callback_data == 'buy_traffic' and buy.style == 'success'
    assert buy.text == get_texts('ru').FORK_BUTTON_BUY_TRAFFIC
    assert subscription.callback_data == 'menu_subscription'
    assert _greens(markup) == 1


async def test_renew_keyboard_theme_off_is_upstream(theme_off, webhook):
    service, send = webhook
    await service._handle_user_expiration(None, _user(), _subscription(trial=True), {'_meta': {'expiration': 24}})
    _, markup = _webhook_sent(send)
    assert _labels(markup) == [['🔄 Продлить подписку'], ['✖️ Закрыть']]
    assert markup.inline_keyboard[0][0].callback_data == 'subscription_extend'


# --- grace (временный доступ) -----------------------------------------------------------------


def _grace_details(reason='expired', completion_reason=None):
    return grace_access_notifications._GraceDetails(
        reason=reason,
        grace_until=datetime.now(UTC) + timedelta(hours=6),
        hours=6,
        quota_gb=1.5,
        completion_reason=completion_reason,
        last_error=None,
    )


@pytest.mark.parametrize('language', ['ru', 'en'])
@pytest.mark.parametrize(
    ('event', 'reason', 'completion'),
    [('granted', 'expired', None), ('granted', 'limited', None), ('ended', 'expired', 'timeout')],
)
async def test_grace_notifications_theme(theme_on, monkeypatch, language, event, reason, completion):
    send = AsyncMock()
    monkeypatch.setattr(notification_delivery_service, 'send_notification', send)
    subscription = _subscription()
    subscription.user = _user(language)
    await grace_access_notifications._notify_user(MagicMock(), event, subscription, _grace_details(reason, completion))
    text, markup = _webhook_sent(send)
    _assert_fork_notification(text, language)
    assert _greens(markup) == 1
    if language == 'en':
        _assert_en_buttons(markup)
