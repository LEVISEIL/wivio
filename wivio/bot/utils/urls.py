from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import re


class Platform(StrEnum):
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    YOUTUBE_SHORTS = "youtube_shorts"


class UnsupportedUrlError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedVideoUrl:
    original_url: str
    normalized_url: str
    platform: Platform


URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
TRACKING_PREFIXES = ("utm_",)
TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "igsh",
    "si",
    "feature",
    "source",
    "share_app_id",
    "share_item_id",
    "timestamp",
}


def extract_first_url(text: str) -> str | None:
    match = URL_RE.search(text.strip())
    if not match:
        return None
    return match.group(0).rstrip(".,;)")


def parse_video_url(text: str) -> ParsedVideoUrl:
    url = extract_first_url(text)
    if not url:
        raise UnsupportedUrlError("Send a TikTok, Instagram Reels, or YouTube Shorts link.")

    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")
    path = parsed.path.rstrip("/")

    platform: Platform
    if host in {"tiktok.com", "vm.tiktok.com", "vt.tiktok.com"} or host.endswith(".tiktok.com"):
        platform = Platform.TIKTOK
    elif host in {"instagram.com", "instagr.am"} or host.endswith(".instagram.com"):
        if "/reel/" not in path and "/reels/" not in path:
            raise UnsupportedUrlError("Only Instagram Reels links are supported.")
        platform = Platform.INSTAGRAM
    elif host in {"youtube.com", "youtu.be"} or host.endswith(".youtube.com"):
        if "/shorts/" not in path and host != "youtu.be":
            raise UnsupportedUrlError("Only YouTube Shorts links are supported.")
        platform = Platform.YOUTUBE_SHORTS
    else:
        raise UnsupportedUrlError("Unsupported platform.")

    normalized = normalize_url(url)
    return ParsedVideoUrl(original_url=url, normalized_url=normalized, platform=platform)


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")
    path = parsed.path.rstrip("/") or "/"
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key not in TRACKING_PARAMS and not key.startswith(TRACKING_PREFIXES)
    ]
    return urlunparse(
        (
            parsed.scheme.lower() or "https",
            host,
            path,
            "",
            urlencode(query, doseq=True),
            "",
        )
    )
