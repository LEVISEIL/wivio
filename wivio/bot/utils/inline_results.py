from __future__ import annotations

from aiogram.types import (
    InlineQueryResultArticle,
    InlineQueryResultCachedVideo,
    InputTextMessageContent,
)

DEFAULT_THUMB = "https://telegram.org/img/t_logo.png"


def cached_video_result(
    result_id: str,
    file_id: str,
    title: str,
    caption: str,
    description: str,
) -> InlineQueryResultCachedVideo:
    return InlineQueryResultCachedVideo(
        id=result_id,
        video_file_id=file_id,
        title=title[:64] or "Video",
        description=description[:128],
        caption=caption[:1024],
        parse_mode="HTML",
    )


def article_result(
    result_id: str,
    title: str,
    message: str,
    description: str,
    thumbnail_url: str | None = None,
) -> InlineQueryResultArticle:
    return InlineQueryResultArticle(
        id=result_id,
        title=title,
        description=description,
        thumbnail_url=thumbnail_url or DEFAULT_THUMB,
        input_message_content=InputTextMessageContent(
            message_text=message,
            parse_mode="HTML",
            disable_web_page_preview=True,
        ),
    )
