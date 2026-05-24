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
from bot.services.errors import VideoBotError
from bot.services.video_cache import FAILED_STATUS, TIMEOUT_STATUS, VideoCacheService
from bot.utils.inline_results import article_result, cached_video_result
from bot.utils.urls import ParsedVideoUrl, UnsupportedUrlError, parse_video_url

logger = logging.getLogger(__name__)

router = Router(name="inline")


class VideoInlineQueryHandler(InlineQueryHandler):
    async def handle(self) -> Any:
        inline_query: InlineQuery = self.event
        video_cache: VideoCacheService = self.data["video_cache"]
        users: UserRepository = self.data["users"]
        cache_time: int = self.data["inline_cache_time"]
        ready_wait_seconds: int = self.data["inline_ready_wait_seconds"]
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
            if status in {FAILED_STATUS, TIMEOUT_STATUS}:
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
            results=[_cached_video_inline_result(cached, description)],
            cache_time=cache_time,
            is_personal=True,
        )


async def _wait_for_inline_ready(
    video_cache: VideoCacheService,
    parsed: ParsedVideoUrl,
    user_id: int,
    status: str,
    ready_wait_seconds: int,
) -> CachedVideo | None:
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


def _cached_video_inline_result(cached: CachedVideo, description: str) -> Any:
    return cached_video_result(
        result_id=_result_id(
            cached.normalized_url,
            cached.telegram_file_unique_id or "video",
        ),
        file_id=cached.telegram_file_id,
        title=cached.title,
        caption=cached.caption,
        description=description,
    )


def _loading_button() -> InlineQueryResultsButton:
    return InlineQueryResultsButton(
        text="Видео обрабатывается. Обновите запрос через пару секунд",
        start_parameter="loading",
    )


def _failed_button(status: str) -> InlineQueryResultsButton:
    if status == TIMEOUT_STATUS:
        text = "Видео обрабатывалось слишком долго. Попробуйте ещё раз"
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
