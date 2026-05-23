from __future__ import annotations

import asyncio
import builtins
import logging
from collections import defaultdict
from dataclasses import dataclass
from time import monotonic

from bot.database.models import CachedVideo
from bot.database.repositories import EventRepository, VideoRepository
from bot.services.downloader import VideoDownloader
from bot.services.errors import TimeoutError
from bot.services.uploader import TelegramUploader
from bot.utils.urls import ParsedVideoUrl

logger = logging.getLogger(__name__)

FAILED_STATUS = "error"
TIMEOUT_STATUS = "timeout"
FAILURE_TTL_SECONDS = 300


@dataclass(frozen=True)
class ProcessingFailure:
    status: str
    error: str
    created_at: float


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
        self._failures: dict[str, ProcessingFailure] = {}

    async def get_or_create(
        self,
        parsed_url: ParsedVideoUrl,
        user_id: int | None,
    ) -> tuple[CachedVideo, bool]:
        cached = await self.videos.get(parsed_url.normalized_url)
        if cached is not None:
            self._failures.pop(parsed_url.normalized_url, None)
            logger.info(
                "Cache hit normalized_url=%s user_id=%s",
                parsed_url.normalized_url,
                user_id,
            )
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
                self._failures.pop(parsed_url.normalized_url, None)
                logger.info(
                    "Cache hit after wait normalized_url=%s user_id=%s",
                    parsed_url.normalized_url,
                    user_id,
                )
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
            except builtins.TimeoutError as exc:
                logger.warning(
                    "Video processing timed out normalized_url=%s user_id=%s timeout_seconds=%s",
                    parsed_url.normalized_url,
                    user_id,
                    self.timeout_seconds,
                )
                await self.events.add(
                    parsed_url.normalized_url,
                    user_id,
                    parsed_url.platform.value,
                    "timeout",
                    str(exc),
                )
                raise TimeoutError() from exc
            except Exception as exc:
                logger.exception(
                    "Video processing failed normalized_url=%s user_id=%s",
                    parsed_url.normalized_url,
                    user_id,
                )
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
            logger.info(
                "Cache hit normalized_url=%s user_id=%s",
                parsed_url.normalized_url,
                user_id,
            )
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
                logger.info(
                    "Cache hit after wait normalized_url=%s user_id=%s",
                    parsed_url.normalized_url,
                    user_id,
                )
                await self.events.add(
                    parsed_url.normalized_url,
                    user_id,
                    parsed_url.platform.value,
                    "cache_hit_after_wait",
                )
                return cached, "cached"

            task = self._inflight.get(parsed_url.normalized_url)
            if task is not None and not task.done():
                logger.info(
                    "Video already in progress normalized_url=%s user_id=%s",
                    parsed_url.normalized_url,
                    user_id,
                )
                await self.events.add(
                    parsed_url.normalized_url,
                    user_id,
                    parsed_url.platform.value,
                    "in_progress",
                )
                return None, "in_progress"

            failure = self._get_recent_failure(parsed_url.normalized_url)
            if failure is not None:
                logger.info(
                    "Recent video processing failure normalized_url=%s user_id=%s status=%s",
                    parsed_url.normalized_url,
                    user_id,
                    failure.status,
                )
                await self.events.add(
                    parsed_url.normalized_url,
                    user_id,
                    parsed_url.platform.value,
                    failure.status,
                    failure.error[:500],
                )
                return None, failure.status

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
            logger.info(
                "Queued background video processing normalized_url=%s user_id=%s",
                parsed_url.normalized_url,
                user_id,
            )
            await self.events.add(
                parsed_url.normalized_url,
                user_id,
                parsed_url.platform.value,
                "queued",
            )
            return None, "queued"

    async def wait_for_cached(
        self,
        parsed_url: ParsedVideoUrl,
        user_id: int | None,
        timeout_seconds: int | None = None,
    ) -> CachedVideo | None:
        cached = await self.videos.get(parsed_url.normalized_url)
        if cached is not None:
            logger.info(
                "Cache became ready before wait normalized_url=%s user_id=%s",
                parsed_url.normalized_url,
                user_id,
            )
            return cached

        task = self._inflight.get(parsed_url.normalized_url)
        if task is None:
            logger.info(
                "No in-flight task to wait for normalized_url=%s user_id=%s",
                parsed_url.normalized_url,
                user_id,
            )
            return None

        wait_timeout = timeout_seconds or self.timeout_seconds
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=wait_timeout)
        except builtins.TimeoutError:
            logger.info(
                "Video is still processing after inline wait "
                "normalized_url=%s user_id=%s timeout_seconds=%s",
                parsed_url.normalized_url,
                user_id,
                wait_timeout,
            )

        cached = await self.videos.get(parsed_url.normalized_url)
        if cached is None:
            logger.info(
                "Video is not cached yet after inline wait normalized_url=%s user_id=%s",
                parsed_url.normalized_url,
                user_id,
            )
        else:
            logger.info(
                "Video is ready after inline wait normalized_url=%s user_id=%s",
                parsed_url.normalized_url,
                user_id,
            )
        return cached

    async def _download_upload_and_store(self, parsed_url: ParsedVideoUrl) -> CachedVideo:
        logger.info(
            "Processing video normalized_url=%s platform=%s",
            parsed_url.normalized_url,
            parsed_url.platform.value,
        )
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
        self._failures.pop(parsed_url.normalized_url, None)
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
        except builtins.TimeoutError as exc:
            self._remember_failure(
                parsed_url.normalized_url,
                TIMEOUT_STATUS,
                str(exc),
            )
            await self.events.add(
                parsed_url.normalized_url,
                user_id,
                parsed_url.platform.value,
                "timeout",
                str(exc),
            )
            logger.warning("Background processing timed out for %s", parsed_url.normalized_url)
        except Exception as exc:
            self._remember_failure(
                parsed_url.normalized_url,
                FAILED_STATUS,
                str(exc),
            )
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
            logger.info(
                "Background processing completed normalized_url=%s",
                parsed_url.normalized_url,
            )

    def _remember_failure(self, normalized_url: str, status: str, error: str) -> None:
        self._failures[normalized_url] = ProcessingFailure(
            status=status,
            error=error[:500],
            created_at=monotonic(),
        )
        logger.info(
            "Remembered video processing failure normalized_url=%s status=%s",
            normalized_url,
            status,
        )

    def _get_recent_failure(self, normalized_url: str) -> ProcessingFailure | None:
        failure = self._failures.get(normalized_url)
        if failure is None:
            return None
        if monotonic() - failure.created_at > FAILURE_TTL_SECONDS:
            self._failures.pop(normalized_url, None)
            logger.info("Forgot expired processing failure normalized_url=%s", normalized_url)
            return None
        return failure

    def _forget_inflight(self, normalized_url: str, task: asyncio.Task[None]) -> None:
        if self._inflight.get(normalized_url) is task:
            self._inflight.pop(normalized_url, None)
            logger.debug(
                "Forgot in-flight task normalized_url=%s done=%s",
                normalized_url,
                task.done(),
            )
