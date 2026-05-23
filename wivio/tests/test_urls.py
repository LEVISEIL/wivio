import pytest

from bot.utils.urls import (
    Platform,
    UnsupportedUrlError,
    extract_first_url,
    normalize_url,
    parse_video_url,
)


def test_extract_first_url_strips_common_trailing_punctuation() -> None:
    assert extract_first_url("watch https://vm.tiktok.com/ZNRnPAR4S/.") == (
        "https://vm.tiktok.com/ZNRnPAR4S/"
    )


def test_parse_tiktok_short_link() -> None:
    parsed = parse_video_url("@wivio_bot https://vm.tiktok.com/ZNRnPAR4S/")

    assert parsed.original_url == "https://vm.tiktok.com/ZNRnPAR4S/"
    assert parsed.normalized_url == "https://vm.tiktok.com/ZNRnPAR4S"
    assert parsed.platform == Platform.TIKTOK


def test_parse_instagram_reel() -> None:
    parsed = parse_video_url("https://www.instagram.com/reel/DYmi5rJMeRs/?igsh=abc")

    assert parsed.normalized_url == "https://instagram.com/reel/DYmi5rJMeRs"
    assert parsed.platform == Platform.INSTAGRAM


def test_parse_youtube_shorts() -> None:
    parsed = parse_video_url("https://m.youtube.com/shorts/aRa1aCDEj4M?si=share")

    assert parsed.normalized_url == "https://youtube.com/shorts/aRa1aCDEj4M"
    assert parsed.platform == Platform.YOUTUBE_SHORTS


def test_rejects_instagram_non_reel() -> None:
    with pytest.raises(UnsupportedUrlError, match="Only Instagram Reels"):
        parse_video_url("https://instagram.com/p/example")


def test_rejects_youtube_non_shorts_page() -> None:
    with pytest.raises(UnsupportedUrlError, match="Only YouTube Shorts"):
        parse_video_url("https://youtube.com/watch?v=aRa1aCDEj4M")


def test_rejects_unsupported_platform() -> None:
    with pytest.raises(UnsupportedUrlError, match="Unsupported platform"):
        parse_video_url("https://example.com/video")


def test_normalize_url_removes_tracking_and_keeps_real_query_params() -> None:
    normalized = normalize_url(
        "HTTPS://www.TIKTOK.com/@user/video/123/?utm_source=x&lang=en&fbclid=abc"
    )

    assert normalized == "https://tiktok.com/@user/video/123?lang=en"
