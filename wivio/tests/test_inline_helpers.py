import pytest

from bot.database.models import CachedVideo
from bot.handlers.inline import (
    MAX_INLINE_CAPTION_LENGTH,
    MAX_INLINE_READY_WAIT_SECONDS,
    MAX_SLOW_INLINE_READY_WAIT_SECONDS,
    _brand_footer,
    _cached_video_inline_result,
    _caption_with_brand_footer,
    _failed_button,
    _inline_ready_wait_seconds,
    _invalid_link_result,
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

    assert button.text == "Видео не успело загрузиться. Вставьте ссылку ещё раз"
    assert button.start_parameter == "loading"


def test_inline_ready_wait_is_clamped_before_telegram_query_expires() -> None:
    assert _inline_ready_wait_seconds(12) == MAX_INLINE_READY_WAIT_SECONDS
    assert _inline_ready_wait_seconds(4) == 4
    assert _inline_ready_wait_seconds(-1) == 0
    assert _inline_ready_wait_seconds(12, parsed_url()) == MAX_SLOW_INLINE_READY_WAIT_SECONDS


def test_invalid_link_result_explains_supported_links() -> None:
    result = _invalid_link_result()

    assert result.title == "Некорректная ссылка"
    assert result.description == "Проверьте ссылку и вставьте её ещё раз"
    assert "TikTok" in result.input_message_content.message_text
    assert "после имени бота" in result.input_message_content.message_text


def test_failed_button_explains_download_failure() -> None:
    button = _failed_button(FAILED_STATUS)

    assert button.text == "Не удалось скачать видео. Проверьте ссылку и попробуйте ещё раз"
    assert button.start_parameter == FAILED_STATUS


def test_failed_button_explains_tiktok_download_failure() -> None:
    button = _failed_button(FAILED_STATUS, Platform.TIKTOK)

    assert button.text == "Не удалось скачать TikTok. Проверьте ссылку и попробуйте ещё раз"
    assert button.start_parameter == FAILED_STATUS


def test_failed_button_explains_instagram_download_failure() -> None:
    button = _failed_button(FAILED_STATUS, Platform.INSTAGRAM)

    assert button.text == "Не удалось скачать Instagram. Проверьте ссылку или доступ к видео"
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

    assert "<b>Cached</b>" not in result.caption
    assert 'Tiktok | <a href="https://vm.tiktok.com/ZNRnPAR4S/">source</a>' in result.caption
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
        ready_wait_seconds=12,
    )

    assert result == cached_video()
    assert cache.calls == [(parsed_url(), 42, MAX_SLOW_INLINE_READY_WAIT_SECONDS)]


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
