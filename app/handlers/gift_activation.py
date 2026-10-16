"""Handler for gift subscription activation via inline callback button."""

import html as html_mod

import structlog
from aiogram import Dispatcher, F, types
from aiogram.types import InaccessibleMessage

from app.config import settings
from app.database.crud.user import get_user_by_telegram_id
from app.database.database import AsyncSessionLocal
from app.localization.fork_text import fork_period, fork_text
from app.localization.texts import get_texts
from app.services.gift_claim_service import (
    GiftClaimAlreadyOwnedError,
    GiftClaimNotActivatableError,
    GiftClaimNotFoundError,
    GiftClaimSelfActivationError,
    claim_bound_gift_for_user,
)
from app.services.guest_purchase_service import GuestPurchaseError


logger = structlog.get_logger(__name__)

_GIFT_NOT_FOUND = 'Подарок не найден или недоступен.'


def _status_parse_mode() -> str | None:
    """parse_mode статусных правок сообщения: тексты форка размечены HTML, апстримные — простой текст."""
    return 'HTML' if settings.PREMIUM_THEME_ENABLED else None


def _guess_language(callback: types.CallbackQuery) -> str:
    """Язык до обращения к БД — по языку клиента Telegram, иначе язык по умолчанию."""
    code = ((callback.from_user.language_code or '') if callback.from_user else '').split('-')[0].lower()
    return code if code in {lang.lower() for lang in settings.get_available_languages()} else settings.DEFAULT_LANGUAGE


def gift_activated_text(texts, tariff_name: str, period_days: int | None, *, callback_variant: bool) -> str:
    """Сообщение «Подарок активирован».

    Без темы — шаблон апстрима с прежней подстановкой («Тариф — N дн.»). Под темой — шаблон форка
    и срок с правильным склонением на языке пользователя. `tariff_name` уже экранирован.
    """
    if settings.PREMIUM_THEME_ENABLED:
        period = fork_period(texts, period_days) if period_days else ''
        tariff_text = f'{tariff_name} · {period}' if tariff_name and period else (tariff_name or period)
        return fork_text(texts, 'FORK_GIFT_ACTIVATED', '').format(tariff_text=tariff_text)
    if callback_variant:
        period_text = f'{period_days} дн.' if period_days else ''
        tariff_text = f'{tariff_name} — {period_text}' if tariff_name else period_text
        return texts.t(
            'GIFT_ACTIVATION_CALLBACK_SUCCESS_TEXT',
            '✅ <b>Подарок активирован!</b>\n{tariff_text}\n\nВаша подписка обновлена.',
        ).format(tariff_text=tariff_text)
    return texts.t(
        'GIFT_ACTIVATION_SUCCESS_TEXT',
        '🎁 <b>Подарок активирован!</b>\n{tariff_name} — {period_days} дн.\n\nВаша подписка обновлена.',
    ).format(tariff_name=tariff_name, period_days=period_days)


async def handle_gift_activate(callback: types.CallbackQuery) -> None:
    """Handle gift_activate:{purchase_id} callback from Telegram notification."""
    if isinstance(callback.message, InaccessibleMessage):
        obsolete = 'Сообщение устарело. Попробуйте /start.'
        if settings.PREMIUM_THEME_ENABLED:
            obsolete = get_texts(_guess_language(callback)).t('GIFT_ACTIVATION_OBSOLETE_MESSAGE', obsolete)
        await callback.answer(obsolete, show_alert=True)
        return

    if not callback.data:
        return

    not_found_alert = not_found_text = _GIFT_NOT_FOUND
    in_progress = '⏳ Активируем подарок...'
    parse_mode = _status_parse_mode()
    if settings.PREMIUM_THEME_ENABLED:
        guess = get_texts(_guess_language(callback))
        not_found_alert = fork_text(guess, 'FORK_GIFT_NOT_FOUND_ALERT', not_found_alert)
        not_found_text = guess.t('GIFT_ACTIVATION_NOT_FOUND', not_found_text)
        in_progress = guess.t('GIFT_ACTIVATION_IN_PROGRESS', in_progress)

    parts = callback.data.split(':', 1)
    if len(parts) != 2:
        await callback.answer(not_found_alert, show_alert=True)
        return

    try:
        purchase_id = int(parts[1])
    except ValueError:
        await callback.answer(not_found_alert, show_alert=True)
        return

    await callback.answer()
    await callback.message.edit_text(in_progress, parse_mode=parse_mode)

    async with AsyncSessionLocal() as db:
        user = await get_user_by_telegram_id(db, callback.from_user.id)
        if not user:
            await callback.message.edit_text(not_found_text, parse_mode=parse_mode)
            return

        texts = get_texts(user.language)

        try:
            purchase = await claim_bound_gift_for_user(
                db,
                claimant_user_id=user.id,
                purchase_id=purchase_id,
            )
        except (GiftClaimNotFoundError, GiftClaimAlreadyOwnedError):
            await callback.message.edit_text(
                texts.t('GIFT_ACTIVATION_NOT_FOUND', _GIFT_NOT_FOUND),
                parse_mode=parse_mode,
            )
            return
        except GiftClaimSelfActivationError:
            await callback.message.edit_text(
                texts.t(
                    'GIFT_ACTIVATION_SELF_CLAIM_ERROR',
                    '⚠️ Нельзя активировать свой собственный подарок.\nОтправьте код другу!',
                ),
                parse_mode=parse_mode,
            )
            return
        except GiftClaimNotActivatableError:
            await callback.message.edit_text(
                texts.t('GIFT_ACTIVATION_NOT_ACTIVATABLE_ERROR', '❌ Этот подарок невозможно активировать.'),
                parse_mode=parse_mode,
            )
            return
        except GuestPurchaseError as exc:
            logger.warning(
                'Gift activation via callback failed',
                purchase_id=purchase_id,
                telegram_id=callback.from_user.id,
                error=exc.message,
            )
            if exc.status_code >= 500:
                await callback.message.edit_text(
                    texts.t(
                        'GIFT_ACTIVATION_GENERIC_ERROR',
                        'Произошла ошибка при активации. Попробуйте позже.',
                    ),
                    parse_mode=parse_mode,
                )
            else:
                await callback.message.edit_text(
                    texts.t(
                        'GIFT_ACTIVATION_FAILED_PREFIX',
                        'Не удалось активировать подарок: {error}',
                    ).format(error=html_mod.escape(exc.message)),
                    parse_mode=parse_mode,
                )
            return
        except Exception:
            logger.exception(
                'Unexpected error during gift activation via callback',
                purchase_id=purchase_id,
                telegram_id=callback.from_user.id,
            )
            await callback.message.edit_text(
                texts.t(
                    'GIFT_ACTIVATION_GENERIC_ERROR',
                    'Произошла ошибка при активации. Попробуйте позже.',
                ),
                parse_mode=parse_mode,
            )
            return

        tariff_name = html_mod.escape(purchase.tariff.name) if purchase.tariff and purchase.tariff.name else ''

        await callback.message.edit_text(
            gift_activated_text(texts, tariff_name, purchase.period_days, callback_variant=True),
        )


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(handle_gift_activate, F.data.startswith('gift_activate:'))
