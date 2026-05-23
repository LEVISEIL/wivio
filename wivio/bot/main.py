from __future__ import annotations

import asyncio
import logging

from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from bot.app import build_app
from bot.config import load_settings
from bot.healthcheck import healthcheck
from bot.utils.logging import setup_logging

logger = logging.getLogger(__name__)


async def run_polling() -> None:
    settings = load_settings()
    setup_logging(settings.logs_dir, settings.log_level)
    logger.info("Starting bot in polling mode")
    bot, dispatcher, database, cleanup = await build_app(settings)

    app = web.Application()
    app.router.add_get(settings.healthcheck_path, healthcheck)
    runner = web.AppRunner(app)

    try:
        await runner.setup()
        site = web.TCPSite(runner, settings.host, settings.port)
        await site.start()
        cleanup.start()
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Polling started. Healthcheck: http://%s:%s%s", settings.host, settings.port, settings.healthcheck_path)
        await dispatcher.start_polling(bot)
    except Exception:
        logger.critical("Polling runtime failed", exc_info=True)
        raise
    finally:
        logger.info("Shutting down polling runtime")
        await cleanup.stop()
        await runner.cleanup()
        await database.close()
        await bot.session.close()


async def run_webhook() -> None:
    settings = load_settings()
    setup_logging(settings.logs_dir, settings.log_level)
    logger.info("Starting bot in webhook mode")
    if not settings.webhook_endpoint:
        logger.critical("WEBHOOK_URL is required when BOT_MODE=webhook")
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
        try:
            cleanup.start()
            await bot.set_webhook(
                url=settings.webhook_endpoint,
                secret_token=settings.webhook_secret or None,
                drop_pending_updates=True,
            )
            logger.info("Webhook set to %s", settings.webhook_endpoint)
        except Exception:
            logger.critical("Webhook startup failed", exc_info=True)
            raise

    async def on_shutdown(_: web.Application) -> None:
        logger.info("Shutting down webhook runtime")
        await cleanup.stop()
        await bot.delete_webhook(drop_pending_updates=False)
        await database.close()
        await bot.session.close()

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = web.TCPSite(runner, settings.host, settings.port)
        await site.start()
        logger.info("Webhook server started on %s:%s", settings.host, settings.port)
        await asyncio.Event().wait()
    except Exception:
        logger.critical("Webhook runtime failed", exc_info=True)
        raise
    finally:
        await runner.cleanup()


async def main() -> None:
    settings = load_settings()
    if settings.bot_mode == "webhook":
        await run_webhook()
        return
    await run_polling()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        logging.getLogger(__name__).critical(
            "Bot process stopped with an unhandled error",
            exc_info=True,
        )
        raise
