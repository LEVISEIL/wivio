from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import aiofiles
import aiohttp
from yt_dlp import DownloadError as YtDlpDownloadError
from yt_dlp import YoutubeDL

from bot.services.errors import DownloadError, FileTooLargeError, RestrictedVideoError
from bot.utils.urls import ParsedVideoUrl, Platform

logger = logging.getLogger(__name__)


class _YtDlpLogger:
    def debug(self, message: str) -> None:
        logger.debug("yt-dlp: %s", message)

    def warning(self, message: str) -> None:
        logger.warning("yt-dlp warning: %s", message)

    def error(self, message: str) -> None:
        logger.debug("yt-dlp error output: %s", message)


@dataclass(frozen=True)
class DownloadedVideo:
    original_url: str
    normalized_url: str
    platform: str
    video_path: Path
    thumbnail_path: Path | None
    thumbnail_url: str | None
    title: str
    caption: str
    duration: int | None
    width: int | None
    height: int | None
    file_size: int


class VideoDownloader:
    def __init__(
        self,
        downloads_dir: Path,
        max_video_size_bytes: int,
        retries: int,
        instagram_cookies_path: Path | None = None,
    ) -> None:
        self.downloads_dir = downloads_dir
        self.max_video_size_bytes = max_video_size_bytes
        self.retries = retries
        self.instagram_cookies_path = instagram_cookies_path

    async def download(self, parsed_url: ParsedVideoUrl) -> DownloadedVideo:
        job_dir = self.downloads_dir / uuid4().hex
        job_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Starting download normalized_url=%s job_dir=%s",
            parsed_url.normalized_url,
            job_dir,
        )

        try:
            info, video_path = await asyncio.to_thread(
                self._download_sync,
                parsed_url.original_url,
                job_dir,
                self._cookies_path_for(parsed_url),
            )
        except YtDlpDownloadError as exc:
            error = str(exc)
            if _is_instagram_auth_required_error(error):
                logger.error(
                    "Instagram cookies/login need attention: update cookies or wait for "
                    "rate-limit to cool down. normalized_url=%s cookies_path=%s error=%s",
                    parsed_url.normalized_url,
                    self.instagram_cookies_path or "",
                    exc,
                    extra={"alert_fingerprint": "instagram-auth-required"},
                )
                raise RestrictedVideoError(error) from exc
            if _is_restricted_instagram_error(error):
                logger.warning(
                    "Instagram restricted video normalized_url=%s error=%s",
                    parsed_url.normalized_url,
                    exc,
                )
                raise RestrictedVideoError(error) from exc
            logger.warning(
                "yt-dlp failed normalized_url=%s error=%s",
                parsed_url.normalized_url,
                exc,
            )
            raise DownloadError(error) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected download failure normalized_url=%s",
                parsed_url.normalized_url,
            )
            raise DownloadError(str(exc)) from exc

        size = video_path.stat().st_size
        if size > self.max_video_size_bytes:
            logger.warning(
                "Downloaded file is too large normalized_url=%s size=%s max_size=%s",
                parsed_url.normalized_url,
                size,
                self.max_video_size_bytes,
            )
            raise FileTooLargeError(
                f"Downloaded file is {size} bytes, max is {self.max_video_size_bytes}"
            )

        thumbnail_url = _clean_string(info.get("thumbnail"))
        thumbnail_path = await self._download_thumbnail(thumbnail_url, job_dir)

        title = _clean_string(info.get("title")) or f"{parsed_url.platform.value.title()} video"
        uploader = _clean_string(info.get("uploader") or info.get("channel"))
        webpage_url = _clean_string(info.get("webpage_url")) or parsed_url.normalized_url
        caption = build_caption(title=title, platform=parsed_url.platform.value, url=webpage_url)
        description = f"{parsed_url.platform.value.replace('_', ' ').title()}"
        if uploader:
            description = f"{description} by {uploader}"

        logger.info("Downloaded %s to %s", parsed_url.normalized_url, video_path)
        return DownloadedVideo(
            original_url=parsed_url.original_url,
            normalized_url=parsed_url.normalized_url,
            platform=parsed_url.platform.value,
            video_path=video_path,
            thumbnail_path=thumbnail_path,
            thumbnail_url=thumbnail_url,
            title=title,
            caption=caption,
            duration=_optional_int(info.get("duration")),
            width=_optional_int(info.get("width")),
            height=_optional_int(info.get("height")),
            file_size=size,
        )

    def _download_sync(
        self,
        url: str,
        job_dir: Path,
        cookies_path: Path | None = None,
    ) -> tuple[dict, Path]:
        downloaded: list[Path] = []

        def hook(data: dict) -> None:
            if data.get("status") == "finished" and data.get("filename"):
                downloaded.append(Path(data["filename"]))

        options = {
            "outtmpl": str(job_dir / "%(extractor_key)s-%(id)s.%(ext)s"),
            "format": "b[ext=mp4]/best[ext=mp4]/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "retries": self.retries,
            "fragment_retries": self.retries,
            "socket_timeout": 20,
            "max_filesize": self.max_video_size_bytes,
            "progress_hooks": [hook],
            "restrictfilenames": True,
            "logger": _YtDlpLogger(),
        }
        if cookies_path is not None:
            options["cookiefile"] = str(cookies_path)

        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as exc:
            if _is_restricted_instagram_error(str(exc)):
                logger.warning(
                    "yt-dlp found restricted Instagram video url=%s job_dir=%s error=%s",
                    url,
                    job_dir,
                    exc,
                )
                raise
            logger.exception("yt-dlp extract_info failed url=%s job_dir=%s", url, job_dir)
            raise

        candidates = [path for path in downloaded if path.exists()]
        candidates.extend(
            path for path in job_dir.iterdir() if path.is_file() and path.suffix.lower() != ".jpg"
        )
        if not candidates:
            logger.error("yt-dlp did not produce a video file url=%s job_dir=%s", url, job_dir)
            raise DownloadError("yt-dlp did not produce a video file")

        video_path = max(candidates, key=lambda path: path.stat().st_size)
        return info, video_path

    def _cookies_path_for(self, parsed_url: ParsedVideoUrl) -> Path | None:
        if parsed_url.platform != Platform.INSTAGRAM:
            return None
        if self.instagram_cookies_path is None:
            return None
        if not self.instagram_cookies_path.exists():
            logger.warning("Instagram cookies file does not exist: %s", self.instagram_cookies_path)
            return None
        return self.instagram_cookies_path

    async def _download_thumbnail(self, url: str | None, job_dir: Path) -> Path | None:
        if not url:
            logger.debug("No thumbnail URL available for job_dir=%s", job_dir)
            return None

        thumbnail_path = job_dir / "thumbnail.jpg"
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status >= 400:
                        logger.warning(
                            "Thumbnail request failed url=%s status=%s",
                            url,
                            response.status,
                        )
                        return None
                    content = await response.read()
            if not content:
                logger.warning("Thumbnail request returned empty body url=%s", url)
                return None
            async with aiofiles.open(thumbnail_path, "wb") as file:
                await file.write(content)
            logger.info("Downloaded thumbnail url=%s path=%s", url, thumbnail_path)
            return thumbnail_path
        except Exception as exc:
            logger.warning("Could not download thumbnail %s: %s", url, exc)
            return None


def build_caption(title: str, platform: str, url: str) -> str:
    platform_title = platform.replace("_", " ").title()
    safe_title = html_escape(title)[:300]
    safe_url = html_escape(url)
    return f'<b>{safe_title}</b>\n\n{platform_title} | <a href="{safe_url}">source</a>'


def html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _clean_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_restricted_instagram_error(error: str) -> bool:
    text = error.lower()
    markers = (
        "this content isn't available to everyone",
        "it can't be seen by certain audiences",
        "instagram sent an empty media response",
        "check if this post is accessible in your browser without being logged-in",
        "login required",
        "rate-limit reached",
        "locked behind the login page",
        "use --cookies-from-browser or --cookies",
        "private",
    )
    return "[instagram]" in text and any(marker in text for marker in markers)


def _is_instagram_auth_required_error(error: str) -> bool:
    text = error.lower()
    markers = (
        "login required",
        "rate-limit reached",
        "locked behind the login page",
        "use --cookies-from-browser or --cookies",
    )
    return "[instagram]" in text and any(marker in text for marker in markers)
