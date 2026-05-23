from __future__ import annotations

import logging
from hashlib import sha1
from typing import Any

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.handlers import InlineQueryHandler
from aiogram.types import InlineQuery

from bot.services.errors import VideoBotError
from bot.services.video_cache import VideoCacheService
from bot.utils.inline_results import article_result, cached_video_result
from bot.utils.urls import UnsupportedUrlError, parse_video_url

logger = logging.getLogger(__name__)

router = Router(name="inline")


class VideoInlineQueryHandler(InlineQueryHandler):
    async def handle(self) -> Any:
        inline_query: InlineQuery = self.event
        video_cache: VideoCacheService = self.data["video_cache"]
        cache_time: int = self.data["inline_cache_time"]

        query = inline_query.query.strip()
        if not query:
            logger.debug("Empty inline query user_id=%s", inline_query.from_user.id)
            await _answer_inline(
                inline_query,
                results=[
                    article_result(
                        "empty",
                        "Paste a video link",
                        "Use inline mode with a TikTok, Instagram Reels, or YouTube Shorts URL.",
                        "TikTok, Reels, and Shorts are supported",
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
            await _answer_inline(
                inline_query,
                results=[
                    article_result(
                        _result_id(parsed.normalized_url, status),
                        "Видео загружается",
                        "Видео загружается. Подождите несколько секунд и обновите inline-запрос.",
                        "Не отправляйте этот результат, дождитесь появления видео",
                    )
                ],
                cache_time=1,
                is_personal=True,
            )
            return

        description = f"Cached | {cached.platform.replace('_', ' ').title()}"
        logger.info(
            "Inline cached video result user_id=%s normalized_url=%s platform=%s",
            inline_query.from_user.id,
            cached.normalized_url,
            cached.platform,
        )
        await _answer_inline(
            inline_query,
            results=[
                cached_video_result(
                    result_id=_result_id(
                        cached.normalized_url,
                        cached.telegram_file_unique_id or "video",
                    ),
                    file_id=cached.telegram_file_id,
                    title=cached.title,
                    caption=cached.caption,
                    description=description,
                )
            ],
            cache_time=cache_time,
            is_personal=True,
        )


def _result_id(*parts: str) -> str:
    digest = sha1("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


async def _answer_inline(
    inline_query: InlineQuery,
    results: list[Any],
    cache_time: int,
    is_personal: bool,
) -> None:
    try:
        await inline_query.answer(
            results=results,
            cache_time=cache_time,
            is_personal=is_personal,
        )
    except TelegramBadRequest as exc:
        if "query is too old" in str(exc).lower() or "query id is invalid" in str(exc).lower():
            logger.warning("Inline query expired before answer: %s", inline_query.id)
            return
        raise


router.inline_query()(VideoInlineQueryHandler)
