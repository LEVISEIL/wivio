import pytest

from bot.utils.retry import retry_async


@pytest.mark.asyncio
async def test_retry_async_retries_until_success() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ValueError("not yet")
        return "ok"

    result = await retry_async(operation, attempts=3, base_delay=0)

    assert result == "ok"
    assert attempts == 3


@pytest.mark.asyncio
async def test_retry_async_raises_last_error() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise RuntimeError(f"boom {attempts}")

    with pytest.raises(RuntimeError, match="boom 2"):
        await retry_async(operation, attempts=2, base_delay=0)

    assert attempts == 2
