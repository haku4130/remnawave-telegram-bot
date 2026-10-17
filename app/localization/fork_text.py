"""Помощники форка для текстов экранов.

Строки форка (ключи слоя app/localization/fork/) читаются через эти функции: без
темы они возвращают исходный текст апстрима, так что вывод остаётся как был.
Форматтеры трафика/устройств/срока подставляют единицы из ключей FORK_* — английский
пользователь не видит «ГБ» и «дней». Работают только при включённой теме (ключи
FORK_* загружаются вместе со слоем форка).
"""

from __future__ import annotations

from app.localization.loader import fork_keys
from app.utils.fork_onboarding import format_days


_INFINITY = '∞'


def fork_text(texts, key: str, default: str) -> str:
    """Значение ключа из слоя форка; нет темы или ключа в слое — `default` (строка апстрима)."""
    if key not in fork_keys(texts.language):
        return default
    return texts.get(key, default)


def has_fork_text(texts, key: str) -> bool:
    """Есть ли у языка `texts` ключ в слое форка (тема включена и у языка есть файл форка).

    Для экранов, где текст форка и апстрима собираются по-разному: без ключа — ветка апстрима,
    чтобы пользователь с языком без файла форка (например, 'tr') не получил пустое сообщение.
    """
    return key in fork_keys(texts.language)


def _gb(texts, value: float) -> str:
    return texts.FORK_GB.replace('{n}', f'{value:g}')


def fork_traffic(texts, gb: float | None) -> str:
    """«12.5 ГБ» / «12.5 GB»; 0 и None (лимит без ограничения) — «∞»."""
    value = float(gb or 0)
    return _gb(texts, value) if value > 0 else _INFINITY


def fork_traffic_usage(texts, used_gb: float | None, limit_gb: float | None, *, bold_used: bool = False) -> str:
    """«1.2 ГБ из 50 ГБ» / «1.2 GB of 50 GB»; лимит 0/None — «∞». bold_used — «<b>1.2 ГБ</b> из 50 ГБ»."""
    used = _gb(texts, round(float(used_gb or 0), 1))
    if bold_used:
        used = f'<b>{used}</b>'
    return texts.FORK_SUB_TRAFFIC_VALUE.replace('{used}', used).replace('{limit}', fork_traffic(texts, limit_gb))


def fork_period(texts, days: int) -> str:
    """«30 дней» / «30 days» (склонение по языку)."""
    return format_days(texts, days)


def fork_devices(texts, used: int | str | None, limit: int | str | None) -> str:
    """«1 из 3» / «1 of 3»; лимит 0/None — «∞»."""
    try:
        has_limit = int(limit or 0) > 0
    except (TypeError, ValueError):
        has_limit = bool(limit)
    return texts.FORK_DEVICES_OF.replace('{used}', str(used if used is not None else 0)).replace(
        '{limit}', str(limit) if has_limit else _INFINITY
    )
