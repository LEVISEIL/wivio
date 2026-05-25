from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CachedVideo:
    normalized_url: str
    original_url: str
    platform: str
    title: str
    caption: str
    thumbnail_url: str | None
    telegram_file_id: str
    telegram_file_unique_id: str | None
    file_size: int | None
    duration: int | None
    width: int | None
    height: int | None


@dataclass(frozen=True)
class UserStats:
    total_users: int
    active_today: int
    active_7_days: int
    new_today: int
    inline_queries: int
    successful_requests: int
    failed_requests: int
    cached_videos: int
    errors_24h: int
    errors_7d: int
