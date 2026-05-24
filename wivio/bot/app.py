from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError

from bot.config import Settings
from bot.database.connection import Database
from bot.database.repositories import EventRepository, UserRepository, VideoRepository
from bot.handlers.inline import router as inline_router
from bot.handlers.start import router as start_router
from bot.middlewares.rate_limit import InlineRateLimitMiddleware
from bot.services.cleanup import CleanupScheduler
from bot.services.downloader import VideoDownloader
from bot.services.uploader import TelegramUploader
from bot.services.video_cache import VideoCacheService

logger = logging.getLogger(__name__)


async def build_app(settings: Settings) -> tuple[Bot, Dispatcher, Database, CleanupScheduler]:
    logger.info("Preparing runtime directories")
    settings.downloads_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Creating Telegram bot client")
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    await _validate_upload_chat(bot, settings.upload_chat_id)

    database = Database(settings.database_path)
    await database.connect()
    await database.migrate()
    logger.info("Database is connected and migrated: %s", settings.database_path)

    video_repo = VideoRepository(database.connection)
    event_repo = EventRepository(database.connection)
    user_repo = UserRepository(database.connection)
    downloader = VideoDownloader(
        downloads_dir=settings.downloads_dir,
        max_video_size_bytes=settings.max_video_size_bytes,
        retries=settings.download_retries,
    )
    uploader = TelegramUploader(
        bot=bot,
        upload_chat_id=settings.upload_chat_id,
        retries=settings.upload_retries,
    )
    video_cache = VideoCacheService(
        videos=video_repo,
        events=event_repo,
        downloader=downloader,
        uploader=uploader,
        timeout_seconds=settings.inline_download_timeout,
    )

    dispatcher = Dispatcher()
    dispatcher["video_cache"] = video_cache
    dispatcher["users"] = user_repo
    dispatcher["inline_cache_time"] = settings.inline_cache_time
    dispatcher["inline_ready_wait_seconds"] = settings.inline_ready_wait_seconds
    dispatcher["bot_username"] = settings.bot_username
    dispatcher["admin_user_ids"] = settings.admin_user_ids
    dispatcher.include_router(start_router)
    dispatcher.include_router(inline_router)
    dispatcher.inline_query.middleware(
        InlineRateLimitMiddleware(
            per_minute=settings.rate_limit_per_minute,
            cooldown_seconds=settings.cooldown_seconds,
        )
    )

    cleanup = CleanupScheduler(
        downloads_dir=settings.downloads_dir,
        ttl_seconds=settings.temp_file_ttl_seconds,
        interval_seconds=settings.cleanup_interval_seconds,
    )

    logger.info("Application is ready in %s mode", settings.bot_mode)
    return bot, dispatcher, database, cleanup


async def _validate_upload_chat(bot: Bot, upload_chat_id: int) -> None:
    try:
        chat = await bot.get_chat(upload_chat_id)
    except TelegramAPIError as exc:
        logger.exception("Upload chat validation failed for chat_id=%s", upload_chat_id)
        await bot.session.close()
        raise RuntimeError(
            "UPLOAD_CHAT_ID is not available for this bot. "
            "Add the bot to that private group/channel, allow it to send videos, "
            "and check that the chat id is correct."
        ) from exc

    logger.info("Upload chat is available: %s (%s)", chat.title or chat.id, chat.id)
