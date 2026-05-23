from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    attempts: int,
    base_delay: float = 1.0,
) -> T:
    last_error: BaseException | None = None
    for attempt in range(max(1, attempts)):
        try:
            return await operation()
        except Exception as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            await asyncio.sleep(base_delay * (2**attempt))

    assert last_error is not None
    raise last_error
