import pytest

from bot.middlewares.rate_limit import InlineRateLimitMiddleware


class User:
    id = 42


class FakeInlineQuery:
    def __init__(self) -> None:
        self.from_user = User()
        self.answers: list[dict] = []

    async def answer(self, **kwargs) -> None:
        self.answers.append(kwargs)


@pytest.mark.asyncio
async def test_rate_limit_allows_requests_under_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 100.0
    monkeypatch.setattr("bot.middlewares.rate_limit.monotonic", lambda: now)
    middleware = InlineRateLimitMiddleware(per_minute=2, cooldown_seconds=0)
    event = FakeInlineQuery()
    calls = 0

    async def handler(_event, _data):
        nonlocal calls
        calls += 1
        return "ok"

    assert await middleware(handler, event, {}) == "ok"
    assert await middleware(handler, event, {}) == "ok"
    assert calls == 2
    assert event.answers == []


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_per_minute_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 100.0
    monkeypatch.setattr("bot.middlewares.rate_limit.monotonic", lambda: now)
    middleware = InlineRateLimitMiddleware(per_minute=1, cooldown_seconds=0)
    event = FakeInlineQuery()

    async def handler(_event, _data):
        return "ok"

    assert await middleware(handler, event, {}) == "ok"
    assert await middleware(handler, event, {}) is None

    assert event.answers
    assert event.answers[0]["results"][0].title == "Rate limit reached"


@pytest.mark.asyncio
async def test_rate_limit_blocks_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    current_time = 100.0
    monkeypatch.setattr("bot.middlewares.rate_limit.monotonic", lambda: current_time)
    middleware = InlineRateLimitMiddleware(per_minute=10, cooldown_seconds=5)
    event = FakeInlineQuery()

    async def handler(_event, _data):
        return "ok"

    assert await middleware(handler, event, {}) == "ok"

    current_time = 103.0
    assert await middleware(handler, event, {}) is None

    assert event.answers
    assert event.answers[0]["results"][0].title == "Please wait a few seconds"
