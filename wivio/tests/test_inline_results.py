from bot.utils.inline_results import article_result, cached_video_result


def test_cached_video_result_truncates_telegram_limited_fields() -> None:
    result = cached_video_result(
        result_id="id",
        file_id="file-id",
        title="T" * 100,
        caption="C" * 2000,
        description="D" * 200,
    )

    assert result.id == "id"
    assert result.video_file_id == "file-id"
    assert result.title == "T" * 64
    assert result.caption == "C" * 1024
    assert result.description == "D" * 128
    assert result.parse_mode == "HTML"


def test_article_result_contains_input_message_content() -> None:
    result = article_result(
        result_id="id",
        title="Title",
        message="Message",
        description="Description",
    )

    assert result.id == "id"
    assert result.title == "Title"
    assert result.input_message_content.message_text == "Message"
    assert result.input_message_content.disable_web_page_preview is True
