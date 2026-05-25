from pathlib import Path

import pytest
from yt_dlp import DownloadError as YtDlpDownloadError

from bot.services.downloader import (
    VideoDownloader,
    _is_instagram_auth_required_error,
    _is_instagram_follow_required_error,
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


def instagram_url() -> ParsedVideoUrl:
    return ParsedVideoUrl(
        original_url="https://www.instagram.com/reel/abc/",
        normalized_url="https://instagram.com/reel/abc",
        platform=Platform.INSTAGRAM,
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
    assert _is_restricted_instagram_error(
        "[Instagram] id: Requested content is not available, rate-limit reached or login required"
    )
    assert _is_restricted_instagram_error(
        "[Instagram] id: This content is only available for registered users "
        "who follow this account. Use --cookies-from-browser or --cookies."
    )
    assert not _is_restricted_instagram_error("[YouTube] id: private video")


def test_detects_instagram_follow_required_errors_without_auth_alert() -> None:
    error = (
        "[Instagram] id: This content is only available for registered users who follow this "
        "account. Use --cookies-from-browser or --cookies for the authentication."
    )

    assert _is_instagram_follow_required_error(error)
    assert not _is_instagram_auth_required_error(error)


def test_detects_instagram_auth_required_errors() -> None:
    assert _is_instagram_auth_required_error(
        "[Instagram] id: Requested content is not available, rate-limit reached or login required"
    )
    assert _is_instagram_auth_required_error(
        "[Instagram] id: Main webpage is locked behind the login page"
    )
    assert not _is_instagram_auth_required_error(
        "[Instagram] id: This content isn't available to everyone"
    )


def test_instagram_cookies_are_only_used_for_instagram(tmp_path: Path) -> None:
    cookies_path = tmp_path / "instagram-cookies.txt"
    cookies_path.write_text("# Netscape HTTP Cookie File\n")
    downloader = VideoDownloader(
        tmp_path,
        max_video_size_bytes=100,
        retries=1,
        instagram_cookies_path=cookies_path,
    )

    assert downloader._cookies_path_for(instagram_url()) == cookies_path
    assert downloader._cookies_path_for(parsed_url()) is None


def test_missing_instagram_cookies_file_is_ignored(tmp_path: Path) -> None:
    downloader = VideoDownloader(
        tmp_path,
        max_video_size_bytes=100,
        retries=1,
        instagram_cookies_path=tmp_path / "missing-cookies.txt",
    )

    assert downloader._cookies_path_for(instagram_url()) is None


@pytest.mark.asyncio
async def test_download_builds_downloaded_video_from_yt_dlp_info(tmp_path: Path) -> None:
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    thumb_path = tmp_path / "thumbnail.jpg"
    thumb_path.write_bytes(b"thumb")

    downloader = VideoDownloader(tmp_path, max_video_size_bytes=100, retries=1)

    def fake_download_sync(
        url: str,
        job_dir: Path,
        cookies_path: Path | None = None,
    ) -> tuple[dict, Path]:
        assert url == parsed_url().original_url
        assert job_dir.exists()
        assert cookies_path is None
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
    downloader._download_sync = lambda _url, _job_dir, _cookies_path=None: (  # type: ignore[method-assign]
        {},
        video_path,
    )

    with pytest.raises(FileTooLargeError):
        await downloader.download(parsed_url())


@pytest.mark.asyncio
async def test_download_raises_restricted_error_for_instagram_access_failure(
    tmp_path: Path,
) -> None:
    downloader = VideoDownloader(tmp_path, max_video_size_bytes=100, retries=1)

    def fake_download_sync(
        url: str,
        job_dir: Path,
        cookies_path: Path | None = None,
    ) -> tuple[dict, Path]:
        assert cookies_path is None
        raise YtDlpDownloadError(
            "ERROR: [Instagram] DR2t_FgCMGy: "
            "This content isn't available to everyone: "
            "It can't be seen by certain audiences."
        )

    downloader._download_sync = fake_download_sync  # type: ignore[method-assign]

    with pytest.raises(RestrictedVideoError):
        await downloader.download(parsed_url())


@pytest.mark.asyncio
async def test_download_passes_cookies_for_instagram(tmp_path: Path) -> None:
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    cookies_path = tmp_path / "instagram-cookies.txt"
    cookies_path.write_text("# Netscape HTTP Cookie File\n")
    downloader = VideoDownloader(
        tmp_path,
        max_video_size_bytes=100,
        retries=1,
        instagram_cookies_path=cookies_path,
    )

    def fake_download_sync(
        url: str,
        job_dir: Path,
        passed_cookies_path: Path | None = None,
    ) -> tuple[dict, Path]:
        assert url == instagram_url().original_url
        assert job_dir.exists()
        assert passed_cookies_path == cookies_path
        return ({}, video_path)

    async def fake_download_thumbnail(url: str | None, job_dir: Path) -> None:
        assert url is None
        assert job_dir.parent == tmp_path

    downloader._download_sync = fake_download_sync  # type: ignore[method-assign]
    downloader._download_thumbnail = fake_download_thumbnail  # type: ignore[method-assign]

    downloaded = await downloader.download(instagram_url())

    assert downloaded.video_path == video_path


def test_download_sync_uses_temporary_cookiefile_copy(tmp_path: Path, monkeypatch) -> None:
    video_path = tmp_path / "job" / "Instagram-abc.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"video")
    cookies_path = tmp_path / "instagram-cookies.txt"
    cookies_path.write_text("# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t0\ta\tb\n")
    downloader = VideoDownloader(tmp_path, max_video_size_bytes=100, retries=1)
    captured_cookiefile: str | None = None

    class FakeYoutubeDL:
        def __init__(self, options: dict) -> None:
            nonlocal captured_cookiefile
            captured_cookiefile = options["cookiefile"]

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, url: str, download: bool) -> dict:
            assert download is True
            return {}

    monkeypatch.setattr("bot.services.downloader.YoutubeDL", FakeYoutubeDL)

    downloader._download_sync(
        "https://www.instagram.com/reel/abc/",
        video_path.parent,
        cookies_path,
    )

    assert captured_cookiefile is not None
    assert Path(captured_cookiefile) == video_path.parent / "cookies.txt"
    assert Path(captured_cookiefile).read_text() == cookies_path.read_text()


@pytest.mark.asyncio
async def test_download_logs_alertable_error_for_instagram_auth_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    downloader = VideoDownloader(
        tmp_path,
        max_video_size_bytes=100,
        retries=1,
        instagram_cookies_path=tmp_path / "instagram-cookies.txt",
    )

    def fake_download_sync(
        url: str,
        job_dir: Path,
        cookies_path: Path | None = None,
    ) -> tuple[dict, Path]:
        raise YtDlpDownloadError(
            "ERROR: [Instagram] abc: Requested content is not available, "
            "rate-limit reached or login required. Use --cookies-from-browser "
            "or --cookies for the authentication."
        )

    downloader._download_sync = fake_download_sync  # type: ignore[method-assign]

    with caplog.at_level("ERROR", logger="bot.services.downloader"):
        with pytest.raises(RestrictedVideoError):
            await downloader.download(instagram_url())

    record = next(
        record
        for record in caplog.records
        if "Instagram cookies/login need attention" in record.getMessage()
    )
    assert record.alert_fingerprint == "instagram-auth-required"
