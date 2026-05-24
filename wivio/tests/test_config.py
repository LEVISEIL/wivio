from pathlib import Path

import pytest

import bot.config as config


def test_load_settings_reads_required_and_default_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.setenv("BOT_TOKEN", "123:token")
    monkeypatch.setenv("BOT_USERNAME", "@wivio_bot")
    monkeypatch.setenv("UPLOAD_CHAT_ID", "-100123")
    monkeypatch.setenv("DATABASE_PATH", "/tmp/test.sqlite3")
    monkeypatch.setenv("DEBUG", "yes")

    settings = config.load_settings()

    assert settings.bot_token == "123:token"
    assert settings.bot_username == "wivio_bot"
    assert settings.upload_chat_id == -100123
    assert settings.database_path == Path("/tmp/test.sqlite3")
    assert settings.bot_mode == "polling"
    assert settings.max_video_size_bytes == 49 * 1024 * 1024
    assert settings.inline_ready_wait_seconds == 12
    assert settings.alerts_enabled is False
    assert settings.alert_bot_token == "123:token"
    assert settings.alert_chat_id == ""
    assert settings.alert_level == "ERROR"
    assert settings.alert_ssl_verify is True
    assert settings.debug is True


def test_load_settings_requires_telegram_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("BOT_USERNAME", raising=False)
    monkeypatch.delenv("UPLOAD_CHAT_ID", raising=False)

    with pytest.raises(RuntimeError, match="BOT_TOKEN, BOT_USERNAME, UPLOAD_CHAT_ID"):
        config.load_settings()


def test_webhook_endpoint_appends_path_when_needed() -> None:
    settings = config.Settings(
        bot_token="token",
        bot_username="bot",
        upload_chat_id=1,
        bot_mode="webhook",
        webhook_url="https://example.com",
        webhook_secret="secret",
        webhook_path="/webhook",
        host="0.0.0.0",
        port=8080,
        healthcheck_path="/healthz",
        database_path=Path("data/bot.sqlite3"),
        downloads_dir=Path("downloads"),
        logs_dir=Path("logs"),
        max_video_size_mb=49,
        inline_download_timeout=45,
        inline_ready_wait_seconds=12,
        download_retries=2,
        upload_retries=2,
        rate_limit_per_minute=6,
        cooldown_seconds=0,
        inline_cache_time=86400,
        temp_file_ttl_seconds=3600,
        cleanup_interval_seconds=900,
        log_level="INFO",
        alerts_enabled=False,
        alert_bot_token="token",
        alert_chat_id="",
        alert_level="ERROR",
        alert_ssl_verify=True,
        debug=False,
    )

    assert settings.webhook_endpoint == "https://example.com/webhook"
