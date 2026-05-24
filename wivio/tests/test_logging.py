import logging

from bot.utils.logging import TelegramAlertHandler, setup_logging


def test_setup_logging_skips_telegram_alert_handler_when_disabled(tmp_path) -> None:
    setup_logging(tmp_path, "INFO")

    handlers = logging.getLogger().handlers

    assert not any(isinstance(handler, TelegramAlertHandler) for handler in handlers)
    assert (tmp_path / "bot.log").exists()


def test_setup_logging_adds_telegram_alert_handler_when_configured(tmp_path) -> None:
    setup_logging(
        tmp_path,
        "INFO",
        telegram_alerts_enabled=True,
        telegram_alert_bot_token="token",
        telegram_alert_chat_id="-100123",
        telegram_alert_message_thread_id=777,
    )

    handlers = logging.getLogger().handlers

    alert_handler = next(
        handler for handler in handlers if isinstance(handler, TelegramAlertHandler)
    )
    assert alert_handler.message_thread_id == 777


def test_telegram_alert_handler_truncates_long_messages() -> None:
    handler = TelegramAlertHandler(
        bot_token="token",
        chat_id="-100123",
        ssl_verify=False,
        level=logging.ERROR,
    )
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="x" * 5000,
        args=(),
        exc_info=None,
    )

    message = handler._format_message(record)
    handler.close()

    assert len(message) < 4096
    assert message.endswith("...[truncated]")


def test_telegram_alert_handler_formats_readable_alert() -> None:
    handler = TelegramAlertHandler(
        bot_token="token",
        chat_id="-100123",
        level=logging.ERROR,
    )
    record = logging.LogRecord(
        name="bot.test",
        level=logging.ERROR,
        pathname="/app/bot/test.py",
        lineno=12,
        msg="Download failed for %s",
        args=("https://example.com/video",),
        exc_info=None,
    )

    message = handler._format_message(record)
    handler.close()

    assert "<b>Wivio Alert</b>" in message
    assert "<b>Level:</b> <code>ERROR</code>" in message
    assert "<b>Logger:</b> <code>bot.test</code>" in message
    assert "<code>Download failed for https://example.com/video</code>" in message


def test_telegram_alert_handler_stores_ssl_verify_setting() -> None:
    handler = TelegramAlertHandler(
        bot_token="token",
        chat_id="-100123",
        message_thread_id=777,
        ssl_verify=False,
    )

    assert handler.ssl_verify is False
    assert handler.message_thread_id == 777
    handler.close()
