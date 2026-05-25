import pytest

from bot.database.models import UserStats
from bot.handlers.start import (
    chat_id_message,
    handle_private_fallback,
    my_id_message,
    private_fallback_message,
    start_keyboard,
    start_message,
    stats_message,
    usage_hint_message,
    video_file_id_message,
    welcome_file_id_message,
    welcome_forward_message_config,
)


class FakeUser:
    id = 42


class FakeChat:
    id = 42


class FakeMessage:
    from_user = FakeUser()
    chat = FakeChat()
    text = "привет"
    caption = None

    def __init__(self) -> None:
        self.answers: list[tuple[str, bool | None]] = []

    async def answer(self, text: str, disable_web_page_preview: bool | None = None) -> None:
        self.answers.append((text, disable_web_page_preview))


def test_start_message_explains_inline_usage() -> None:
    message = start_message("@wivio_bot")

    assert "<b>Привет! Я Wivio.</b>" in message
    assert "<code>@wivio_bot</code>" in message
    assert "TikTok" in message
    assert "YouTube Shorts" in message
    assert "обычно это занимает до 5 секунд" in message


def test_start_keyboard_opens_inline_mode_in_current_chat() -> None:
    keyboard = start_keyboard()
    button = keyboard.inline_keyboard[0][0]

    assert button.text == "Попробовать в этом чате"
    assert button.switch_inline_query_current_chat == ""


def test_private_fallback_message_explains_how_to_send_video_link() -> None:
    message = private_fallback_message(
        "@wivio_bot",
        "https://www.instagram.com/reel/abc/?igsh=test",
    )

    assert "открой нужный чат" in message
    assert "<code>@wivio_bot https://www.instagram.com/reel/abc/?igsh=test</code>" in message
    assert "нажми на него" in message
    assert "inline" not in message.lower()


def test_private_fallback_message_handles_plain_text() -> None:
    message = private_fallback_message("@wivio_bot", "привет")

    assert "Открой чат" in message
    assert "<code>@wivio_bot</code>" in message
    assert "Поддерживаются TikTok" in message
    assert "inline" not in message.lower()


def test_usage_hint_message_uses_clean_username() -> None:
    assert "<code>@wivio_bot</code>" in usage_hint_message("wivio_bot")


@pytest.mark.asyncio
async def test_handle_private_fallback_answers_without_user_event_counter() -> None:
    message = FakeMessage()

    await handle_private_fallback(message, "@wivio_bot")

    assert len(message.answers) == 1
    assert "<code>@wivio_bot</code>" in message.answers[0][0]
    assert message.answers[0][1] is True


def test_chat_id_message_contains_chat_id() -> None:
    assert chat_id_message(-100123) == "Chat ID для алертов: <code>-100123</code>"


def test_chat_id_message_contains_topic_thread_id() -> None:
    assert chat_id_message(-100123, 777) == (
        "Chat ID для алертов: <code>-100123</code>\nThread ID для этой темы: <code>777</code>"
    )


def test_my_id_message_contains_user_id() -> None:
    assert my_id_message(42) == "Ваш Telegram user id: <code>42</code>"


def test_stats_message_contains_user_stats() -> None:
    message = stats_message(
        UserStats(
            total_users=10,
            active_today=3,
            active_7_days=7,
            new_today=2,
            inline_queries=100,
            successful_requests=80,
            failed_requests=5,
            cached_videos=40,
            errors_24h=1,
            errors_7d=9,
        )
    )

    assert "Статистика Wivio" in message
    assert "Всего пользователей: <b>10</b>" in message
    assert "Ошибок загрузки за 24 часа: <b>1</b>" in message
    assert "Ошибок загрузки за 7 дней: <b>9</b>" in message


def test_video_file_id_message_contains_env_name() -> None:
    message = video_file_id_message("telegram-file-id")

    assert "WELCOME_VIDEO_FILE_ID" in message
    assert "<code>telegram-file-id</code>" in message


def test_welcome_file_id_message_can_use_animation_env_name() -> None:
    message = welcome_file_id_message("animation-file-id", "WELCOME_ANIMATION_FILE_ID")

    assert "WELCOME_ANIMATION_FILE_ID" in message
    assert "<code>animation-file-id</code>" in message


def test_welcome_forward_message_config_contains_env_values() -> None:
    message = welcome_forward_message_config(-100123, 456)

    assert "<code>WELCOME_FORWARD_CHAT_ID=-100123</code>" in message
    assert "<code>WELCOME_FORWARD_MESSAGE_ID=456</code>" in message


def test_video_file_id_message_explains_missing_video() -> None:
    assert video_file_id_message(None) == (
        "Отправьте видео или animation боту и ответьте на него командой /fileid."
    )
