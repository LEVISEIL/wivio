import logging

from bot.utils.logging import FutureTelegramAlertHandler, setup_logging


def test_setup_logging_adds_future_alert_handler(tmp_path) -> None:
    setup_logging(tmp_path, "INFO")

    handlers = logging.getLogger().handlers

    assert any(isinstance(handler, FutureTelegramAlertHandler) for handler in handlers)
    assert (tmp_path / "bot.log").exists()


def test_future_alert_handler_is_side_effect_free_for_now() -> None:
    handler = FutureTelegramAlertHandler(level=logging.CRITICAL)
    record = logging.LogRecord(
        name="test",
        level=logging.CRITICAL,
        pathname=__file__,
        lineno=1,
        msg="critical",
        args=(),
        exc_info=None,
    )

    handler.emit(record)
