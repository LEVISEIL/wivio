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
