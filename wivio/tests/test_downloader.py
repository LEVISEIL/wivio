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


def test_build_caption_uses_only_platform_and_source_url() -> None:
    caption = build_caption(
        title="<tag>" + ("x" * 400),
        platform="youtube_shorts",
        url='https://example.com?a="b"&c=<d>',
    )

    assert "&lt;tag&gt;" not in caption
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
    assert downloaded.caption.startswith("Youtube Shorts | ")
    assert "A &lt;title&gt;" not in downloaded.caption
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


def test_download_sync_builds_slideshow_for_instagram_photo_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    downloader = VideoDownloader(tmp_path, max_video_size_bytes=100, retries=1)
    captured_options: dict = {}
    captured_cmd: list[str] = []

    class FakeYoutubeDL:
        def __init__(self, options: dict) -> None:
            captured_options.update(options)

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, url: str, download: bool) -> dict:
            assert url == "https://www.instagram.com/p/abc/"
            assert download is True
            (job_dir / "Instagram-abc-01.jpg").write_bytes(b"image-1")
            (job_dir / "Instagram-abc-02.jpg").write_bytes(b"image-2")
            (job_dir / "Instagram-abc.m4a").write_bytes(b"audio")
            return {"title": "Photo post"}

    def fake_run(
        cmd: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> None:
        captured_cmd.extend(cmd)
        assert check is True
        assert capture_output is True
        assert text is True
        assert timeout == 120
        Path(cmd[-1]).write_bytes(b"video")

    monkeypatch.setattr("bot.services.downloader.YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr("bot.services.downloader.subprocess.run", fake_run)

    info, video_path = downloader._download_sync("https://www.instagram.com/p/abc/", job_dir)

    assert info == {"title": "Photo post"}
    assert captured_options["noplaylist"] is False
    assert "-shortest" in captured_cmd
    assert video_path == job_dir / "instagram-photo-slideshow.mp4"
    assert video_path.read_bytes() == b"video"
    concat_text = (job_dir / "instagram-photo-slideshow.txt").read_text()
    assert "Instagram-abc-01.jpg" in concat_text
    assert "Instagram-abc-02.jpg" in concat_text


def test_instagram_slideshow_uses_absolute_paths_for_ffmpeg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.services.downloader import _build_instagram_photo_slideshow

    relative_job_dir = Path("downloads/test-job")
    job_dir = tmp_path / relative_job_dir
    job_dir.mkdir(parents=True)
    image_path = job_dir / "instagram-photo-001.jpg"
    image_path.write_bytes(b"image")
    captured_cmd: list[str] = []

    def fake_run(
        cmd: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> None:
        captured_cmd.extend(cmd)
        Path(cmd[-1]).write_bytes(b"video")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("bot.services.downloader.subprocess.run", fake_run)

    _build_instagram_photo_slideshow(
        job_dir=relative_job_dir,
        image_paths=[relative_job_dir / image_path.name],
        audio_path=None,
    )

    concat_path = job_dir / "instagram-photo-slideshow.txt"
    assert str(image_path.resolve()) in concat_path.read_text()
    assert captured_cmd[captured_cmd.index("-i") + 1] == str(concat_path.resolve())
    assert captured_cmd[-1] == str((job_dir / "instagram-photo-slideshow.mp4").resolve())


def test_download_sync_builds_instagram_slideshow_from_metadata_thumbnails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    downloader = VideoDownloader(tmp_path, max_video_size_bytes=100, retries=1)
    downloaded_urls: list[str] = []

    class FakeYoutubeDL:
        def __init__(self, options: dict) -> None:
            pass

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, url: str, download: bool) -> dict:
            assert download is True
            return {
                "_type": "playlist",
                "entries": [
                    {
                        "thumbnails": [
                            {
                                "url": "https://cdn.example/one-small.jpg",
                                "width": 100,
                                "height": 100,
                            },
                            {
                                "url": "https://cdn.example/one-large.jpg",
                                "width": 800,
                                "height": 800,
                            },
                        ]
                    },
                    {
                        "thumbnails": [
                            {"url": "https://cdn.example/two.jpg", "width": 800, "height": 800}
                        ]
                    },
                ],
                "http_headers": {"Referer": "https://www.instagram.com/"},
            }

    def fake_download_url_to_path(url: str, path: Path, headers: dict[str, str]) -> None:
        downloaded_urls.append(url)
        assert headers["Referer"] == "https://www.instagram.com/"
        path.write_bytes(b"image")

    def fake_run(
        cmd: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> None:
        Path(cmd[-1]).write_bytes(b"video")

    monkeypatch.setattr("bot.services.downloader.YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr("bot.services.downloader._download_url_to_path", fake_download_url_to_path)
    monkeypatch.setattr("bot.services.downloader.subprocess.run", fake_run)

    _info, video_path = downloader._download_sync("https://www.instagram.com/p/abc/", job_dir)

    assert downloaded_urls == [
        "https://cdn.example/one-large.jpg",
        "https://cdn.example/two.jpg",
    ]
    assert video_path == job_dir / "instagram-photo-slideshow.mp4"


def test_download_sync_builds_instagram_slideshow_from_graphql_when_entries_are_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    downloader = VideoDownloader(tmp_path, max_video_size_bytes=100, retries=1)
    downloaded_urls: list[str] = []

    class FakeYoutubeDL:
        def __init__(self, options: dict) -> None:
            pass

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, url: str, download: bool) -> dict:
            assert download is True
            return {"_type": "playlist", "entries": []}

    def fake_download_url_to_path(url: str, path: Path, headers: dict[str, str]) -> None:
        downloaded_urls.append(url)
        path.write_bytes(b"image")

    def fake_run(
        cmd: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> None:
        Path(cmd[-1]).write_bytes(b"video")

    monkeypatch.setattr("bot.services.downloader.YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(
        "bot.services.downloader._fetch_instagram_graphql_image_urls",
        lambda url: ["https://cdn.example/one.jpg", "https://cdn.example/two.jpg"],
    )
    monkeypatch.setattr("bot.services.downloader._download_url_to_path", fake_download_url_to_path)
    monkeypatch.setattr("bot.services.downloader.subprocess.run", fake_run)

    _info, video_path = downloader._download_sync("https://www.instagram.com/p/abc/", job_dir)

    assert downloaded_urls == ["https://cdn.example/one.jpg", "https://cdn.example/two.jpg"]
    assert video_path == job_dir / "instagram-photo-slideshow.mp4"


def test_instagram_metadata_image_download_keeps_successful_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.services.downloader import _download_instagram_images_from_metadata
    from bot.services.errors import DownloadError

    def fake_download_url_to_path(url: str, path: Path, headers: dict[str, str]) -> None:
        if "broken" in url:
            raise DownloadError("network timeout")
        path.write_bytes(b"image")

    monkeypatch.setattr("bot.services.downloader._download_url_to_path", fake_download_url_to_path)

    image_paths = _download_instagram_images_from_metadata(
        tmp_path,
        [
            "https://cdn.example/one.jpg",
            "https://cdn.example/broken.jpg",
            "https://cdn.example/two.jpg",
        ],
        {},
    )

    assert [path.name for path in image_paths] == [
        "instagram-photo-001.jpg",
        "instagram-photo-003.jpg",
    ]


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
