from __future__ import annotations

from aiohttp import web


async def healthcheck(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})
