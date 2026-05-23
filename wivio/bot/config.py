from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    bot_username: str
    upload_chat_id: int

    bot_mode: str
    webhook_url: str
    webhook_secret: str
    webhook_path: str

    host: str
    port: int
    healthcheck_path: str

    database_path: Path
    downloads_dir: Path
    logs_dir: Path

    max_video_size_mb: int
    inline_download_timeout: int
    download_retries: int
    upload_retries: int

    rate_limit_per_minute: int
    cooldown_seconds: int
    inline_cache_time: int
    temp_file_ttl_seconds: int
    cleanup_interval_seconds: int

    log_level: str
    debug: bool

    @property
    def max_video_size_bytes(self) -> int:
        return self.max_video_size_mb * 1024 * 1024

    @property
    def webhook_endpoint(self) -> str:
        url = self.webhook_url.rstrip("/")
        if not url:
            return url
        if url.endswith(self.webhook_path):
            return url
        return f"{url}{self.webhook_path}"


def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("BOT_TOKEN", "").strip()
    username = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
    upload_chat_id = os.getenv("UPLOAD_CHAT_ID", "").strip()

    missing = [
        name
        for name, value in {
            "BOT_TOKEN": token,
            "BOT_USERNAME": username,
            "UPLOAD_CHAT_ID": upload_chat_id,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    return Settings(
        bot_token=token,
        bot_username=username,
        upload_chat_id=int(upload_chat_id),
        bot_mode=os.getenv("BOT_MODE", "polling").strip().lower(),
        webhook_url=os.getenv("WEBHOOK_URL", "").strip(),
        webhook_secret=os.getenv("WEBHOOK_SECRET", "").strip(),
        webhook_path=os.getenv("WEBHOOK_PATH", "/webhook").strip() or "/webhook",
        host=os.getenv("HOST", "0.0.0.0").strip(),
        port=_int("PORT", 8080),
        healthcheck_path=os.getenv("HEALTHCHECK_PATH", "/healthz").strip() or "/healthz",
        database_path=Path(os.getenv("DATABASE_PATH", "./data/bot.sqlite3")),
        downloads_dir=Path(os.getenv("DOWNLOADS_DIR", "./downloads")),
        logs_dir=Path(os.getenv("LOGS_DIR", "./logs")),
        max_video_size_mb=_int("MAX_VIDEO_SIZE_MB", 49),
        inline_download_timeout=_int("INLINE_DOWNLOAD_TIMEOUT", 45),
        download_retries=_int("DOWNLOAD_RETRIES", 2),
        upload_retries=_int("UPLOAD_RETRIES", 2),
        rate_limit_per_minute=_int("RATE_LIMIT_PER_MINUTE", 6),
        cooldown_seconds=_int("COOLDOWN_SECONDS", 0),
        inline_cache_time=_int("INLINE_CACHE_TIME", 86400),
        temp_file_ttl_seconds=_int("TEMP_FILE_TTL_SECONDS", 3600),
        cleanup_interval_seconds=_int("CLEANUP_INTERVAL_SECONDS", 900),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        debug=_bool(os.getenv("DEBUG"), False),
    )
