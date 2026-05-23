from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import FSInputFile

from bot.services.downloader import DownloadedVideo
from bot.services.errors import UploadError
from bot.utils.retry import retry_async

logger = logging.getLogger(__name__)


class TelegramUploader:
    def __init__(self, bot: Bot, upload_chat_id: int, retries: int) -> None:
        self.bot = bot
        self.upload_chat_id = upload_chat_id
        self.retries = retries

    async def upload(self, video: DownloadedVideo) -> tuple[str, str | None]:
        async def operation() -> tuple[str, str | None]:
            thumbnail = FSInputFile(video.thumbnail_path) if video.thumbnail_path else None
            message = await self.bot.send_video(
                chat_id=self.upload_chat_id,
                video=FSInputFile(video.video_path),
                thumbnail=thumbnail,
                caption=video.caption,
                parse_mode="HTML",
                supports_streaming=True,
                duration=video.duration,
                width=video.width,
                height=video.height,
            )
            if message.video is None:
                raise UploadError("Telegram did not return a video object")
            return message.video.file_id, message.video.file_unique_id

        try:
            file_id, file_unique_id = await retry_async(operation, attempts=self.retries)
        except Exception as exc:
            raise UploadError(str(exc)) from exc

        logger.info("Uploaded %s to Telegram file_id=%s", video.normalized_url, file_id[:16])
        return file_id, file_unique_id
