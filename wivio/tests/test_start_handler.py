from bot.handlers.start import start_keyboard, start_message


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
