from __future__ import annotations

import logging
from collections.abc import Mapping

import aiosqlite
from aiogram.types import User

from bot.database.models import CachedVideo, UserStats

logger = logging.getLogger(__name__)


class VideoRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def get(self, normalized_url: str) -> CachedVideo | None:
        try:
            cursor = await self.connection.execute(
                "SELECT * FROM videos WHERE normalized_url = ?",
                (normalized_url,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                return None

            await self.connection.execute(
                "UPDATE videos SET last_used_at = CURRENT_TIMESTAMP WHERE normalized_url = ?",
                (normalized_url,),
            )
            await self.connection.commit()
            return _row_to_video(row)
        except Exception:
            logger.exception("Could not read cached video normalized_url=%s", normalized_url)
            raise

    async def upsert(self, video: CachedVideo) -> None:
        try:
            await self.connection.execute(
                """
                INSERT INTO videos (
                    normalized_url,
                    original_url,
                    platform,
                    title,
                    caption,
                    thumbnail_url,
                    telegram_file_id,
                    telegram_file_unique_id,
                    file_size,
                    duration,
                    width,
                    height
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(normalized_url) DO UPDATE SET
                    original_url = excluded.original_url,
                    platform = excluded.platform,
                    title = excluded.title,
                    caption = excluded.caption,
                    thumbnail_url = excluded.thumbnail_url,
                    telegram_file_id = excluded.telegram_file_id,
                    telegram_file_unique_id = excluded.telegram_file_unique_id,
                    file_size = excluded.file_size,
                    duration = excluded.duration,
                    width = excluded.width,
                    height = excluded.height,
                    last_used_at = CURRENT_TIMESTAMP
                """,
                (
                    video.normalized_url,
                    video.original_url,
                    video.platform,
                    video.title,
                    video.caption,
                    video.thumbnail_url,
                    video.telegram_file_id,
                    video.telegram_file_unique_id,
                    video.file_size,
                    video.duration,
                    video.width,
                    video.height,
                ),
            )
            await self.connection.commit()
        except Exception:
            logger.exception(
                "Could not upsert cached video normalized_url=%s",
                video.normalized_url,
            )
            raise


class EventRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def add(
        self,
        normalized_url: str,
        user_id: int | None,
        platform: str | None,
        status: str,
        error: str | None = None,
    ) -> None:
        try:
            await self.connection.execute(
                """
                INSERT INTO download_events (normalized_url, user_id, platform, status, error)
                VALUES (?, ?, ?, ?, ?)
                """,
                (normalized_url, user_id, platform, status, error),
            )
            await self.connection.commit()
        except Exception:
            logger.exception(
                "Could not store download event normalized_url=%s status=%s",
                normalized_url,
                status,
            )
            raise


class UserRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def touch(self, user: User, event: str) -> None:
        column = _event_counter_column(event)
        try:
            await self.connection.execute(
                f"""
                INSERT INTO users (
                    telegram_user_id,
                    username,
                    first_name,
                    last_name,
                    {column}
                )
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    last_seen_at = CURRENT_TIMESTAMP,
                    {column} = {column} + 1
                """,
                (
                    user.id,
                    user.username,
                    user.first_name,
                    user.last_name,
                ),
            )
            await self.connection.commit()
        except Exception:
            logger.exception("Could not touch user user_id=%s event=%s", user.id, event)
            raise

    async def increment_success(self, user_id: int) -> None:
        await self._increment_counter(user_id, "successful_requests_count")

    async def increment_failure(self, user_id: int) -> None:
        await self._increment_counter(user_id, "failed_requests_count")

    async def stats(self) -> UserStats:
        try:
            cursor = await self.connection.execute(
                """
                SELECT
                    COUNT(*) AS total_users,
                    SUM(last_seen_at >= datetime('now', '-1 day')) AS active_today,
                    SUM(last_seen_at >= datetime('now', '-7 days')) AS active_7_days,
                    SUM(first_seen_at >= datetime('now', '-1 day')) AS new_today,
                    COALESCE(SUM(inline_queries_count), 0) AS inline_queries,
                    COALESCE(SUM(successful_requests_count), 0) AS successful_requests,
                    COALESCE(SUM(failed_requests_count), 0) AS failed_requests
                FROM users
                """
            )
            row = await cursor.fetchone()
            await cursor.close()

            videos_cursor = await self.connection.execute("SELECT COUNT(*) AS total FROM videos")
            videos_row = await videos_cursor.fetchone()
            await videos_cursor.close()

            errors_cursor = await self.connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM download_events
                WHERE status IN ('error', 'timeout')
                  AND created_at >= datetime('now', '-1 day')
                """
            )
            errors_row = await errors_cursor.fetchone()
            await errors_cursor.close()

            return UserStats(
                total_users=_int_value(row["total_users"]),
                active_today=_int_value(row["active_today"]),
                active_7_days=_int_value(row["active_7_days"]),
                new_today=_int_value(row["new_today"]),
                inline_queries=_int_value(row["inline_queries"]),
                successful_requests=_int_value(row["successful_requests"]),
                failed_requests=_int_value(row["failed_requests"]),
                cached_videos=_int_value(videos_row["total"]),
                errors_24h=_int_value(errors_row["total"]),
            )
        except Exception:
            logger.exception("Could not build user stats")
            raise

    async def _increment_counter(self, user_id: int, column: str) -> None:
        try:
            await self.connection.execute(
                f"""
                INSERT INTO users (telegram_user_id, {column})
                VALUES (?, 1)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    last_seen_at = CURRENT_TIMESTAMP,
                    {column} = {column} + 1
                """,
                (user_id,),
            )
            await self.connection.commit()
        except Exception:
            logger.exception("Could not increment %s for user_id=%s", column, user_id)
            raise


def _event_counter_column(event: str) -> str:
    if event == "start":
        return "start_count"
    if event == "inline":
        return "inline_queries_count"
    raise ValueError(f"Unsupported user event: {event}")


def _int_value(value: object) -> int:
    if value is None:
        return 0
    return int(value)


def _row_to_video(row: Mapping[str, object]) -> CachedVideo:
    return CachedVideo(
        normalized_url=str(row["normalized_url"]),
        original_url=str(row["original_url"]),
        platform=str(row["platform"]),
        title=str(row["title"]),
        caption=str(row["caption"]),
        thumbnail_url=row["thumbnail_url"],  # type: ignore[arg-type]
        telegram_file_id=str(row["telegram_file_id"]),
        telegram_file_unique_id=row["telegram_file_unique_id"],  # type: ignore[arg-type]
        file_size=row["file_size"],  # type: ignore[arg-type]
        duration=row["duration"],  # type: ignore[arg-type]
        width=row["width"],  # type: ignore[arg-type]
        height=row["height"],  # type: ignore[arg-type]
    )
