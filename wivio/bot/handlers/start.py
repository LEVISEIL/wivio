from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.database.models import UserStats
from bot.database.repositories import UserRepository

logger = logging.getLogger(__name__)

router = Router(name="start")


async def handle_start(message: Message, bot_username: str, users: UserRepository) -> None:
    logger.info("Start command user_id=%s", message.from_user.id if message.from_user else None)
    if message.from_user is not None:
        await users.touch(message.from_user, "start")
    await message.answer(
        text=start_message(bot_username),
        reply_markup=start_keyboard(),
        disable_web_page_preview=True,
    )


async def handle_chat_id(message: Message) -> None:
    logger.info(
        "Chat id requested user_id=%s chat_id=%s thread_id=%s",
        message.from_user.id if message.from_user else None,
        message.chat.id,
        message.message_thread_id,
    )
    await message.answer(chat_id_message(message.chat.id, message.message_thread_id))


async def handle_my_id(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    logger.info("User id requested user_id=%s", user_id)
    await message.answer(my_id_message(user_id))


async def handle_stats(
    message: Message,
    users: UserRepository,
    admin_user_ids: frozenset[int],
) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id not in admin_user_ids:
        logger.warning("Stats command denied user_id=%s", user_id)
        await message.answer("Команда доступна только администратору.")
        return

    logger.info("Stats command user_id=%s", user_id)
    await message.answer(stats_message(await users.stats()))


def start_message(bot_username: str) -> str:
    username = bot_username.lstrip("@")
    return (
        "<b>Привет! Я Wivio.</b>\n\n"
        "Я помогаю отправлять видео из TikTok, Instagram Reels, Instagram posts "
        "и YouTube Shorts прямо через inline-режим Telegram.\n\n"
        "<b>Как пользоваться:</b>\n"
        "1. Открой любой чат.\n"
        f"2. Напиши <code>@{username}</code> и вставь ссылку на видео.\n"
        "3. Дождись обработки: обычно это занимает до 5 секунд\n"
        "4. Когда появится видео, нажми на него, и Telegram отправит его в чат.\n\n"
        "Если видео уже было загружено раньше, оно появится сразу."
    )


def chat_id_message(chat_id: int, message_thread_id: int | None = None) -> str:
    message = f"Chat ID для алертов: <code>{chat_id}</code>"
    if message_thread_id is not None:
        message += f"\nThread ID для этой темы: <code>{message_thread_id}</code>"
    return message


def my_id_message(user_id: int | None) -> str:
    if user_id is None:
        return "Не удалось определить ваш Telegram user id."
    return f"Ваш Telegram user id: <code>{user_id}</code>"


def stats_message(stats: UserStats) -> str:
    return (
        "<b>Статистика Wivio</b>\n\n"
        f"Всего пользователей: <b>{stats.total_users}</b>\n"
        f"Активных за 24 часа: <b>{stats.active_today}</b>\n"
        f"Активных за 7 дней: <b>{stats.active_7_days}</b>\n"
        f"Новых за 24 часа: <b>{stats.new_today}</b>\n\n"
        f"Inline-запросов всего: <b>{stats.inline_queries}</b>\n"
        f"Успешных выдач видео: <b>{stats.successful_requests}</b>\n"
        f"Ошибок у пользователей: <b>{stats.failed_requests}</b>\n\n"
        f"Видео в кэше: <b>{stats.cached_videos}</b>\n"
        f"Ошибок загрузки за 24 часа: <b>{stats.errors_24h}</b>"
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
router.message(Command("chatid"))(handle_chat_id)
router.message(Command("myid"))(handle_my_id)
router.message(Command("stats"))(handle_stats)
