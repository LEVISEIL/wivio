from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)

router = Router(name="start")


async def handle_start(message: Message, bot_username: str) -> None:
    logger.info("Start command user_id=%s", message.from_user.id if message.from_user else None)
    await message.answer(
        text=start_message(bot_username),
        reply_markup=start_keyboard(),
        disable_web_page_preview=True,
    )


def start_message(bot_username: str) -> str:
    username = bot_username.lstrip("@")
    return (
        "<b>Привет! Я Wivio.</b>\n\n"
        "Я помогаю отправлять видео из TikTok, Instagram Reels, Instagram posts "
        "и YouTube Shorts прямо через inline-режим Telegram.\n\n"
        "<b>Как пользоваться:</b>\n"
        "1. Открой любой чат.\n"
        f"2. Напиши <code>@{username}</code> и вставь ссылку на видео.\n"
        "3. Дождись обработки: первый раз видео может готовиться несколько секунд.\n"
        "4. Когда появится видео, нажми на него, и Telegram отправит его в чат.\n\n"
        "Если видео уже было загружено раньше, оно появится сразу."
    )


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Попробовать в этом чате",
                    switch_inline_query_current_chat="",
                )
            ]
        ]
    )


router.message(CommandStart())(handle_start)
