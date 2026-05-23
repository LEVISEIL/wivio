from __future__ import annotations

from collections.abc import Mapping
import logging

import aiosqlite

from bot.database.models import CachedVideo

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
            logger.exception("Could not upsert cached video normalized_url=%s", video.normalized_url)
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
