from pathlib import Path

import pytest
from yt_dlp import DownloadError as YtDlpDownloadError

from bot.services.downloader import (
    VideoDownloader,
    _is_restricted_instagram_error,
    build_caption,
    html_escape,
)
from bot.services.errors import FileTooLargeError, RestrictedVideoError
from bot.utils.urls import ParsedVideoUrl, Platform


def parsed_url() -> ParsedVideoUrl:
    return ParsedVideoUrl(
        original_url="https://youtube.com/shorts/aRa1aCDEj4M",
        normalized_url="https://youtube.com/shorts/aRa1aCDEj4M",
        platform=Platform.YOUTUBE_SHORTS,
    )


def test_html_escape_escapes_caption_sensitive_characters() -> None:
    assert html_escape('<b>"A&B"</b>') == "&lt;b&gt;&quot;A&amp;B&quot;&lt;/b&gt;"


def test_build_caption_limits_title_and_escapes_url() -> None:
    caption = build_caption(
        title="<tag>" + ("x" * 400),
        platform="youtube_shorts",
        url='https://example.com?a="b"&c=<d>',
    )

    assert caption.startswith("<b>&lt;tag&gt;")
    assert "Youtube Shorts" in caption
    assert "&quot;b&quot;" in caption
    assert "&lt;d&gt;" in caption


def test_detects_restricted_instagram_errors() -> None:
    assert _is_restricted_instagram_error(
        "[Instagram] id: This content isn't available to everyone"
    )
    assert not _is_restricted_instagram_error("[YouTube] id: private video")


@pytest.mark.asyncio
async def test_download_builds_downloaded_video_from_yt_dlp_info(tmp_path: Path) -> None:
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    thumb_path = tmp_path / "thumbnail.jpg"
    thumb_path.write_bytes(b"thumb")

    downloader = VideoDownloader(tmp_path, max_video_size_bytes=100, retries=1)

    def fake_download_sync(url: str, job_dir: Path) -> tuple[dict, Path]:
        assert url == parsed_url().original_url
        assert job_dir.exists()
        return (
            {
                "title": "A <title>",
                "thumbnail": "https://example.com/thumb.jpg",
                "webpage_url": "https://example.com/watch",
                "duration": "12",
                "width": "720",
                "height": "1280",
            },
            video_path,
        )

    async def fake_download_thumbnail(url: str | None, job_dir: Path) -> Path:
        assert url == "https://example.com/thumb.jpg"
        return thumb_path

    downloader._download_sync = fake_download_sync  # type: ignore[method-assign]
    downloader._download_thumbnail = fake_download_thumbnail  # type: ignore[method-assign]

    downloaded = await downloader.download(parsed_url())

    assert downloaded.video_path == video_path
    assert downloaded.thumbnail_path == thumb_path
    assert downloaded.title == "A <title>"
    assert downloaded.caption.startswith("<b>A &lt;title&gt;</b>")
    assert downloaded.duration == 12
    assert downloaded.width == 720
    assert downloaded.height == 1280
    assert downloaded.file_size == 5


@pytest.mark.asyncio
async def test_download_rejects_files_over_configured_limit(tmp_path: Path) -> None:
    video_path = tmp_path / "large.mp4"
    video_path.write_bytes(b"too large")
    downloader = VideoDownloader(tmp_path, max_video_size_bytes=3, retries=1)
    downloader._download_sync = lambda _url, _job_dir: ({}, video_path)  # type: ignore[method-assign]

    with pytest.raises(FileTooLargeError):
        await downloader.download(parsed_url())


@pytest.mark.asyncio
async def test_download_raises_restricted_error_for_instagram_access_failure(
    tmp_path: Path,
) -> None:
    downloader = VideoDownloader(tmp_path, max_video_size_bytes=100, retries=1)

    def fake_download_sync(url: str, job_dir: Path) -> tuple[dict, Path]:
        raise YtDlpDownloadError(
            "ERROR: [Instagram] DR2t_FgCMGy: "
            "This content isn't available to everyone: "
            "It can't be seen by certain audiences."
        )

    downloader._download_sync = fake_download_sync  # type: ignore[method-assign]

    with pytest.raises(RestrictedVideoError):
        await downloader.download(parsed_url())
