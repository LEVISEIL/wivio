from pathlib import Path

import pytest

from bot.database.connection import Database
from bot.database.models import CachedVideo
from bot.database.repositories import EventRepository, UserRepository, VideoRepository


class FakeTelegramUser:
    id = 42
    username = "tester"
    first_name = "Test"
    last_name = "User"


def video(title: str = "Title", file_id: str = "file-id") -> CachedVideo:
    return CachedVideo(
        normalized_url="https://youtube.com/shorts/aRa1aCDEj4M",
        original_url="https://youtube.com/shorts/aRa1aCDEj4M?si=share",
        platform="youtube_shorts",
        title=title,
        caption="<b>Title</b>",
        thumbnail_url="https://example.com/thumb.jpg",
        telegram_file_id=file_id,
        telegram_file_unique_id="unique-id",
        file_size=123,
        duration=10,
        width=720,
        height=1280,
    )


@pytest.mark.asyncio
async def test_database_migrates_and_repositories_store_video_and_events(tmp_path: Path) -> None:
    database = Database(tmp_path / "bot.sqlite3")
    await database.connect()
    try:
        await database.migrate()
        videos = VideoRepository(database.connection)
        events = EventRepository(database.connection)

        await videos.upsert(video())
        cached = await videos.get("https://youtube.com/shorts/aRa1aCDEj4M")

        assert cached == video()

        await videos.upsert(video(title="Updated", file_id="new-file-id"))
        updated = await videos.get("https://youtube.com/shorts/aRa1aCDEj4M")

        assert updated is not None
        assert updated.title == "Updated"
        assert updated.telegram_file_id == "new-file-id"

        await events.add(
            "https://youtube.com/shorts/aRa1aCDEj4M",
            user_id=42,
            platform="youtube_shorts",
            status="created",
        )
        cursor = await database.connection.execute(
            "SELECT user_id, platform, status, error FROM download_events"
        )
        row = await cursor.fetchone()
        await cursor.close()

        assert dict(row) == {
            "user_id": 42,
            "platform": "youtube_shorts",
            "status": "created",
            "error": None,
        }
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_user_repository_tracks_users_and_stats(tmp_path: Path) -> None:
    database = Database(tmp_path / "bot.sqlite3")
    await database.connect()
    try:
        await database.migrate()
        videos = VideoRepository(database.connection)
        events = EventRepository(database.connection)
        users = UserRepository(database.connection)

        await users.touch(FakeTelegramUser(), "start")
        await users.touch(FakeTelegramUser(), "inline")
        await users.increment_success(42)
        await users.increment_failure(42)
        await videos.upsert(video())
        await events.add(
            "https://youtube.com/shorts/aRa1aCDEj4M",
            user_id=42,
            platform="youtube_shorts",
            status="error",
            error="failed",
        )

        stats = await users.stats()

        assert stats.total_users == 1
        assert stats.active_today == 1
        assert stats.active_7_days == 1
        assert stats.new_today == 1
        assert stats.inline_queries == 1
        assert stats.successful_requests == 1
        assert stats.failed_requests == 1
        assert stats.cached_videos == 1
        assert stats.errors_24h == 1
    finally:
        await database.close()
