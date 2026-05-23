import asyncio
from pathlib import Path

import pytest

from bot.database.models import CachedVideo
from bot.services.downloader import DownloadedVideo
from bot.services.video_cache import VideoCacheService
from bot.utils.urls import Platform, ParsedVideoUrl


class FakeVideoRepository:
    def __init__(self) -> None:
        self.items: dict[str, CachedVideo] = {}

    async def get(self, normalized_url: str) -> CachedVideo | None:
        return self.items.get(normalized_url)

    async def upsert(self, video: CachedVideo) -> None:
        self.items[video.normalized_url] = video


class FakeEventRepository:
    def __init__(self) -> None:
        self.events: list[tuple[str, int | None, str | None, str, str | None]] = []

    async def add(
        self,
        normalized_url: str,
        user_id: int | None,
        platform: str | None,
        status: str,
        error: str | None = None,
    ) -> None:
        self.events.append((normalized_url, user_id, platform, status, error))


class FakeDownloader:
    def __init__(self, downloaded: DownloadedVideo) -> None:
        self.downloaded = downloaded
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def download(self, parsed_url: ParsedVideoUrl) -> DownloadedVideo:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self.downloaded


class FakeUploader:
    def __init__(self) -> None:
        self.calls = 0

    async def upload(self, video: DownloadedVideo) -> tuple[str, str]:
        self.calls += 1
        return "telegram-file-id", "telegram-unique-id"


def parsed_url() -> ParsedVideoUrl:
    return ParsedVideoUrl(
        original_url="https://vm.tiktok.com/ZNRnPAR4S/",
        normalized_url="https://vm.tiktok.com/ZNRnPAR4S",
        platform=Platform.TIKTOK,
    )


def cached_video() -> CachedVideo:
    return CachedVideo(
        normalized_url=parsed_url().normalized_url,
        original_url=parsed_url().original_url,
        platform="tiktok",
        title="Cached",
        caption="<b>Cached</b>",
        thumbnail_url=None,
        telegram_file_id="cached-file-id",
        telegram_file_unique_id="cached-unique-id",
        file_size=10,
        duration=1,
        width=100,
        height=100,
    )


def downloaded_video(tmp_path: Path) -> DownloadedVideo:
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    return DownloadedVideo(
        original_url=parsed_url().original_url,
        normalized_url=parsed_url().normalized_url,
        platform="tiktok",
        video_path=video_path,
        thumbnail_path=None,
        thumbnail_url=None,
        title="New Video",
        caption="<b>New Video</b>",
        duration=12,
        width=720,
        height=1280,
        file_size=5,
    )


@pytest.mark.asyncio
async def test_get_or_enqueue_returns_cached_video_without_background_work(tmp_path: Path) -> None:
    videos = FakeVideoRepository()
    videos.items[parsed_url().normalized_url] = cached_video()
    events = FakeEventRepository()
    downloader = FakeDownloader(downloaded_video(tmp_path))
    uploader = FakeUploader()
    service = VideoCacheService(videos, events, downloader, uploader, timeout_seconds=1)

    result, status = await service.get_or_enqueue(parsed_url(), user_id=42)

    assert result == cached_video()
    assert status == "cached"
    assert downloader.calls == 0
    assert events.events[-1][3] == "cache_hit"


@pytest.mark.asyncio
async def test_get_or_enqueue_queues_new_video_and_reuses_inflight_task(tmp_path: Path) -> None:
    videos = FakeVideoRepository()
    events = FakeEventRepository()
    downloader = FakeDownloader(downloaded_video(tmp_path))
    uploader = FakeUploader()
    service = VideoCacheService(videos, events, downloader, uploader, timeout_seconds=5)

    result, status = await service.get_or_enqueue(parsed_url(), user_id=42)
    assert result is None
    assert status == "queued"

    await asyncio.wait_for(downloader.started.wait(), timeout=1)

    result, status = await service.get_or_enqueue(parsed_url(), user_id=42)
    assert result is None
    assert status == "in_progress"
    assert downloader.calls == 1

    downloader.release.set()
    await asyncio.wait_for(next(iter(service._inflight.values())), timeout=1)

    result, status = await service.get_or_enqueue(parsed_url(), user_id=42)

    assert result is not None
    assert result.telegram_file_id == "telegram-file-id"
    assert status == "cached"
    assert uploader.calls == 1
    assert [event[3] for event in events.events] == [
        "queued",
        "in_progress",
        "created",
        "cache_hit",
    ]


@pytest.mark.asyncio
async def test_background_failure_is_logged_and_can_be_retried(tmp_path: Path) -> None:
    class FailingDownloader(FakeDownloader):
        async def download(self, parsed_url: ParsedVideoUrl) -> DownloadedVideo:
            self.calls += 1
            raise RuntimeError("download failed")

    videos = FakeVideoRepository()
    events = FakeEventRepository()
    downloader = FailingDownloader(downloaded_video(tmp_path))
    uploader = FakeUploader()
    service = VideoCacheService(videos, events, downloader, uploader, timeout_seconds=5)

    result, status = await service.get_or_enqueue(parsed_url(), user_id=42)
    assert result is None
    assert status == "queued"

    task = next(iter(service._inflight.values()))
    await asyncio.wait_for(task, timeout=1)

    assert parsed_url().normalized_url not in service._inflight
    assert events.events[-1][3] == "error"

    result, status = await service.get_or_enqueue(parsed_url(), user_id=42)

    assert result is None
    assert status == "queued"
    task = next(iter(service._inflight.values()))
    await asyncio.wait_for(task, timeout=1)
    assert downloader.calls == 2
