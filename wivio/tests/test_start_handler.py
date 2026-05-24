from bot.database.models import UserStats
from bot.handlers.start import (
    chat_id_message,
    my_id_message,
    start_keyboard,
    start_message,
    stats_message,
)


def test_start_message_explains_inline_usage() -> None:
    message = start_message("@wivio_bot")

    assert "<b>Привет! Я Wivio.</b>" in message
    assert "<code>@wivio_bot</code>" in message
    assert "TikTok" in message
    assert "YouTube Shorts" in message
    assert "первый раз видео может готовиться несколько секунд" in message


def test_start_keyboard_opens_inline_mode_in_current_chat() -> None:
    keyboard = start_keyboard()
    button = keyboard.inline_keyboard[0][0]

    assert button.text == "Попробовать в этом чате"
    assert button.switch_inline_query_current_chat == ""


def test_chat_id_message_contains_chat_id() -> None:
    assert chat_id_message(-100123) == "Chat ID для алертов: <code>-100123</code>"


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
        )
    )

    assert "Статистика Wivio" in message
    assert "Всего пользователей: <b>10</b>" in message
    assert "Ошибок загрузки за 24 часа: <b>1</b>" in message
