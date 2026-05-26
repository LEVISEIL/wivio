from __future__ import annotations

import logging
from hashlib import sha1
from typing import Any

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.handlers import InlineQueryHandler
from aiogram.types import InlineQuery, InlineQueryResultsButton

from bot.database.models import CachedVideo
from bot.database.repositories import UserRepository
from bot.services.downloader import build_caption
from bot.services.errors import VideoBotError
from bot.services.video_cache import (
    FAILED_STATUS,
    RESTRICTED_STATUS,
    TIMEOUT_STATUS,
    VideoCacheService,
)
from bot.utils.inline_results import article_result, cached_video_result
from bot.utils.urls import ParsedVideoUrl, UnsupportedUrlError, parse_video_url

logger = logging.getLogger(__name__)

router = Router(name="inline")

BRAND_FOOTER_TEMPLATES = (
    '🤍 Спасибо, что пользуетесь <a href="https://t.me/{username}">@{username}</a>',
    '🔥 Видео отправлено с помощью <a href="https://t.me/{username}">@{username}</a>',
    '⚡️ Отправляйте видео прям в чат через <a href="https://t.me/{username}">@{username}</a>',
)
MAX_INLINE_CAPTION_LENGTH = 1024
MAX_INLINE_READY_WAIT_SECONDS = 8


class VideoInlineQueryHandler(InlineQueryHandler):
    async def handle(self) -> Any:
        inline_query: InlineQuery = self.event
        video_cache: VideoCacheService = self.data["video_cache"]
        users: UserRepository = self.data["users"]
        cache_time: int = self.data["inline_cache_time"]
        ready_wait_seconds: int = self.data["inline_ready_wait_seconds"]
        bot_username: str = self.data["bot_username"]
        await users.touch(inline_query.from_user, "inline")

        query = inline_query.query.strip()
        if not query:
            logger.debug("Empty inline query user_id=%s", inline_query.from_user.id)
            await _answer_inline(
                inline_query,
                results=[
                    article_result(
                        "empty",
                        "Вставьте ссылку и дождитесь обработки видео",
                        "Если ссылка уже вставлена, подождите несколько секунд.",
                        "Поддерживаются TikTok, Reels, Instagram posts и Shorts",
                    )
                ],
                cache_time=1,
                is_personal=True,
            )
            return

        try:
            parsed = parse_video_url(query)
        except UnsupportedUrlError as exc:
            logger.info(
                "Unsupported inline query user_id=%s query=%r error=%s",
                inline_query.from_user.id,
                query[:200],
                exc,
            )
            await _answer_inline(
                inline_query,
                results=[
                    article_result(
                        "unsupported",
                        "Unsupported link",
                        str(exc),
                        "Send a TikTok, Instagram Reels, or YouTube Shorts link",
                    )
                ],
                cache_time=1,
                is_personal=True,
            )
            return

        try:
            cached, status = await video_cache.get_or_enqueue(
                parsed,
                user_id=inline_query.from_user.id,
            )
        except VideoBotError as exc:
            await _answer_inline(
                inline_query,
                results=[
                    article_result(
                        _result_id(parsed.normalized_url, "error"),
                        "Video is not ready",
                        exc.user_message,
                        "Try again later or use another public video",
                    )
                ],
                cache_time=1,
                is_personal=True,
            )
            return
        except Exception:
            logger.exception("Unhandled inline processing error")
            await _answer_inline(
                inline_query,
                results=[
                    article_result(
                        _result_id(parsed.normalized_url, "unexpected"),
                        "Something went wrong",
                        "Could not process this video. Try again later.",
                        "Unexpected processing error",
                    )
                ],
                cache_time=1,
                is_personal=True,
            )
            return

        if cached is None:
            logger.info(
                "Inline video is not ready yet user_id=%s normalized_url=%s status=%s",
                inline_query.from_user.id,
                parsed.normalized_url,
                status,
            )
            if status in {FAILED_STATUS, TIMEOUT_STATUS, RESTRICTED_STATUS}:
                await users.increment_failure(inline_query.from_user.id)
                await _answer_inline(
                    inline_query,
                    results=[],
                    cache_time=0,
                    is_personal=True,
                    button=_failed_button(status),
                )
                return

            cached = await _wait_for_inline_ready(
                video_cache=video_cache,
                parsed=parsed,
                user_id=inline_query.from_user.id,
                status=status,
                ready_wait_seconds=ready_wait_seconds,
            )
            if cached is not None:
                logger.info(
                    "Inline video became ready in same query user_id=%s normalized_url=%s",
                    inline_query.from_user.id,
                    cached.normalized_url,
                )
            else:
                status = video_cache.get_recent_failure_status(parsed.normalized_url) or status
                if status in {FAILED_STATUS, TIMEOUT_STATUS, RESTRICTED_STATUS}:
                    logger.info(
                        "Inline video failed during wait user_id=%s normalized_url=%s status=%s",
                        inline_query.from_user.id,
                        parsed.normalized_url,
                        status,
                    )
                    await users.increment_failure(inline_query.from_user.id)
                    await _answer_inline(
                        inline_query,
                        results=[],
                        cache_time=0,
                        is_personal=True,
                        button=_failed_button(status),
                    )
                    return

                await _answer_inline(
                    inline_query,
                    results=[],
                    cache_time=0,
                    is_personal=True,
                    button=_loading_button(),
                )
                return

        description = f"Cached | {cached.platform.replace('_', ' ').title()}"
        logger.info(
            "Inline cached video result user_id=%s normalized_url=%s platform=%s",
            inline_query.from_user.id,
            cached.normalized_url,
            cached.platform,
        )
        await users.increment_success(inline_query.from_user.id)
        await _answer_inline(
            inline_query,
            results=[
                _cached_video_inline_result(
                    cached=cached,
                    description=description,
                    bot_username=bot_username,
                    variant_key=inline_query.id,
                )
            ],
            cache_time=min(cache_time, 1),
            is_personal=True,
        )


async def _wait_for_inline_ready(
    video_cache: VideoCacheService,
    parsed: ParsedVideoUrl,
    user_id: int,
    status: str,
    ready_wait_seconds: int,
) -> CachedVideo | None:
    ready_wait_seconds = _inline_ready_wait_seconds(ready_wait_seconds)
    if ready_wait_seconds <= 0:
        return None

    logger.info(
        "Waiting for inline video readiness user_id=%s normalized_url=%s status=%s seconds=%s",
        user_id,
        parsed.normalized_url,
        status,
        ready_wait_seconds,
    )
    return await video_cache.wait_for_cached(
        parsed,
        user_id=user_id,
        timeout_seconds=ready_wait_seconds,
    )


def _cached_video_inline_result(
    cached: CachedVideo,
    description: str,
    bot_username: str,
    variant_key: str | None = None,
) -> Any:
    caption_variant_key = f"{cached.normalized_url}:{variant_key or cached.normalized_url}"
    return cached_video_result(
        result_id=_result_id(
            cached.normalized_url,
            cached.telegram_file_unique_id or "video",
        ),
        file_id=cached.telegram_file_id,
        title=cached.title,
        caption=_caption_with_brand_footer(
            caption=_source_caption(cached),
            bot_username=bot_username,
            variant_key=caption_variant_key,
        ),
        description=description,
    )


def _source_caption(cached: CachedVideo) -> str:
    return build_caption(
        title="",
        platform=cached.platform,
        url=cached.original_url or cached.normalized_url,
    )


def _caption_with_brand_footer(caption: str, bot_username: str, variant_key: str) -> str:
    footer = _brand_footer(bot_username, variant_key)
    separator = "\n\n"
    if not caption:
        return footer[:MAX_INLINE_CAPTION_LENGTH]

    branded_caption = f"{caption}{separator}{footer}"
    if len(branded_caption) <= MAX_INLINE_CAPTION_LENGTH:
        return branded_caption

    max_caption_length = MAX_INLINE_CAPTION_LENGTH - len(separator) - len(footer)
    if max_caption_length <= 0:
        return footer[:MAX_INLINE_CAPTION_LENGTH]
    return f"{caption[:max_caption_length]}{separator}{footer}"


def _brand_footer(bot_username: str, variant_key: str) -> str:
    username = bot_username.lstrip("@")
    template = BRAND_FOOTER_TEMPLATES[_variant_index(variant_key, len(BRAND_FOOTER_TEMPLATES))]
    return template.format(username=username)


def _variant_index(value: str, size: int) -> int:
    if size <= 0:
        return 0
    digest = sha1(value.encode("utf-8")).hexdigest()
    return int(digest, 16) % size


def _loading_button() -> InlineQueryResultsButton:
    return InlineQueryResultsButton(
        text="Видео не успело загрузиться. Вставьте ссылку ещё раз",
        start_parameter="loading",
    )


def _inline_ready_wait_seconds(configured_seconds: int) -> int:
    return max(0, min(configured_seconds, MAX_INLINE_READY_WAIT_SECONDS))


def _failed_button(status: str) -> InlineQueryResultsButton:
    if status == TIMEOUT_STATUS:
        text = "Видео обрабатывалось слишком долго. Попробуйте ещё раз"
    elif status == RESTRICTED_STATUS:
        text = "Instagram ограничил доступ к этому видео"
    else:
        text = "Не удалось скачать. Возможно, нужен вход в Instagram"
    return InlineQueryResultsButton(
        text=text,
        start_parameter=status,
    )


def _result_id(*parts: str) -> str:
    digest = sha1("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


async def _answer_inline(
    inline_query: InlineQuery,
    results: list[Any],
    cache_time: int,
    is_personal: bool,
    button: InlineQueryResultsButton | None = None,
) -> None:
    try:
        await inline_query.answer(
            results=results,
            cache_time=cache_time,
            is_personal=is_personal,
            button=button,
        )
    except TelegramBadRequest as exc:
        error_text = str(exc).lower()
        if (
            "query is too old" in error_text
            or "query id is invalid" in error_text
            or "query is already answered" in error_text
            or "query was already answered" in error_text
        ):
            logger.warning("Inline query expired before answer: %s", inline_query.id)
            return
        raise


router.inline_query()(VideoInlineQueryHandler)
