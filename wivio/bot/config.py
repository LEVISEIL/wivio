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


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    return int(raw)


def _optional_path(name: str) -> Path | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    return Path(raw.strip()).expanduser()


def _int_set(name: str) -> frozenset[int]:
    raw = os.getenv(name, "")
    values: set[int] = set()
    for item in raw.split(","):
        value = item.strip()
        if value:
            values.add(int(value))
    return frozenset(values)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    bot_username: str
    upload_chat_id: int
    welcome_forward_chat_id: int | None
    welcome_forward_message_id: int | None
    welcome_animation_url: str
    welcome_animation_path: Path | None
    welcome_animation_file_id: str
    welcome_video_file_id: str

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
    inline_ready_wait_seconds: int
    max_cached_videos: int
    cache_trim_to_videos: int
    instagram_cookies_path: Path | None
    download_retries: int
    upload_retries: int

    rate_limit_per_minute: int
    cooldown_seconds: int
    inline_cache_time: int
    temp_file_ttl_seconds: int
    cleanup_interval_seconds: int

    log_level: str
    alerts_enabled: bool
    alert_bot_token: str
    alert_chat_id: str
    alert_message_thread_id: int | None
    alert_level: str
    alert_ssl_verify: bool
    admin_user_ids: frozenset[int]
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
        welcome_forward_chat_id=_optional_int("WELCOME_FORWARD_CHAT_ID"),
        welcome_forward_message_id=_optional_int("WELCOME_FORWARD_MESSAGE_ID"),
        welcome_animation_url=os.getenv("WELCOME_ANIMATION_URL", "").strip(),
        welcome_animation_path=_optional_path("WELCOME_ANIMATION_PATH"),
        welcome_animation_file_id=os.getenv("WELCOME_ANIMATION_FILE_ID", "").strip(),
        welcome_video_file_id=os.getenv("WELCOME_VIDEO_FILE_ID", "").strip(),
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
        inline_ready_wait_seconds=_int("INLINE_READY_WAIT_SECONDS", 12),
        max_cached_videos=_int("MAX_CACHED_VIDEOS", 5000),
        cache_trim_to_videos=_int("CACHE_TRIM_TO_VIDEOS", 4500),
        instagram_cookies_path=_optional_path("INSTAGRAM_COOKIES_PATH"),
        download_retries=_int("DOWNLOAD_RETRIES", 2),
        upload_retries=_int("UPLOAD_RETRIES", 2),
        rate_limit_per_minute=_int("RATE_LIMIT_PER_MINUTE", 6),
        cooldown_seconds=_int("COOLDOWN_SECONDS", 0),
        inline_cache_time=_int("INLINE_CACHE_TIME", 86400),
        temp_file_ttl_seconds=_int("TEMP_FILE_TTL_SECONDS", 3600),
        cleanup_interval_seconds=_int("CLEANUP_INTERVAL_SECONDS", 900),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        alerts_enabled=_bool(os.getenv("ALERTS_ENABLED"), False),
        alert_bot_token=os.getenv("ALERT_BOT_TOKEN", token).strip(),
        alert_chat_id=os.getenv("ALERT_CHAT_ID", "").strip(),
        alert_message_thread_id=_optional_int("ALERT_MESSAGE_THREAD_ID"),
        alert_level=os.getenv("ALERT_LEVEL", "ERROR").strip().upper(),
        alert_ssl_verify=_bool(os.getenv("ALERT_SSL_VERIFY"), True),
        admin_user_ids=_int_set("ADMIN_USER_IDS"),
        debug=_bool(os.getenv("DEBUG"), False),
    )
