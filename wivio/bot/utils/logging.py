from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class FutureTelegramAlertHandler(logging.Handler):
    """Placeholder for production critical-error alerts."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.CRITICAL:
            return

        # Future hook: send formatted critical records to Telegram/Slack/etc.
        # Keep this handler side-effect free until alert credentials are configured.
        return


def setup_logging(logs_dir: Path, level: str) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        logs_dir / "bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(file_handler)
    root.addHandler(FutureTelegramAlertHandler(level=logging.CRITICAL))

    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("yt_dlp").setLevel(logging.WARNING)
