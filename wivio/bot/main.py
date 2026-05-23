from __future__ import annotations

import asyncio
import logging

from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
#тест
from bot.app import build_app
from bot.config import load_settings
from bot.healthcheck import healthcheck
from bot.utils.logging import setup_logging

logger = logging.getLogger(__name__)


async def run_polling() -> None:
    settings = load_settings()
    setup_logging(settings.logs_dir, settings.log_level)
    bot, dispatcher, database, cleanup = await build_app(settings)

    app = web.Application()
    app.router.add_get(settings.healthcheck_path, healthcheck)
    runner = web.AppRunner(app)

    await runner.setup()
    site = web.TCPSite(runner, settings.host, settings.port)
    await site.start()
    cleanup.start()

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Polling started. Healthcheck: http://%s:%s%s", settings.host, settings.port, settings.healthcheck_path)
        await dispatcher.start_polling(bot)
    finally:
        await cleanup.stop()
        await runner.cleanup()
        await database.close()
        await bot.session.close()


async def run_webhook() -> None:
    settings = load_settings()
    setup_logging(settings.logs_dir, settings.log_level)
    if not settings.webhook_endpoint:
        raise RuntimeError("WEBHOOK_URL is required when BOT_MODE=webhook")

    bot, dispatcher, database, cleanup = await build_app(settings)

    app = web.Application()
    app.router.add_get(settings.healthcheck_path, healthcheck)
    SimpleRequestHandler(
        dispatcher=dispatcher,
        bot=bot,
        secret_token=settings.webhook_secret or None,
    ).register(app, path=settings.webhook_path)
    setup_application(app, dispatcher, bot=bot)

    async def on_startup(_: web.Application) -> None:
        cleanup.start()
        await bot.set_webhook(
            url=settings.webhook_endpoint,
            secret_token=settings.webhook_secret or None,
            drop_pending_updates=True,
        )
        logger.info("Webhook set to %s", settings.webhook_endpoint)

    async def on_shutdown(_: web.Application) -> None:
        await cleanup.stop()
        await bot.delete_webhook(drop_pending_updates=False)
        await database.close()
        await bot.session.close()

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.host, settings.port)
    await site.start()
    logger.info("Webhook server started on %s:%s", settings.host, settings.port)

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


async def main() -> None:
    settings = load_settings()
    if settings.bot_mode == "webhook":
        await run_webhook()
        return
    await run_polling()


if __name__ == "__main__":
    asyncio.run(main())
