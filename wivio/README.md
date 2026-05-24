# Telegram Inline Video Downloader Bot

Inline-mode Telegram bot for downloading public videos from TikTok, Instagram Reels, and YouTube Shorts, uploading them once to a hidden Telegram chat, caching the resulting `file_id` in SQLite, and instantly reusing it in future inline results.

## How It Works

User types in any Telegram chat:

```text
@mybot https://tiktok.com/...
@mybot https://instagram.com/reel/...
@mybot https://instagram.com/p/...
@mybot https://youtube.com/shorts/...
```

Flow:

1. Bot receives an inline query.
2. Bot detects the platform and normalizes the URL.
3. If the URL exists in SQLite, bot returns `InlineQueryResultCachedVideo`.
4. If the URL is new, bot starts background processing and keeps the same inline query open for a short readiness window.
5. If the video is ready during that window, bot returns `InlineQueryResultCachedVideo` in the same inline query.
6. If it is still processing, bot returns a non-sendable loading status.
7. User taps the cached result and Telegram sends the video instantly.

## Features

- Telegram inline mode with class-based `InlineQueryHandler`
- TikTok, Instagram Reels/video posts, and YouTube Shorts support
- Automatic platform detection
- Telegram `file_id` cache in SQLite
- Async architecture with aiogram 3
- `yt-dlp` downloader service
- Hidden upload chat service
- Retry for Telegram uploads and `yt-dlp` downloads
- Anti-spam cooldown and per-minute rate limit middleware
- Temporary file storage with cleanup scheduler
- Same-query readiness wait for first-time downloads, with a non-sendable loading fallback
- Thumbnail download and Telegram upload thumbnail support
- Captions and inline result descriptions
- File size limit
- Polling and webhook modes
- Healthcheck endpoint
- Docker and Docker Compose support
- Rotating logs

## Project Structure

```text
bot/
  config.py
  app.py
  main.py
  healthcheck.py
  database/
    connection.py
    models.py
    repositories.py
    schema.sql
  handlers/
    inline.py
  middlewares/
    rate_limit.py
  services/
    cleanup.py
    downloader.py
    errors.py
    uploader.py
    video_cache.py
  utils/
    inline_results.py
    logging.py
    retry.py
    urls.py
downloads/
logs/
.env.example
requirements.txt
Dockerfile
docker-compose.yml
```

## BotFather Setup

1. Create a bot with `@BotFather`.
2. Enable inline mode:

```text
/setinline
```

3. Optional but recommended: set inline placeholder text:

```text
Paste TikTok, Reels, or Shorts URL
```

4. Create a private channel or group for uploads.
5. Add the bot to that chat and allow it to send videos.
6. Put the chat id into `UPLOAD_CHAT_ID`.

## Environment

Create `.env` from `.env.example`:

```bash
cp .env.example .env
```

Required values:

```env
BOT_TOKEN=123456:replace-me
BOT_USERNAME=mybot
UPLOAD_CHAT_ID=-1001234567890
```

Useful settings:

```env
BOT_MODE=polling
MAX_VIDEO_SIZE_MB=49
RATE_LIMIT_PER_MINUTE=6
COOLDOWN_SECONDS=0
INLINE_DOWNLOAD_TIMEOUT=45
INLINE_READY_WAIT_SECONDS=12
MAX_CACHED_VIDEOS=5000
CACHE_TRIM_TO_VIDEOS=4500
ADMIN_USER_IDS=986436438
```

Telegram error alerts:

```env
ALERTS_ENABLED=false
ALERT_BOT_TOKEN=123456:replace-me
ALERT_CHAT_ID=-1001234567890
ALERT_MESSAGE_THREAD_ID=123
ALERT_LEVEL=ERROR
ALERT_SSL_VERIFY=true
```

`ALERT_BOT_TOKEN` can be omitted if alerts should use the main `BOT_TOKEN`.
Use `ALERT_MESSAGE_THREAD_ID` when alerts should be sent to a specific forum topic inside a supergroup.
Set `ALERT_SSL_VERIFY=false` only for local environments with broken Python CA certificates.

Admin commands:

- `/myid` shows your Telegram user id.
- `/stats` shows user and cache statistics for ids listed in `ADMIN_USER_IDS`.

For webhook mode:

```env
BOT_MODE=webhook
WEBHOOK_URL=https://example.com
WEBHOOK_PATH=/webhook
WEBHOOK_SECRET=change-me
HOST=0.0.0.0
PORT=8080
```

The final webhook URL will be `WEBHOOK_URL + WEBHOOK_PATH`, unless `WEBHOOK_URL` already ends with the path.

## Local Run

Install Python 3.12 and FFmpeg, then:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m bot.main
```

Healthcheck:

```text
http://127.0.0.1:8080/healthz
```

## Tests

Install development dependencies and run the test suite:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Run the full local check before committing:

```bash
make check
```

## Docker Run

```bash
docker compose up -d --build
```

Logs:

```bash
docker compose logs -f
```

## SQLite Schema

The schema is stored in `bot/database/schema.sql` and is applied automatically on startup.

Main cache table:

```sql
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_url TEXT NOT NULL UNIQUE,
    original_url TEXT NOT NULL,
    platform TEXT NOT NULL,
    title TEXT NOT NULL,
    caption TEXT NOT NULL,
    thumbnail_url TEXT,
    telegram_file_id TEXT NOT NULL,
    telegram_file_unique_id TEXT,
    file_size INTEGER,
    duration INTEGER,
    width INTEGER,
    height INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Download events are also stored for simple operational diagnostics.

## Production Notes

- Use a private upload channel as `UPLOAD_CHAT_ID`.
- Keep `MAX_VIDEO_SIZE_MB` below Telegram bot limits for your deployment.
- FFmpeg is included in Docker. For local PyCharm runs, the default downloader config avoids format merging so FFmpeg is not required for most videos.
- Some Instagram/TikTok videos may require cookies or may be unavailable due to privacy, age, region, or anti-bot restrictions. This project keeps the service layer ready for adding `yt-dlp` cookie support later.
- Inline queries have strict response timing. First-time downloads are queued in the background and the bot waits up to `INLINE_READY_WAIT_SECONDS` to return the final video in the same inline query. If the video is still not ready after that, Telegram cannot auto-refresh the same popup, so the bot falls back to a non-sendable loading status.
- Video cache is bounded with an LRU-style high-water/low-water policy: when SQLite has more than `MAX_CACHED_VIDEOS`, it trims the oldest `last_used_at` records down to `CACHE_TRIM_TO_VIDEOS`.
- Enable `ALERTS_ENABLED=true` and set `ALERT_CHAT_ID` to receive `ERROR` and `CRITICAL` logs in Telegram.
- Put the app behind Nginx/Caddy for HTTPS webhook deployments.

## Troubleshooting

- `Missing required environment variables`: check `.env`.
- No inline results: enable inline mode in BotFather and verify `BOT_USERNAME`.
- `Too many requests. Try again in a few seconds.`: set `COOLDOWN_SECONDS=0` for inline mode and restart the bot.
- Upload fails: verify the bot is in `UPLOAD_CHAT_ID` and can send videos.
- Download fails: check whether the video is public and supported by your installed `yt-dlp`.
- Healthcheck fails in Docker: inspect container logs and confirm port `8080` is exposed.
