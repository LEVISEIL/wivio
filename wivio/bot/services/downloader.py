from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import ssl
import subprocess
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from html import unescape as html_unescape
from pathlib import Path
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

import aiofiles
import aiohttp
from yt_dlp import DownloadError as YtDlpDownloadError
from yt_dlp import YoutubeDL

from bot.services.errors import DownloadError, FileTooLargeError, RestrictedVideoError
from bot.utils.urls import ParsedVideoUrl, Platform

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".aac", ".opus", ".ogg", ".wav"}
INSTAGRAM_PHOTO_SECONDS = 2
INSTAGRAM_SLIDESHOW_FPS = 15
INSTAGRAM_SLIDESHOW_MAX_WIDTH = 900
INSTAGRAM_FFMPEG_PRESET = "veryfast"
INSTAGRAM_IMAGE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
TIKTOK_MEDIA_TIMEOUT_SECONDS = 6
TIKTOK_PHOTO_PREFLIGHT_TIMEOUT_SECONDS = 5
TIKTOK_PHOTO_FETCH_TIMEOUT_SECONDS = 10
TIKTOK_WEB_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


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


@dataclass(frozen=True)
class TikTokPhotoPost:
    image_url_groups: list[list[str]]
    audio_url: str | None
    title: str
    webpage_url: str


@dataclass(frozen=True)
class TikTokPhotoPreflight:
    html_text: str | None
    final_url: str
    seconds: float


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
            "Starting download normalized_url=%s platform=%s job_dir=%s",
            parsed_url.normalized_url,
            parsed_url.platform.value,
            job_dir,
        )

        try:
            started_at = monotonic()
            info, video_path = await asyncio.to_thread(
                self._download_sync,
                parsed_url.original_url,
                job_dir,
                self._cookies_path_for(parsed_url),
            )
            download_seconds = monotonic() - started_at
        except YtDlpDownloadError as exc:
            error = str(exc)
            if _is_instagram_follow_required_error(error):
                logger.warning(
                    "Instagram follow-only video normalized_url=%s error=%s",
                    parsed_url.normalized_url,
                    exc,
                )
                raise RestrictedVideoError(error) from exc
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
        thumbnail_started_at = monotonic()
        thumbnail_path = await self._download_thumbnail(thumbnail_url, job_dir)
        thumbnail_seconds = monotonic() - thumbnail_started_at

        title = _clean_string(info.get("title")) or f"{parsed_url.platform.value.title()} video"
        uploader = _clean_string(info.get("uploader") or info.get("channel"))
        webpage_url = _clean_string(info.get("webpage_url")) or parsed_url.normalized_url
        caption = build_caption(title=title, platform=parsed_url.platform.value, url=webpage_url)
        description = f"{parsed_url.platform.value.replace('_', ' ').title()}"
        if uploader:
            description = f"{description} by {uploader}"

        logger.info(
            "Download completed normalized_url=%s platform=%s path=%s size=%s "
            "download_seconds=%.3f thumbnail_seconds=%.3f",
            parsed_url.normalized_url,
            parsed_url.platform.value,
            video_path,
            size,
            download_seconds,
            thumbnail_seconds,
        )
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
        media_download_started_at: float | None = None
        media_download_finished_at: float | None = None
        ytdlp_total_seconds = 0.0
        extract_seconds = 0.0
        media_download_seconds = 0.0

        if _is_tiktok_url(url):
            preflight = _preflight_tiktok_photo_webpage(url)
            if preflight is not None:
                if preflight.html_text is not None:
                    logger.info(
                        "TikTok photo fast path detected url=%s final_url=%s "
                        "preflight_seconds=%.3f",
                        url,
                        preflight.final_url,
                        preflight.seconds,
                    )
                    return _download_tiktok_photo_slideshow_from_webpage_sync(
                        html_text=preflight.html_text,
                        final_url=preflight.final_url,
                        job_dir=job_dir,
                        fetch_seconds=preflight.seconds,
                        fast_path=True,
                    )
                if _is_tiktok_photo_url(preflight.final_url):
                    logger.info(
                        "TikTok photo fast path resolved URL without HTML url=%s final_url=%s "
                        "preflight_seconds=%.3f",
                        url,
                        preflight.final_url,
                        preflight.seconds,
                    )
                    return _download_tiktok_photo_slideshow_sync(preflight.final_url, job_dir)
                if preflight.final_url != url and _is_tiktok_url(preflight.final_url):
                    logger.info(
                        "TikTok preflight resolved non-photo URL url=%s final_url=%s "
                        "preflight_seconds=%.3f",
                        url,
                        preflight.final_url,
                        preflight.seconds,
                    )
                    url = preflight.final_url

        def hook(data: dict) -> None:
            nonlocal media_download_finished_at, media_download_started_at

            if data.get("status") == "downloading" and media_download_started_at is None:
                media_download_started_at = monotonic()
            if data.get("status") == "finished" and data.get("filename"):
                if media_download_started_at is None:
                    media_download_started_at = monotonic()
                media_download_finished_at = monotonic()
                downloaded.append(Path(data["filename"]))

        options = {
            "outtmpl": str(job_dir / "%(extractor_key)s-%(id)s.%(ext)s"),
            "format": "b[ext=mp4]/best[ext=mp4]/best",
            "noplaylist": not _is_instagram_url(url),
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
        temporary_cookies_path = None
        if cookies_path is not None:
            temporary_cookies_path = _copy_cookiefile(cookies_path, job_dir)
            options["cookiefile"] = str(temporary_cookies_path)

        try:
            ytdlp_started_at = monotonic()
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
            ytdlp_total_seconds = monotonic() - ytdlp_started_at
            if media_download_started_at is not None and media_download_finished_at is not None:
                media_download_seconds = media_download_finished_at - media_download_started_at
            extract_seconds = max(0.0, ytdlp_total_seconds - media_download_seconds)
            logger.info(
                "yt-dlp video timings url=%s extract_seconds=%.3f "
                "media_download_seconds=%.3f ytdlp_total_seconds=%.3f",
                url,
                extract_seconds,
                media_download_seconds,
                ytdlp_total_seconds,
            )
        except Exception as exc:
            error = str(exc)
            if _is_restricted_instagram_error(error):
                logger.warning(
                    "yt-dlp found restricted Instagram video url=%s job_dir=%s error=%s",
                    url,
                    job_dir,
                    exc,
                )
                raise
            if _is_instagram_no_video_formats_error(error) and _is_instagram_url(url):
                metadata_started_at = monotonic()
                image_urls = _fetch_instagram_graphql_image_urls(url, temporary_cookies_path)
                metadata_seconds = monotonic() - metadata_started_at
                if image_urls:
                    logger.info(
                        "yt-dlp found Instagram photo post without video formats; "
                        "downloading images from metadata url=%s images=%s "
                        "metadata_seconds=%.3f",
                        url,
                        len(image_urls),
                        metadata_seconds,
                    )
                    info = _instagram_fallback_info(url)
                    video_path = _build_instagram_slideshow_from_image_urls(
                        url=url,
                        job_dir=job_dir,
                        image_urls=image_urls,
                        info=info,
                        cookies_path=temporary_cookies_path,
                    )
                    return info, video_path
                logger.warning(
                    "yt-dlp found Instagram photo post without video formats, but no images "
                    "were found url=%s job_dir=%s metadata_seconds=%.3f error=%s",
                    url,
                    job_dir,
                    metadata_seconds,
                    exc,
                )
            if _is_tiktok_photo_slideshow_error(error) and _is_tiktok_url(url):
                fallback_url = _extract_tiktok_photo_url_from_error(error) or url
                logger.info(
                    "yt-dlp could not process TikTok photo/slideshow; trying slideshow fallback "
                    "url=%s fallback_url=%s job_dir=%s error=%s",
                    url,
                    fallback_url,
                    job_dir,
                    exc,
                )
                return _download_tiktok_photo_slideshow_sync(fallback_url, job_dir)
            logger.exception("yt-dlp extract_info failed url=%s job_dir=%s", url, job_dir)
            raise

        candidates = _unique_paths(
            [path for path in downloaded if path.exists()]
            + [
                path
                for path in job_dir.iterdir()
                if path.is_file()
                and path != temporary_cookies_path
                and not path.name.endswith(".part")
            ]
        )
        video_candidates = [path for path in candidates if path.suffix.lower() in VIDEO_EXTENSIONS]
        if video_candidates:
            video_path = max(video_candidates, key=lambda path: path.stat().st_size)
            logger.info(
                "yt-dlp video file selected url=%s path=%s size=%s extract_seconds=%.3f "
                "media_download_seconds=%.3f ytdlp_total_seconds=%.3f",
                url,
                video_path,
                video_path.stat().st_size,
                extract_seconds,
                media_download_seconds,
                ytdlp_total_seconds,
            )
            return info, video_path

        image_candidates = _sort_media_paths(
            path for path in candidates if path.suffix.lower() in IMAGE_EXTENSIONS
        )
        if not image_candidates and _is_instagram_url(url):
            image_urls = _extract_instagram_image_urls(info)
            if not image_urls:
                metadata_started_at = monotonic()
                image_urls = _fetch_instagram_graphql_image_urls(url, temporary_cookies_path)
                logger.info(
                    "Instagram GraphQL image metadata fetched url=%s images=%s "
                    "metadata_seconds=%.3f",
                    url,
                    len(image_urls),
                    monotonic() - metadata_started_at,
                )
            if image_urls:
                logger.info(
                    "yt-dlp produced no Instagram media files; downloading images from metadata "
                    "url=%s images=%s",
                    url,
                    len(image_urls),
                )
                image_candidates = _download_instagram_images_from_metadata(
                    job_dir=job_dir,
                    image_urls=image_urls,
                    info=info,
                    cookies_path=temporary_cookies_path,
                )

        if image_candidates and _is_instagram_url(url):
            audio_candidates = [
                path for path in candidates if path.suffix.lower() in AUDIO_EXTENSIONS
            ]
            audio_path = max(audio_candidates, key=lambda path: path.stat().st_size, default=None)
            slideshow_started_at = monotonic()
            video_path = _build_instagram_photo_slideshow(
                job_dir=job_dir,
                image_paths=image_candidates,
                audio_path=audio_path,
            )
            logger.info(
                "Instagram photo slideshow completed url=%s images=%s audio=%s "
                "slideshow_seconds=%.3f path=%s",
                url,
                len(image_candidates),
                bool(audio_path),
                monotonic() - slideshow_started_at,
                video_path,
            )
            return info, video_path

        logger.error("yt-dlp did not produce a video file url=%s job_dir=%s", url, job_dir)
        raise DownloadError("yt-dlp did not produce a video file")

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
    safe_url = html_escape(url)
    return f'{platform_title} | <a href="{safe_url}">source</a>'


def html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _clean_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _copy_cookiefile(cookies_path: Path, job_dir: Path) -> Path:
    temporary_cookies_path = job_dir / "cookies.txt"
    shutil.copyfile(cookies_path, temporary_cookies_path)
    return temporary_cookies_path


def _is_instagram_url(url: str) -> bool:
    return "instagram.com" in url.lower() or "instagr.am" in url.lower()


def _is_tiktok_url(url: str) -> bool:
    return "tiktok.com" in url.lower()


def _is_tiktok_photo_url(url: str) -> bool:
    if not _is_tiktok_url(url):
        return False
    return "photo" in {part.lower() for part in urlparse(url).path.split("/") if part}


def _instagram_fallback_info(url: str) -> dict:
    return {
        "title": "Instagram photo post",
        "webpage_url": url,
        "http_headers": {"Referer": "https://www.instagram.com/"},
    }


def _unique_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _sort_media_paths(paths: Iterable[Path]) -> list[Path]:
    return sorted(paths, key=lambda path: path.name)


def _extract_instagram_image_urls(info: dict) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: object) -> None:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return
        if url in seen:
            return
        seen.add(url)
        urls.append(url)

    def best_thumbnail(item: dict) -> str | None:
        thumbnails = item.get("thumbnails")
        if not isinstance(thumbnails, list):
            return None
        candidates: list[tuple[int, str]] = []
        for thumbnail in thumbnails:
            if not isinstance(thumbnail, dict):
                continue
            url = thumbnail.get("url")
            if not isinstance(url, str):
                continue
            width = _optional_int(thumbnail.get("width")) or 0
            height = _optional_int(thumbnail.get("height")) or 0
            candidates.append((width * height, url))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def walk(item: object) -> None:
        if isinstance(item, dict):
            entries = item.get("entries")
            if entries is not None:
                for entry in entries:
                    walk(entry)
                return

            add(best_thumbnail(item))
            for key in ("display_url", "display_src", "thumbnail", "url"):
                value = item.get(key)
                if isinstance(value, str) and _looks_like_image_url(value):
                    add(value)
        elif isinstance(item, list):
            for entry in item:
                walk(entry)

    walk(info)
    return urls


def _fetch_instagram_graphql_image_urls(
    url: str,
    cookies_path: Path | None = None,
) -> list[str]:
    shortcode = _instagram_shortcode_from_url(url)
    if not shortcode:
        return []

    variables = {
        "shortcode": shortcode,
        "child_comment_count": 3,
        "fetch_comment_count": 40,
        "parent_comment_count": 24,
        "has_threaded_comments": True,
    }
    query = urlencode(
        {
            "doc_id": "8845758582119845",
            "variables": json.dumps(variables, separators=(",", ":")),
        }
    )
    graphql_url = f"https://www.instagram.com/graphql/query/?{query}"
    headers = {
        "User-Agent": INSTAGRAM_IMAGE_USER_AGENT,
        "X-IG-App-ID": "936619743392459",
        "X-ASBD-ID": "198387",
        "X-IG-WWW-Claim": "0",
        "Origin": "https://www.instagram.com",
        "Accept": "*/*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": url,
    }
    headers.update(_instagram_cookie_headers(cookies_path))
    request = Request(
        graphql_url,
        headers=headers,
    )
    try:
        content = _read_url(
            request,
            graphql_url,
            "Instagram GraphQL media",
            verify_cert=False,
        )
        payload = json.loads(content.decode("utf-8"))
    except (DownloadError, json.JSONDecodeError) as exc:
        logger.warning("Could not fetch Instagram image metadata url=%s error=%s", url, exc)
        return []

    media = payload.get("data", {}).get("xdt_shortcode_media")
    if not isinstance(media, dict):
        return []
    return _extract_instagram_media_image_urls(media)


def _instagram_shortcode_from_url(url: str) -> str | None:
    parts = [part for part in urlparse(url).path.split("/") if part]
    for marker in ("p", "reel", "reels", "tv"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                return parts[index + 1]
    return None


def _extract_instagram_media_image_urls(media: dict) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: str | None) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        urls.append(url)

    sidecar = media.get("edge_sidecar_to_children")
    edges = sidecar.get("edges") if isinstance(sidecar, dict) else None
    if isinstance(edges, list):
        for edge in edges:
            node = edge.get("node") if isinstance(edge, dict) else None
            if isinstance(node, dict) and not node.get("is_video"):
                add(_best_instagram_media_image_url(node))
        return urls

    if not media.get("is_video"):
        add(_best_instagram_media_image_url(media))
    return urls


def _best_instagram_media_image_url(media: dict) -> str | None:
    candidates: list[tuple[int, str]] = []
    for image in _instagram_image_candidates(media):
        url = image.get("url") or image.get("src")
        if not isinstance(url, str):
            continue
        width = _optional_int(image.get("width") or image.get("config_width")) or 0
        height = _optional_int(image.get("height") or image.get("config_height")) or 0
        candidates.append((width * height, url))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]

    for key in ("display_url", "display_src", "thumbnail_src", "thumbnail"):
        value = media.get(key)
        if isinstance(value, str):
            return value
    return None


def _instagram_image_candidates(media: dict) -> list[dict]:
    candidates: list[dict] = []
    for key in ("display_resources", "thumbnail_resources"):
        value = media.get(key)
        if isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))

    image_versions = media.get("image_versions2")
    if isinstance(image_versions, dict):
        value = image_versions.get("candidates")
        if isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))
    return candidates


def _looks_like_image_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(extension) for extension in IMAGE_EXTENSIONS)


def _download_instagram_images_from_metadata(
    job_dir: Path,
    image_urls: list[str],
    info: dict,
    cookies_path: Path | None = None,
) -> list[Path]:
    if not image_urls:
        raise DownloadError("Instagram photo post did not contain image URLs")

    started_at = monotonic()
    headers = _instagram_image_headers(info, cookies_path)
    items: list[tuple[str, Path]] = []
    for index, url in enumerate(image_urls, start=1):
        image_path = job_dir / f"instagram-photo-{index:03d}{_image_suffix_from_url(url)}"
        items.append((url, image_path))

    max_workers = min(2, len(items))
    image_paths: list[Path] = []
    failed_count = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            (url, image_path, executor.submit(_download_url_to_path, url, image_path, headers))
            for url, image_path in items
        ]
        for url, image_path, future in futures:
            try:
                future.result()
            except DownloadError as exc:
                logger.warning(
                    "Could not download Instagram carousel image url=%s error=%s", url, exc
                )
                failed_count += 1
                continue
            image_paths.append(image_path)

    if not image_paths:
        raise DownloadError("Could not download any Instagram carousel images")

    downloaded_bytes = sum(path.stat().st_size for path in image_paths if path.exists())
    logger.info(
        "Instagram carousel images downloaded job_dir=%s requested=%s succeeded=%s failed=%s "
        "workers=%s bytes=%s image_download_seconds=%.3f",
        job_dir,
        len(items),
        len(image_paths),
        failed_count,
        max_workers,
        downloaded_bytes,
        monotonic() - started_at,
    )
    return image_paths


def _build_instagram_slideshow_from_image_urls(
    url: str,
    job_dir: Path,
    image_urls: list[str],
    info: dict,
    cookies_path: Path | None = None,
) -> Path:
    started_at = monotonic()
    image_download_started_at = monotonic()
    image_paths = _download_instagram_images_from_metadata(
        job_dir=job_dir,
        image_urls=image_urls,
        info=info,
        cookies_path=cookies_path,
    )
    image_download_seconds = monotonic() - image_download_started_at
    slideshow_started_at = monotonic()
    video_path = _build_instagram_photo_slideshow(
        job_dir=job_dir,
        image_paths=image_paths,
        audio_path=None,
    )
    slideshow_seconds = monotonic() - slideshow_started_at
    logger.info(
        "Instagram photo fallback timings url=%s images=%s audio=%s "
        "image_download_seconds=%.3f slideshow_seconds=%.3f total_seconds=%.3f path=%s",
        url,
        len(image_paths),
        False,
        image_download_seconds,
        slideshow_seconds,
        monotonic() - started_at,
        video_path,
    )
    return video_path


def _download_tiktok_photo_slideshow_sync(url: str, job_dir: Path) -> tuple[dict, Path]:
    fetch_started_at = monotonic()
    html_text, final_url = _fetch_tiktok_webpage(url)
    fetch_seconds = monotonic() - fetch_started_at
    return _download_tiktok_photo_slideshow_from_webpage_sync(
        html_text=html_text,
        final_url=final_url,
        job_dir=job_dir,
        fetch_seconds=fetch_seconds,
        fast_path=False,
    )


def _download_tiktok_photo_slideshow_from_webpage_sync(
    html_text: str,
    final_url: str,
    job_dir: Path,
    fetch_seconds: float,
    fast_path: bool,
) -> tuple[dict, Path]:
    started_at = monotonic()
    photo_post = _extract_tiktok_photo_post(html_text, final_url)
    logger.info(
        "Building TikTok photo slideshow url=%s images=%s has_audio=%s "
        "fetch_seconds=%.3f fast_path=%s",
        photo_post.webpage_url,
        len(photo_post.image_url_groups),
        bool(photo_post.audio_url),
        fetch_seconds,
        fast_path,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        image_future = executor.submit(
            _timed_call,
            _download_tiktok_images,
            job_dir,
            photo_post.image_url_groups,
            photo_post.webpage_url,
        )
        audio_future = executor.submit(
            _timed_call,
            _download_tiktok_audio,
            job_dir,
            photo_post.audio_url,
            photo_post.webpage_url,
        )
        image_paths, image_download_seconds = image_future.result()
        audio_path, audio_download_seconds = audio_future.result()

    slideshow_started_at = monotonic()
    video_path = _build_tiktok_photo_slideshow(
        job_dir=job_dir,
        image_paths=image_paths,
        audio_path=audio_path,
    )
    slideshow_seconds = monotonic() - slideshow_started_at
    logger.info(
        "TikTok photo fallback timings url=%s images=%s audio=%s "
        "fetch_seconds=%.3f image_download_seconds=%.3f audio_download_seconds=%.3f "
        "slideshow_seconds=%.3f total_seconds=%.3f path=%s",
        photo_post.webpage_url,
        len(image_paths),
        bool(audio_path),
        fetch_seconds,
        image_download_seconds,
        audio_download_seconds,
        slideshow_seconds,
        fetch_seconds + (monotonic() - started_at),
        video_path,
    )
    return (
        {
            "title": photo_post.title,
            "webpage_url": photo_post.webpage_url,
            "duration": len(image_paths) * INSTAGRAM_PHOTO_SECONDS,
            "extractor_key": "TikTok",
        },
        video_path,
    )


def _fetch_tiktok_webpage(url: str) -> tuple[str, str]:
    request = Request(url, headers=_tiktok_web_headers(url))
    try:
        with urlopen(  # noqa: S310
            request,
            timeout=TIKTOK_PHOTO_FETCH_TIMEOUT_SECONDS,
            context=ssl._create_unverified_context(),  # noqa: S323
        ) as response:
            content = response.read()
            final_url = response.geturl()
    except (HTTPError, URLError, OSError) as exc:
        raise DownloadError(f"Could not fetch TikTok photo page: {exc}") from exc
    return content.decode("utf-8", errors="replace"), final_url


def _preflight_tiktok_photo_webpage(url: str) -> TikTokPhotoPreflight | None:
    started_at = monotonic()
    request = Request(url, headers=_tiktok_web_headers(url))
    try:
        with urlopen(  # noqa: S310
            request,
            timeout=TIKTOK_PHOTO_PREFLIGHT_TIMEOUT_SECONDS,
            context=ssl._create_unverified_context(),  # noqa: S323
        ) as response:
            final_url = response.geturl()
            if not _is_tiktok_photo_url(final_url):
                return TikTokPhotoPreflight(
                    html_text=None,
                    final_url=final_url,
                    seconds=monotonic() - started_at,
                )
            try:
                content = response.read()
            except (HTTPError, URLError, OSError, TimeoutError) as exc:
                logger.info(
                    "TikTok photo preflight resolved URL but could not read HTML url=%s "
                    "final_url=%s error=%s",
                    url,
                    final_url,
                    exc,
                )
                return TikTokPhotoPreflight(
                    html_text=None,
                    final_url=final_url,
                    seconds=monotonic() - started_at,
                )
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        logger.debug("TikTok photo preflight failed url=%s error=%s", url, exc)
        return None

    return TikTokPhotoPreflight(
        html_text=content.decode("utf-8", errors="replace"),
        final_url=final_url,
        seconds=monotonic() - started_at,
    )


def _extract_tiktok_photo_post(html_text: str, final_url: str) -> TikTokPhotoPost:
    for payload in _extract_tiktok_json_payloads(html_text):
        for item in _find_tiktok_item_structs(payload):
            image_url_groups = _extract_tiktok_item_image_url_groups(item)
            if not image_url_groups:
                continue
            return TikTokPhotoPost(
                image_url_groups=image_url_groups,
                audio_url=_extract_tiktok_item_audio_url(item),
                title=_clean_string(item.get("desc") or item.get("title")) or "TikTok photo post",
                webpage_url=_clean_string(item.get("webpage_url")) or final_url,
            )
    raise DownloadError("Could not find TikTok photo carousel metadata")


def _extract_tiktok_json_payloads(html_text: str) -> list[dict]:
    payloads: list[dict] = []
    for script_id in ("__UNIVERSAL_DATA_FOR_REHYDRATION__", "SIGI_STATE"):
        pattern = (
            rf'<script[^>]+id=["\']{re.escape(script_id)}["\'][^>]*>'
            r"(.*?)</script>"
        )
        for match in re.finditer(pattern, html_text, flags=re.DOTALL | re.IGNORECASE):
            try:
                payload = json.loads(html_unescape(match.group(1)).strip())
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
    return payloads


def _find_tiktok_item_structs(value: object) -> Iterable[dict]:
    if isinstance(value, dict):
        if isinstance(value.get("imagePost"), dict):
            yield value
        for child in value.values():
            yield from _find_tiktok_item_structs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _find_tiktok_item_structs(child)


def _extract_tiktok_item_image_url_groups(item: dict) -> list[list[str]]:
    image_post = item.get("imagePost")
    if not isinstance(image_post, dict):
        return []

    images = image_post.get("images")
    if not isinstance(images, list):
        return []

    groups: list[list[str]] = []
    seen_groups: set[tuple[str, ...]] = set()
    for image in images:
        candidates = _extract_http_urls(image)
        if not candidates:
            continue
        urls = _prefer_tiktok_image_urls(candidates)
        group_key = tuple(urls)
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)
        groups.append(urls)
    return groups


def _extract_tiktok_item_audio_url(item: dict) -> str | None:
    music = item.get("music") or item.get("musicInfo")
    if not isinstance(music, dict):
        return None
    for key in ("playUrl", "play_url", "previewUrl", "matchedSong"):
        urls = _extract_http_urls(music.get(key))
        if urls:
            return urls[0]
    urls = _extract_http_urls(music)
    return urls[0] if urls else None


def _extract_http_urls(value: object) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: object) -> None:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return
        if url in seen:
            return
        seen.add(url)
        urls.append(url)

    def walk(item: object) -> None:
        if isinstance(item, str):
            add(item)
        elif isinstance(item, dict):
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return urls


def _prefer_tiktok_image_urls(urls: list[str]) -> list[str]:
    image_urls = [url for url in urls if _looks_like_image_url(url)]
    candidates = image_urls or urls
    prioritized = sorted(candidates, key=_tiktok_media_url_priority)
    unique: list[str] = []
    seen: set[str] = set()
    for url in prioritized:
        if url in seen:
            continue
        seen.add(url)
        unique.append(url)
    return unique


def _tiktok_media_url_priority(url: str) -> tuple[int, str]:
    host = urlparse(url).netloc.lower()
    if "tiktokcdn" in host:
        return (0, url)
    return (1, url)


def _download_tiktok_images(
    job_dir: Path,
    image_url_groups: list[list[str]],
    referer: str,
) -> list[Path]:
    if not image_url_groups:
        raise DownloadError("TikTok photo post did not contain image URLs")

    started_at = monotonic()
    headers = _tiktok_web_headers(referer)
    items: list[tuple[list[str], Path]] = []
    for index, urls in enumerate(image_url_groups, start=1):
        image_path = job_dir / f"tiktok-photo-{index:03d}{_image_suffix_from_url(urls[0])}"
        items.append((urls, image_path))

    max_workers = min(4, len(items))
    image_paths: list[Path] = []
    failed_count = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            (
                urls,
                image_path,
                executor.submit(_download_tiktok_media_candidates, urls, image_path, headers),
            )
            for urls, image_path in items
        ]
        for urls, image_path, future in futures:
            primary_url = urls[0]
            try:
                used_url = future.result()
            except DownloadError as exc:
                logger.warning(
                    "Could not download TikTok carousel image primary_url=%s candidates=%s "
                    "error=%s",
                    primary_url,
                    len(urls),
                    exc,
                )
                failed_count += 1
                continue
            if used_url != primary_url:
                logger.info(
                    "Downloaded TikTok carousel image from fallback URL primary_url=%s "
                    "used_url=%s path=%s",
                    primary_url,
                    used_url,
                    image_path,
                )
            image_paths.append(image_path)

    if not image_paths:
        raise DownloadError("Could not download any TikTok carousel images")

    downloaded_bytes = sum(path.stat().st_size for path in image_paths if path.exists())
    logger.info(
        "TikTok carousel images downloaded job_dir=%s requested=%s succeeded=%s failed=%s "
        "workers=%s bytes=%s image_download_seconds=%.3f",
        job_dir,
        len(items),
        len(image_paths),
        failed_count,
        max_workers,
        downloaded_bytes,
        monotonic() - started_at,
    )
    return image_paths


def _download_tiktok_audio(job_dir: Path, audio_url: str | None, referer: str) -> Path | None:
    if not audio_url:
        return None
    suffix = Path(urlparse(audio_url).path).suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        suffix = ".mp3"
    audio_path = job_dir / f"tiktok-audio{suffix}"
    try:
        _download_bytes_to_path(
            audio_url,
            audio_path,
            _tiktok_web_headers(referer),
            timeout=TIKTOK_MEDIA_TIMEOUT_SECONDS,
        )
    except DownloadError as exc:
        logger.warning("Could not download TikTok carousel audio url=%s error=%s", audio_url, exc)
        return None
    return audio_path


def _timed_call(func, *args):
    started_at = monotonic()
    result = func(*args)
    return result, monotonic() - started_at


def _download_tiktok_media_candidates(
    urls: list[str],
    path: Path,
    headers: dict[str, str],
) -> str:
    if not urls:
        raise DownloadError("TikTok media candidate list was empty")

    last_error: DownloadError | None = None
    for candidate_index, url in enumerate(urls, start=1):
        try:
            _download_bytes_to_path(
                url,
                path,
                headers,
                timeout=TIKTOK_MEDIA_TIMEOUT_SECONDS,
            )
        except DownloadError as exc:
            last_error = exc
            logger.warning(
                "TikTok media candidate failed url=%s path=%s candidate=%s candidates=%s "
                "timeout_seconds=%s error=%s",
                url,
                path,
                candidate_index,
                len(urls),
                TIKTOK_MEDIA_TIMEOUT_SECONDS,
                exc,
            )
            continue
        return url

    raise DownloadError(
        f"Could not download TikTok media from {len(urls)} candidates: {last_error}"
    )


def _download_bytes_to_path(
    url: str,
    path: Path,
    headers: dict[str, str],
    timeout: int = 20,
) -> None:
    request = Request(url, headers=headers)
    content = _read_url(request, url, "TikTok media", verify_cert=False, timeout=timeout)
    if not content:
        raise DownloadError("TikTok media response was empty")
    path.write_bytes(content)


def _tiktok_web_headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": TIKTOK_WEB_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": referer,
    }


def _instagram_image_headers(
    info: dict,
    cookies_path: Path | None = None,
) -> dict[str, str]:
    headers = {
        "User-Agent": INSTAGRAM_IMAGE_USER_AGENT,
        "Referer": "https://www.instagram.com/",
    }
    info_headers = info.get("http_headers")
    if isinstance(info_headers, dict):
        headers.update(
            {str(key): str(value) for key, value in info_headers.items() if value is not None}
        )
    headers.update(_instagram_cookie_headers(cookies_path))
    return headers


def _instagram_cookie_headers(cookies_path: Path | None) -> dict[str, str]:
    cookie_header = _cookie_header_from_file(cookies_path)
    if not cookie_header:
        return {}

    headers = {"Cookie": cookie_header}
    csrf_token = _cookie_value(cookie_header, "csrftoken")
    if csrf_token:
        headers["X-CSRFToken"] = csrf_token
    return headers


def _cookie_header_from_file(cookies_path: Path | None) -> str | None:
    if cookies_path is None:
        return None

    try:
        lines = cookies_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("Could not read Instagram cookies file path=%s error=%s", cookies_path, exc)
        return None

    cookies: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#HttpOnly_"):
            line = line.removeprefix("#HttpOnly_")
        elif line.startswith("#"):
            continue

        parts = line.split("\t")
        if len(parts) >= 7:
            domain = parts[0].lstrip(".").lower()
            if not domain.endswith("instagram.com"):
                continue
            name = parts[5].strip()
            value = parts[6].strip()
            if name and value:
                cookies[name] = value
            continue

        for item in line.split(";"):
            if "=" not in item:
                continue
            name, value = item.strip().split("=", 1)
            if name and value:
                cookies[name] = value

    if not cookies:
        logger.warning(
            "Instagram cookies file did not contain usable cookies path=%s",
            cookies_path,
        )
        return None
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def _cookie_value(cookie_header: str, name: str) -> str | None:
    prefix = f"{name}="
    for item in cookie_header.split(";"):
        text = item.strip()
        if text.startswith(prefix):
            return text[len(prefix) :]
    return None


def _image_suffix_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return suffix
    return ".jpg"


def _download_url_to_path(url: str, path: Path, headers: dict[str, str]) -> None:
    request = Request(url, headers=headers)
    content = _read_url(request, url, "Instagram image", verify_cert=False)

    if not content:
        raise DownloadError("Instagram image response was empty")
    path.write_bytes(content)


def _read_url(
    request: Request,
    url: str,
    label: str,
    verify_cert: bool = True,
    timeout: int = 20,
) -> bytes:
    context = None if verify_cert else ssl._create_unverified_context()  # noqa: S323
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            return response.read()
    except URLError as exc:
        if verify_cert and _is_ssl_certificate_error(exc):
            logger.warning(
                "%s request failed during SSL verification, retrying without certificate "
                "check url=%s error=%s",
                label,
                url,
                exc,
            )
            return _read_url(request, url, label, verify_cert=False, timeout=timeout)
        raise DownloadError(f"Could not download {label}: {exc}") from exc
    except (HTTPError, OSError) as exc:
        raise DownloadError(f"Could not download {label}: {exc}") from exc


def _is_ssl_certificate_error(exc: URLError) -> bool:
    reason = getattr(exc, "reason", None)
    return isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(exc)


def _build_instagram_photo_slideshow(
    job_dir: Path,
    image_paths: list[Path],
    audio_path: Path | None,
) -> Path:
    return _build_photo_slideshow(
        job_dir=job_dir,
        image_paths=image_paths,
        audio_path=audio_path,
        output_stem="instagram-photo-slideshow",
        media_label="Instagram",
    )


def _build_tiktok_photo_slideshow(
    job_dir: Path,
    image_paths: list[Path],
    audio_path: Path | None,
) -> Path:
    return _build_photo_slideshow(
        job_dir=job_dir,
        image_paths=image_paths,
        audio_path=audio_path,
        output_stem="tiktok-photo-slideshow",
        media_label="TikTok",
    )


def _build_photo_slideshow(
    job_dir: Path,
    image_paths: list[Path],
    audio_path: Path | None,
    output_stem: str,
    media_label: str,
) -> Path:
    if not image_paths:
        raise DownloadError(f"{media_label} photo post did not contain images")

    concat_path = job_dir / f"{output_stem}.txt"
    video_path = job_dir / f"{output_stem}.mp4"
    concat_path.write_text(_ffmpeg_concat_text(image_paths), encoding="utf-8")
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path.resolve()),
    ]
    if audio_path is not None:
        cmd.extend(["-i", str(audio_path.resolve())])

    cmd.extend(["-map", "0:v:0"])
    if audio_path is not None:
        cmd.extend(["-map", "1:a:0", "-shortest"])

    cmd.extend(
        [
            "-vf",
            f"scale='min({INSTAGRAM_SLIDESHOW_MAX_WIDTH},iw)':-2,pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-r",
            str(INSTAGRAM_SLIDESHOW_FPS),
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            INSTAGRAM_FFMPEG_PRESET,
            "-movflags",
            "+faststart",
        ]
    )
    if audio_path is not None:
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])
    cmd.append(str(video_path.resolve()))

    try:
        ffmpeg_started_at = monotonic()
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
        ffmpeg_seconds = monotonic() - ffmpeg_started_at
    except FileNotFoundError as exc:
        raise DownloadError("ffmpeg is not installed") from exc
    except subprocess.CalledProcessError as exc:
        error = (exc.stderr or exc.stdout or str(exc)).strip()
        raise DownloadError(
            f"ffmpeg could not build {media_label} photo slideshow: {error}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DownloadError(
            f"ffmpeg timed out while building {media_label} photo slideshow"
        ) from exc

    if not video_path.exists():
        raise DownloadError(f"ffmpeg did not produce {media_label} photo slideshow")
    logger.info(
        "ffmpeg built %s slideshow job_dir=%s images=%s audio=%s fps=%s photo_seconds=%s "
        "max_width=%s preset=%s size=%s ffmpeg_seconds=%.3f path=%s",
        media_label,
        job_dir,
        len(image_paths),
        bool(audio_path),
        INSTAGRAM_SLIDESHOW_FPS,
        INSTAGRAM_PHOTO_SECONDS,
        INSTAGRAM_SLIDESHOW_MAX_WIDTH,
        INSTAGRAM_FFMPEG_PRESET,
        video_path.stat().st_size,
        ffmpeg_seconds,
        video_path,
    )
    return video_path


def _ffmpeg_concat_text(image_paths: list[Path]) -> str:
    lines = ["ffconcat version 1.0"]
    for image_path in image_paths:
        lines.append(f"file '{_escape_ffmpeg_concat_path(image_path.resolve())}'")
        lines.append(f"duration {INSTAGRAM_PHOTO_SECONDS}")
    lines.append(f"file '{_escape_ffmpeg_concat_path(image_paths[-1].resolve())}'")
    return "\n".join(lines) + "\n"


def _escape_ffmpeg_concat_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace("'", "'\\''")


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
        "only available for registered users who follow this account",
        "instagram sent an empty media response",
        "check if this post is accessible in your browser without being logged-in",
        "login required",
        "rate-limit reached",
        "locked behind the login page",
        "use --cookies-from-browser or --cookies",
        "private",
    )
    return "[instagram]" in text and any(marker in text for marker in markers)


def _is_instagram_no_video_formats_error(error: str) -> bool:
    text = error.lower()
    return "[instagram]" in text and "no video formats found" in text


def _is_tiktok_photo_slideshow_error(error: str) -> bool:
    text = error.lower()
    if "tiktok.com" in text and "/photo/" in text and "unsupported url" in text:
        return True

    status_markers = (
        "video not available, status code 10231",
        "video not available, status code 10240",
    )
    return "[tiktok]" in text and any(marker in text for marker in status_markers)


def _extract_tiktok_photo_url_from_error(error: str) -> str | None:
    for match in re.finditer(r"https?://[^\s\"'<>]+", error):
        url = match.group(0).rstrip(".,)'\"")
        if _is_tiktok_photo_url(url):
            return url
    return None


def _is_instagram_follow_required_error(error: str) -> bool:
    text = error.lower()
    markers = (
        "only available for registered users who follow this account",
        "follow this account",
    )
    return "[instagram]" in text and any(marker in text for marker in markers)


def _is_instagram_auth_required_error(error: str) -> bool:
    text = error.lower()
    if _is_instagram_follow_required_error(error):
        return False
    markers = (
        "login required",
        "rate-limit reached",
        "locked behind the login page",
        "use --cookies-from-browser or --cookies",
    )
    return "[instagram]" in text and any(marker in text for marker in markers)
