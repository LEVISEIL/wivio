import pytest

from bot.database.models import CachedVideo
from bot.handlers.inline import (
    MAX_INLINE_CAPTION_LENGTH,
    _brand_footer,
    _cached_video_inline_result,
    _caption_with_brand_footer,
    _failed_button,
    _loading_button,
    _wait_for_inline_ready,
)
from bot.services.video_cache import FAILED_STATUS, RESTRICTED_STATUS, TIMEOUT_STATUS
from bot.utils.urls import ParsedVideoUrl, Platform


class FakeVideoCache:
    def __init__(self, cached: CachedVideo | None) -> None:
        self.cached = cached
        self.calls: list[tuple[ParsedVideoUrl, int | None, int | None]] = []

    async def wait_for_cached(
        self,
        parsed_url: ParsedVideoUrl,
        user_id: int | None,
        timeout_seconds: int | None = None,
    ) -> CachedVideo | None:
        self.calls.append((parsed_url, user_id, timeout_seconds))
        return self.cached


def test_loading_button_is_status_only_for_inline_results() -> None:
    button = _loading_button()

    assert button.text == "Видео обрабатывается. Обновите запрос через пару секунд"
    assert button.start_parameter == "loading"


def test_failed_button_explains_download_failure() -> None:
    button = _failed_button(FAILED_STATUS)

    assert button.text == "Не удалось скачать. Возможно, нужен вход в Instagram"
    assert button.start_parameter == FAILED_STATUS


def test_failed_button_explains_timeout() -> None:
    button = _failed_button(TIMEOUT_STATUS)

    assert button.text == "Видео обрабатывалось слишком долго. Попробуйте ещё раз"
    assert button.start_parameter == TIMEOUT_STATUS


def test_failed_button_explains_restricted_instagram_video() -> None:
    button = _failed_button(RESTRICTED_STATUS)

    assert button.text == "Instagram ограничил доступ к этому видео"
    assert button.start_parameter == RESTRICTED_STATUS


def test_cached_video_result_adds_brand_footer_to_caption() -> None:
    result = _cached_video_inline_result(
        cached_video(),
        "Cached | Tiktok",
        "@wivio_bot",
        variant_key="query-1",
    )

    assert "<b>Cached</b>" in result.caption
    assert "@wivio_bot</a>" in result.caption
    assert "https://t.me/wivio_bot" in result.caption


def test_brand_footer_has_stable_variants() -> None:
    first = _brand_footer("@wivio_bot", "https://example.com/video-1")
    second = _brand_footer("@wivio_bot", "https://example.com/video-1")

    assert first == second
    assert "@wivio_bot</a>" in first


def test_caption_with_brand_footer_keeps_description_before_brand_text() -> None:
    caption = _caption_with_brand_footer("C" * 2000, "@wivio_bot", "key")

    assert len(caption) == MAX_INLINE_CAPTION_LENGTH
    assert caption.startswith("C")
    assert "@wivio_bot</a>" in caption


@pytest.mark.asyncio
async def test_wait_for_inline_ready_returns_cached_video() -> None:
    cache = FakeVideoCache(cached_video())

    result = await _wait_for_inline_ready(
        video_cache=cache,
        parsed=parsed_url(),
        user_id=42,
        status="queued",
        ready_wait_seconds=8,
    )

    assert result == cached_video()
    assert cache.calls == [(parsed_url(), 42, 8)]


@pytest.mark.asyncio
async def test_wait_for_inline_ready_can_be_disabled() -> None:
    cache = FakeVideoCache(cached_video())

    result = await _wait_for_inline_ready(
        video_cache=cache,
        parsed=parsed_url(),
        user_id=42,
        status="queued",
        ready_wait_seconds=0,
    )

    assert result is None
    assert cache.calls == []


def parsed_url() -> ParsedVideoUrl:
    return ParsedVideoUrl(
        original_url="https://vm.tiktok.com/ZNRnPAR4S/",
        normalized_url="https://vm.tiktok.com/ZNRnPAR4S",
        platform=Platform.TIKTOK,
    )


def cached_video() -> CachedVideo:
    return CachedVideo(
        normalized_url=parsed_url().normalized_url,
        original_url=parsed_url().original_url,
        platform="tiktok",
        title="Cached",
        caption="<b>Cached</b>",
        thumbnail_url=None,
        telegram_file_id="cached-file-id",
        telegram_file_unique_id="cached-unique-id",
        file_size=10,
        duration=1,
        width=100,
        height=100,
    )
