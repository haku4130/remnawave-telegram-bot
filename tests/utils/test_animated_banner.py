"""Анимированный баннер вместо статичного логотипа.

Баннер должен появляться везде, где раньше стоял логотип: в фото-сообщениях
экранов и в шапке rich-сообщений. Главное требование — запасной путь: если
Telegram отверг анимацию, бот до рестарта возвращается к статичному логотипу,
а случайные ошибки (удалённый чат, длинная подпись) баннер не выключают.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputMediaAnimation, InputMediaPhoto

import app.utils.message_patch as mp
import app.utils.photo_message as pm
from app.config import settings
from app.services.startup_notification_service import StartupNotificationService
from app.utils import animated_banner, rich_admin, rich_menu, rich_notify
from app.utils.animated_banner import RICH_BANNER_TAG, RICH_MEDIA_ID


MEDIA_ERROR = 'Bad Request: wrong file identifier/HTTP URL specified'


def _bad_request(message: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=None, message=message)


def _animation_message(file_id: str = 'BANNER_FID') -> MagicMock:
    message = MagicMock()
    message.animation.file_id = file_id
    message.rich_message = None
    return message


@pytest.fixture(autouse=True)
def _banner(monkeypatch, tmp_path):
    banner = tmp_path / 'bot-banner.mp4'
    banner.write_bytes(b'mp4')
    monkeypatch.setattr(settings, 'ANIMATED_BANNER_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'ANIMATED_BANNER_FILE', str(banner), raising=False)
    monkeypatch.setattr(settings, 'MAIN_MENU_RICH_LOGO_URL', 'https://cdn.example/logo.png', raising=False)
    animated_banner._reset_banner_state()
    rich_menu._reset_rich_menu_availability()
    yield
    animated_banner._reset_banner_state()
    rich_menu._reset_rich_menu_availability()


class TestState:
    def test_enabled_when_flag_on_and_file_present(self):
        assert animated_banner.is_banner_enabled() is True

    def test_flag_off_disables(self, monkeypatch):
        monkeypatch.setattr(settings, 'ANIMATED_BANNER_ENABLED', False, raising=False)
        assert animated_banner.is_banner_enabled() is False

    def test_missing_file_disables(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, 'ANIMATED_BANNER_FILE', str(tmp_path / 'nope.mp4'), raising=False)
        assert animated_banner.is_banner_enabled() is False

    def test_rejection_disables_until_restart_and_only_once(self):
        assert animated_banner.mark_banner_unavailable(_bad_request(MEDIA_ERROR)) is True
        assert animated_banner.mark_banner_unavailable(_bad_request(MEDIA_ERROR)) is False
        assert animated_banner.is_banner_enabled() is False

    def test_file_is_uploaded_once_then_file_id_reused(self):
        assert isinstance(animated_banner.get_banner_media(), FSInputFile)
        animated_banner.cache_banner_file_id(_animation_message('FID1'))
        animated_banner.cache_banner_file_id(_animation_message('FID2'))
        assert animated_banner.get_banner_media() == 'FID1'

    def test_file_id_found_inside_rich_message_dump(self):
        dump = {'blocks': [{'type': 'paragraph'}, {'type': 'animation', 'animation': {'file_id': 'RICH_FID'}}]}
        assert animated_banner._find_file_id(dump) == 'RICH_FID'

    @pytest.mark.parametrize(
        ('message', 'expected'),
        [
            (MEDIA_ERROR, True),
            ('Bad Request: RICH_MESSAGE_VIDEO_INVALID', True),
            ('Bad Request: MEDIA_EMPTY', True),
            ('Bad Request: chat not found', False),
            ('Bad Request: message is not modified', False),
            ('Bad Request: MEDIA_CAPTION_TOO_LONG', False),
            ('Bad Request: BUTTON_USER_PRIVACY_RESTRICTED', False),
        ],
    )
    def test_only_media_errors_blame_the_banner(self, message, expected):
        assert animated_banner.is_banner_media_error(_bad_request(message)) is expected


class TestRichHeader:
    def test_banner_replaces_static_image(self):
        assert rich_menu._rich_logo_html() == RICH_BANNER_TAG

    def test_static_image_when_banner_off(self, monkeypatch):
        monkeypatch.setattr(settings, 'ANIMATED_BANNER_ENABLED', False, raising=False)
        assert rich_menu._rich_logo_html() == '<img src="https://cdn.example/logo.png"/>'

    def test_explicit_none_removes_banner_too(self, monkeypatch):
        monkeypatch.setattr(settings, 'MAIN_MENU_RICH_LOGO_URL', 'none', raising=False)
        assert rich_menu._rich_logo_html() == ''

    def test_banner_file_travels_with_rich_message(self):
        message = rich_menu._input_rich_message(f'{RICH_BANNER_TAG}<p>меню</p>', 'ru')
        assert len(message.media) == 1
        assert message.media[0].id == RICH_MEDIA_ID
        assert isinstance(message.media[0].media, InputMediaAnimation)

    def test_no_media_without_banner_tag(self):
        assert rich_menu._input_rich_message('<p>меню</p>', 'ru').media is None

    def test_menu_media_error_drops_banner_for_retry(self):
        assert rich_menu._retry_without_logo(_bad_request(MEDIA_ERROR)) is True
        assert rich_menu._rich_logo_html() == '<img src="https://cdn.example/logo.png"/>'

    def test_unrelated_menu_error_keeps_banner(self):
        assert rich_menu._retry_without_logo(_bad_request('Bad Request: message to edit not found')) is False
        assert animated_banner.is_banner_enabled() is True
        assert rich_menu._resolve_rich_logo_url() == 'https://cdn.example/logo.png'


class TestRichNotification:
    @pytest.fixture(autouse=True)
    def _rich_on(self, monkeypatch):
        monkeypatch.setattr(settings, 'USER_NOTIFICATIONS_RICH_ENABLED', True, raising=False)
        monkeypatch.setattr(settings, 'MAIN_MENU_RICH_INLINE_BUTTONS', False, raising=False)
        monkeypatch.setattr(rich_notify, 'is_rich_menu_enabled', lambda: True)

    async def test_banner_leads_notification(self):
        bot = AsyncMock()
        bot.send_rich_message.return_value = _animation_message('NOTIFY_FID')

        assert await rich_notify.try_send_rich_notification(bot, 42, 'Заголовок\nтекст', with_logo=True)

        rich_message = bot.send_rich_message.await_args.kwargs['rich_message']
        assert rich_message.html.startswith(RICH_BANNER_TAG)
        assert rich_message.media[0].id == RICH_MEDIA_ID
        assert animated_banner.get_banner_media() == 'NOTIFY_FID'

    async def test_rejected_banner_retries_with_static_logo(self):
        bot = AsyncMock()
        bot.send_rich_message.side_effect = [_bad_request(MEDIA_ERROR), None]

        assert await rich_notify.try_send_rich_notification(bot, 42, 'текст', with_logo=True)

        retry = bot.send_rich_message.await_args_list[1].kwargs['rich_message']
        assert retry.html.startswith('<img src="https://cdn.example/logo.png"/>')
        assert retry.media is None

    async def test_dead_chat_does_not_disable_banner(self):
        bot = AsyncMock()
        bot.send_rich_message.side_effect = _bad_request('Bad Request: chat not found')

        assert await rich_notify.try_send_rich_notification(bot, 42, 'текст', with_logo=True) is False
        assert animated_banner.is_banner_enabled() is True


class TestRichAdmin:
    async def test_rejected_banner_retries_with_static_logo(self, monkeypatch):
        monkeypatch.setattr(rich_admin, 'is_rich_admin_enabled', lambda: True)
        bot = AsyncMock()
        bot.send_rich_message.side_effect = [_bad_request(MEDIA_ERROR), None]

        assert await rich_admin.try_send_rich_admin_message(bot, -100, f'{RICH_BANNER_TAG}<p>старт</p>')

        retry = bot.send_rich_message.await_args_list[1].kwargs['rich_message']
        assert retry.html == '<img src="https://cdn.example/logo.png"/><p>старт</p>'


class TestSendLogo:
    async def test_sends_animation_and_caches_file_id(self):
        send_animation = AsyncMock(return_value=_animation_message('CLASSIC_FID'))
        send_photo = AsyncMock()

        await mp.send_logo(send_animation, send_photo, chat_id=1, caption='экран')

        assert isinstance(send_animation.await_args.kwargs['animation'], FSInputFile)
        assert send_animation.await_args.kwargs['caption'] == 'экран'
        send_photo.assert_not_awaited()
        assert animated_banner.get_banner_media() == 'CLASSIC_FID'

    async def test_rejected_banner_falls_back_to_photo(self, monkeypatch):
        monkeypatch.setattr(mp, 'get_logo_media', lambda: 'LOGO_FID')
        send_animation = AsyncMock(side_effect=_bad_request(MEDIA_ERROR))
        send_photo = AsyncMock()

        await mp.send_logo(send_animation, send_photo, chat_id=1, caption='экран')

        assert send_photo.await_args.kwargs['photo'] == 'LOGO_FID'
        assert animated_banner.is_banner_enabled() is False

    async def test_unrelated_error_is_raised_and_banner_kept(self):
        send_animation = AsyncMock(side_effect=_bad_request('Bad Request: BUTTON_USER_PRIVACY_RESTRICTED'))

        with pytest.raises(TelegramBadRequest):
            await mp.send_logo(send_animation, AsyncMock(), chat_id=1, caption='экран')
        assert animated_banner.is_banner_enabled() is True

    async def test_banner_off_sends_photo(self, monkeypatch):
        monkeypatch.setattr(settings, 'ANIMATED_BANNER_ENABLED', False, raising=False)
        monkeypatch.setattr(mp, 'get_logo_media', lambda: 'LOGO_FID')
        send_animation = AsyncMock()
        send_photo = AsyncMock()

        await mp.send_logo(send_animation, send_photo, chat_id=1, caption='экран')

        send_animation.assert_not_awaited()
        send_photo.assert_awaited_once()


def _screen_message() -> MagicMock:
    """Экран, уже показанный с баннером: анимация с подписью, без текста."""
    message = MagicMock()
    message.photo = None
    message.animation.file_id = 'OLD_FID'
    message.text = None
    message.caption = None
    message.edit_media = AsyncMock()
    message.delete = AsyncMock()
    message.answer_animation = AsyncMock()
    message.answer_photo = AsyncMock()
    return message


class TestClassicScreens:
    @pytest.fixture(autouse=True)
    def _logo_mode(self, monkeypatch, tmp_path):
        logo = tmp_path / 'vpn_logo.png'
        logo.write_bytes(b'png')
        monkeypatch.setattr(settings, 'ENABLE_LOGO_MODE', True, raising=False)
        monkeypatch.setattr(mp, 'LOGO_PATH', logo)
        monkeypatch.setattr(pm, 'LOGO_PATH', logo)
        monkeypatch.setattr(mp, 'get_logo_media', lambda: 'LOGO_FID')
        monkeypatch.setattr(pm, 'get_logo_media', lambda: 'LOGO_FID')

    async def test_patched_edit_swaps_banner_in_place(self):
        message = _screen_message()

        await mp._edit_with_photo(message, 'новый экран')

        media = message.edit_media.await_args.args[0]
        assert isinstance(media, InputMediaAnimation)
        assert media.caption == 'новый экран'
        message.delete.assert_not_awaited()

    async def test_patched_edit_retries_with_photo_when_banner_rejected(self):
        message = _screen_message()
        message.edit_media.side_effect = [_bad_request(MEDIA_ERROR), None]

        await mp._edit_with_photo(message, 'новый экран')

        retry = message.edit_media.await_args_list[1].args[0]
        assert isinstance(retry, InputMediaPhoto)
        assert retry.media == 'LOGO_FID'

    async def test_patched_answer_sends_banner(self):
        message = _screen_message()

        await mp._answer_with_photo(message, 'экран')

        message.answer_animation.assert_awaited_once()
        message.answer_photo.assert_not_awaited()

    async def test_callback_screen_edits_to_banner(self):
        callback = MagicMock()
        callback.message = _screen_message()

        await pm.edit_or_answer_photo(callback, 'экран', MagicMock())

        media = callback.message.edit_media.await_args.args[0]
        assert isinstance(media, InputMediaAnimation)

    async def test_callback_screen_falls_back_to_photo_when_banner_rejected(self):
        callback = MagicMock()
        callback.message = _screen_message()
        callback.message.edit_media.side_effect = [_bad_request(MEDIA_ERROR), None]

        await pm.edit_or_answer_photo(callback, 'экран', MagicMock())

        retry = callback.message.edit_media.await_args_list[1].args[0]
        assert isinstance(retry, InputMediaPhoto)

    async def test_text_mode_deletes_banner_screen_instead_of_editing_text(self):
        callback = MagicMock()
        callback.message = _screen_message()
        callback.message.answer = AsyncMock()

        await pm.edit_or_answer_photo(callback, 'экран', MagicMock(), force_text=True)

        callback.message.delete.assert_awaited_once()
        callback.message.answer.assert_awaited_once()


class TestPrewarm:
    def _service(self, bot) -> StartupNotificationService:
        service = StartupNotificationService(bot)
        service.chat_id = -100123
        service.topic_id = None
        return service

    async def test_uploads_once_caches_and_deletes(self):
        bot = MagicMock()
        bot.send_animation = AsyncMock(return_value=_animation_message('WARM_FID'))
        bot.delete_message = AsyncMock()

        assert await self._service(bot).prewarm_banner() is True

        assert animated_banner.get_banner_media() == 'WARM_FID'
        bot.delete_message.assert_awaited_once()

    async def test_skipped_when_banner_off(self, monkeypatch):
        monkeypatch.setattr(settings, 'ANIMATED_BANNER_ENABLED', False, raising=False)
        bot = MagicMock()
        bot.send_animation = AsyncMock()

        assert await self._service(bot).prewarm_banner() is False
        bot.send_animation.assert_not_awaited()

    async def test_rejected_file_disables_banner(self):
        bot = MagicMock()
        bot.send_animation = AsyncMock(side_effect=_bad_request(MEDIA_ERROR))

        assert await self._service(bot).prewarm_banner() is False
        assert animated_banner.is_banner_enabled() is False
