from __future__ import annotations

import asyncio
from collections import defaultdict
import logging

from bot.database.models import CachedVideo
from bot.database.repositories import EventRepository, VideoRepository
from bot.services.downloader import VideoDownloader
from bot.services.errors import TimeoutError
from bot.services.uploader import TelegramUploader
from bot.utils.urls import ParsedVideoUrl

logger = logging.getLogger(__name__)


class VideoCacheService:
    def __init__(
        self,
        videos: VideoRepository,
        events: EventRepository,
        downloader: VideoDownloader,
        uploader: TelegramUploader,
        timeout_seconds: int,
    ) -> None:
        self.videos = videos
        self.events = events
        self.downloader = downloader
        self.uploader = uploader
        self.timeout_seconds = timeout_seconds
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._inflight: dict[str, asyncio.Task[None]] = {}

    async def get_or_create(
        self,
        parsed_url: ParsedVideoUrl,
        user_id: int | None,
    ) -> tuple[CachedVideo, bool]:
        cached = await self.videos.get(parsed_url.normalized_url)
        if cached is not None:
            await self.events.add(
                parsed_url.normalized_url,
                user_id,
                parsed_url.platform.value,
                "cache_hit",
            )
            return cached, True

        lock = self._locks[parsed_url.normalized_url]
        async with lock:
            cached = await self.videos.get(parsed_url.normalized_url)
            if cached is not None:
                await self.events.add(
                    parsed_url.normalized_url,
                    user_id,
                    parsed_url.platform.value,
                    "cache_hit_after_wait",
                )
                return cached, True

            try:
                result = await asyncio.wait_for(
                    self._download_upload_and_store(parsed_url),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                await self.events.add(
                    parsed_url.normalized_url,
                    user_id,
                    parsed_url.platform.value,
                    "timeout",
                    str(exc),
                )
                raise TimeoutError() from exc
            except Exception as exc:
                await self.events.add(
                    parsed_url.normalized_url,
                    user_id,
                    parsed_url.platform.value,
                    "error",
                    str(exc)[:500],
                )
                raise

            await self.events.add(
                parsed_url.normalized_url,
                user_id,
                parsed_url.platform.value,
                "created",
            )
            return result, False

    async def get_or_enqueue(
        self,
        parsed_url: ParsedVideoUrl,
        user_id: int | None,
    ) -> tuple[CachedVideo | None, str]:
        cached = await self.videos.get(parsed_url.normalized_url)
        if cached is not None:
            await self.events.add(
                parsed_url.normalized_url,
                user_id,
                parsed_url.platform.value,
                "cache_hit",
            )
            return cached, "cached"

        lock = self._locks[parsed_url.normalized_url]
        async with lock:
            cached = await self.videos.get(parsed_url.normalized_url)
            if cached is not None:
                await self.events.add(
                    parsed_url.normalized_url,
                    user_id,
                    parsed_url.platform.value,
                    "cache_hit_after_wait",
                )
                return cached, "cached"

            task = self._inflight.get(parsed_url.normalized_url)
            if task is not None and not task.done():
                await self.events.add(
                    parsed_url.normalized_url,
                    user_id,
                    parsed_url.platform.value,
                    "in_progress",
                )
                return None, "in_progress"

            task = asyncio.create_task(
                self._download_upload_store_and_log(parsed_url, user_id),
                name=f"video-cache:{parsed_url.normalized_url}",
            )
            self._inflight[parsed_url.normalized_url] = task
            task.add_done_callback(
                lambda done_task, url=parsed_url.normalized_url: self._forget_inflight(
                    url,
                    done_task,
                )
            )
            await self.events.add(
                parsed_url.normalized_url,
                user_id,
                parsed_url.platform.value,
                "queued",
            )
            return None, "queued"

    async def _download_upload_and_store(self, parsed_url: ParsedVideoUrl) -> CachedVideo:
        downloaded = await self.downloader.download(parsed_url)
        file_id, file_unique_id = await self.uploader.upload(downloaded)

        cached = CachedVideo(
            normalized_url=downloaded.normalized_url,
            original_url=downloaded.original_url,
            platform=downloaded.platform,
            title=downloaded.title,
            caption=downloaded.caption,
            thumbnail_url=downloaded.thumbnail_url,
            telegram_file_id=file_id,
            telegram_file_unique_id=file_unique_id,
            file_size=downloaded.file_size,
            duration=downloaded.duration,
            width=downloaded.width,
            height=downloaded.height,
        )
        await self.videos.upsert(cached)
        logger.info("Cached %s", parsed_url.normalized_url)
        return cached

    async def _download_upload_store_and_log(
        self,
        parsed_url: ParsedVideoUrl,
        user_id: int | None,
    ) -> None:
        try:
            await asyncio.wait_for(
                self._download_upload_and_store(parsed_url),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            await self.events.add(
                parsed_url.normalized_url,
                user_id,
                parsed_url.platform.value,
                "timeout",
                str(exc),
            )
            logger.warning("Background processing timed out for %s", parsed_url.normalized_url)
        except Exception as exc:
            await self.events.add(
                parsed_url.normalized_url,
                user_id,
                parsed_url.platform.value,
                "error",
                str(exc)[:500],
            )
            logger.exception("Background processing failed for %s", parsed_url.normalized_url)
        else:
            await self.events.add(
                parsed_url.normalized_url,
                user_id,
                parsed_url.platform.value,
                "created",
            )

    def _forget_inflight(self, normalized_url: str, task: asyncio.Task[None]) -> None:
        if self._inflight.get(normalized_url) is task:
            self._inflight.pop(normalized_url, None)
