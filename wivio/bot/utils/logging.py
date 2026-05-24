from __future__ import annotations

import asyncio
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import aiohttp


class TelegramAlertHandler(logging.Handler):
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        message_thread_id: int | None = None,
        timeout_seconds: int = 5,
        ssl_verify: bool = True,
        level: int | str = logging.ERROR,
    ) -> None:
        super().__init__(level)
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.message_thread_id = message_thread_id
        self.timeout_seconds = timeout_seconds
        self.ssl_verify = ssl_verify
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="telegram-alert")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self._format_message(record)
            self._executor.submit(self._send_message, message)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        super().close()

    def _format_message(self, record: logging.LogRecord) -> str:
        message = _format_telegram_alert(record)
        if len(message) <= 3900:
            return message
        return f"{message[:3900]}\n...[truncated]"

    def _send_message(self, text: str) -> None:
        try:
            asyncio.run(self._send_message_async(text))
        except Exception:
            return

    async def _send_message_async(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        connector = aiohttp.TCPConnector(ssl=self.ssl_verify)
        data = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
        if self.message_thread_id is not None:
            data["message_thread_id"] = str(self.message_thread_id)

        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.post(
                url,
                data=data,
            ) as response:
                await response.read()


def setup_logging(
    logs_dir: Path,
    level: str,
    telegram_alerts_enabled: bool = False,
    telegram_alert_bot_token: str = "",
    telegram_alert_chat_id: str = "",
    telegram_alert_message_thread_id: int | None = None,
    telegram_alert_level: str = "ERROR",
    telegram_alert_ssl_verify: bool = True,
) -> None:
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
    for handler in root.handlers[:]:
        handler.close()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(file_handler)

    if telegram_alerts_enabled:
        if telegram_alert_bot_token and telegram_alert_chat_id:
            alert_handler = TelegramAlertHandler(
                bot_token=telegram_alert_bot_token,
                chat_id=telegram_alert_chat_id,
                message_thread_id=telegram_alert_message_thread_id,
                ssl_verify=telegram_alert_ssl_verify,
                level=_parse_level(telegram_alert_level, logging.ERROR),
            )
            alert_handler.setFormatter(formatter)
            root.addHandler(alert_handler)
        else:
            root.warning(
                "Telegram alerts are enabled but ALERT_BOT_TOKEN or ALERT_CHAT_ID is missing"
            )

    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("yt_dlp").setLevel(logging.WARNING)


def _parse_level(value: str, default: int) -> int:
    level = getattr(logging, value.strip().upper(), None)
    if isinstance(level, int):
        return level
    return default


def _format_telegram_alert(record: logging.LogRecord) -> str:
    timestamp = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"{_level_icon(record.levelno)} <b>Wivio Alert</b>",
        "",
        f"<b>Level:</b> <code>{_html_escape(record.levelname)}</code>",
        f"<b>Logger:</b> <code>{_html_escape(record.name)}</code>",
        f"<b>Time:</b> <code>{timestamp}</code>",
        f"<b>Location:</b> <code>{_html_escape(record.pathname)}:{record.lineno}</code>",
        "",
        "<b>Message:</b>",
        f"<code>{_html_escape(record.getMessage())}</code>",
    ]
    if record.exc_info:
        stacktrace = "".join(traceback.format_exception(*record.exc_info)).strip()
        lines.extend(["", "<b>Traceback:</b>", f"<pre>{_html_escape(stacktrace)}</pre>"])
    return "\n".join(lines)


def _level_icon(level: int) -> str:
    if level >= logging.CRITICAL:
        return "CRITICAL"
    if level >= logging.ERROR:
        return "ERROR"
    if level >= logging.WARNING:
        return "WARNING"
    return "INFO"


def _html_escape(value: object) -> str:
    text = str(value)
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
