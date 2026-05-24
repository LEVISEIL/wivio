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
    )

    handlers = logging.getLogger().handlers

    assert any(isinstance(handler, TelegramAlertHandler) for handler in handlers)


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


def test_telegram_alert_handler_stores_ssl_verify_setting() -> None:
    handler = TelegramAlertHandler(
        bot_token="token",
        chat_id="-100123",
        ssl_verify=False,
    )

    assert handler.ssl_verify is False
    handler.close()
