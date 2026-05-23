from __future__ import annotations

import logging
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import InlineQuery

from bot.utils.inline_results import article_result

logger = logging.getLogger(__name__)


class InlineRateLimitMiddleware(BaseMiddleware):
    def __init__(self, per_minute: int, cooldown_seconds: int) -> None:
        self.per_minute = per_minute
        self.cooldown_seconds = cooldown_seconds
        self._hits: defaultdict[int, deque[float]] = defaultdict(deque)
        self._last_seen: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[InlineQuery, dict[str, Any]], Awaitable[Any]],
        event: InlineQuery,
        data: dict[str, Any],
    ) -> Any:
        user_id = event.from_user.id
        now = monotonic()

        last_seen = self._last_seen.get(user_id, 0)
        if now - last_seen < self.cooldown_seconds:
            logger.info("Inline cooldown hit user_id=%s", user_id)
            await event.answer(
                results=[
                    article_result(
                        "cooldown",
                        "Please wait a few seconds",
                        "Too many requests. Try again in a few seconds.",
                        "Cooldown is active",
                    )
                ],
                cache_time=1,
                is_personal=True,
            )
            return None

        hits = self._hits[user_id]
        while hits and now - hits[0] > 60:
            hits.popleft()

        if len(hits) >= self.per_minute:
            logger.warning(
                "Inline rate limit hit user_id=%s hits=%s per_minute=%s",
                user_id,
                len(hits),
                self.per_minute,
            )
            await event.answer(
                results=[
                    article_result(
                        "rate-limit",
                        "Rate limit reached",
                        "Too many videos in one minute. Please wait a bit.",
                        "Rate limit is active",
                    )
                ],
                cache_time=1,
                is_personal=True,
            )
            return None

        hits.append(now)
        self._last_seen[user_id] = now
        return await handler(event, data)
