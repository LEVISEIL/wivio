from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.database.models import UserStats
from bot.database.repositories import UserRepository
from bot.utils.urls import UnsupportedUrlError, extract_first_url, parse_video_url

logger = logging.getLogger(__name__)

router = Router(name="start")


async def handle_start(
    message: Message,
    bot_username: str,
    users: UserRepository,
    welcome_forward_chat_id: int | None = None,
    welcome_forward_message_id: int | None = None,
    welcome_animation_url: str = "",
    welcome_animation_path: Path | None = None,
    welcome_animation_file_id: str = "",
    welcome_video_file_id: str = "",
) -> None:
    logger.info("Start command user_id=%s", message.from_user.id if message.from_user else None)
    if message.from_user is not None:
        await users.touch(message.from_user, "start")

    text = start_message(bot_username)
    keyboard = start_keyboard()
    if await forward_welcome_message(
        message=message,
        from_chat_id=welcome_forward_chat_id,
        message_id=welcome_forward_message_id,
    ):
        return

    if await send_welcome_media(
        message=message,
        text=text,
        keyboard=keyboard,
        animation_url=welcome_animation_url,
        animation_path=welcome_animation_path,
        animation_file_id=welcome_animation_file_id,
        video_file_id=welcome_video_file_id,
    ):
        return

    await message.answer(
        text=text,
        reply_markup=keyboard,
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


async def handle_file_id(
    message: Message,
    admin_user_ids: frozenset[int],
) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id not in admin_user_ids:
        logger.warning("File id command denied user_id=%s", user_id)
        await message.answer("Команда доступна только администратору.")
        return

    target = message.reply_to_message or message
    forward_config = ""
    if message.reply_to_message is not None:
        forward_config = welcome_forward_message_config(target.chat.id, target.message_id)

    animation = target.animation if target else None
    if animation is not None:
        logger.info(
            "Animation file id requested user_id=%s file_unique_id=%s",
            user_id,
            animation.file_unique_id,
        )
        await message.answer(
            "\n\n".join(
                item
                for item in [
                    welcome_file_id_message(animation.file_id, "WELCOME_ANIMATION_FILE_ID"),
                    forward_config,
                ]
                if item
            )
        )
        return

    video = target.video if target else None
    if video is None:
        await message.answer(forward_config or welcome_file_id_message(None))
        return

    logger.info(
        "Video file id requested user_id=%s file_unique_id=%s",
        user_id,
        video.file_unique_id,
    )
    await message.answer(
        "\n\n".join(
            item
            for item in [
                welcome_file_id_message(video.file_id, "WELCOME_VIDEO_FILE_ID"),
                forward_config,
            ]
            if item
        )
    )


async def handle_private_fallback(
    message: Message,
    bot_username: str,
) -> None:
    user_id = message.from_user.id if message.from_user else None
    logger.info("Private fallback message user_id=%s chat_id=%s", user_id, message.chat.id)

    await message.answer(
        private_fallback_message(bot_username, message.text or message.caption or ""),
        disable_web_page_preview=True,
    )


async def forward_welcome_message(
    message: Message,
    from_chat_id: int | None,
    message_id: int | None,
) -> bool:
    if from_chat_id is None or message_id is None:
        return False

    try:
        await message.bot.forward_message(
            chat_id=message.chat.id,
            from_chat_id=from_chat_id,
            message_id=message_id,
        )
        return True
    except TelegramAPIError:
        logger.warning(
            "Could not forward welcome message user_id=%s from_chat_id=%s message_id=%s",
            message.from_user.id if message.from_user else None,
            from_chat_id,
            message_id,
            exc_info=True,
        )
        return False


async def send_welcome_media(
    message: Message,
    text: str,
    keyboard: InlineKeyboardMarkup,
    animation_url: str = "",
    animation_path: Path | None = None,
    animation_file_id: str = "",
    video_file_id: str = "",
) -> bool:
    user_id = message.from_user.id if message.from_user else None
    if animation_file_id:
        try:
            await message.answer_animation(
                animation=animation_file_id,
                caption=text,
                reply_markup=keyboard,
            )
            return True
        except TelegramAPIError:
            logger.warning("Could not send welcome animation user_id=%s", user_id, exc_info=True)

    if animation_url:
        try:
            await message.answer_animation(
                animation=animation_url,
                caption=text,
                reply_markup=keyboard,
            )
            return True
        except TelegramAPIError:
            logger.warning(
                "Could not send welcome animation url user_id=%s url=%s",
                user_id,
                animation_url,
                exc_info=True,
            )

    if animation_path is not None:
        animation_exists = await asyncio.to_thread(animation_path.exists)
        if not animation_exists:
            logger.warning(
                "Welcome animation path does not exist user_id=%s path=%s",
                user_id,
                animation_path,
            )
        else:
            try:
                await message.answer_animation(
                    animation=FSInputFile(animation_path),
                    caption=text,
                    reply_markup=keyboard,
                )
                return True
            except TelegramAPIError:
                logger.warning(
                    "Could not send welcome animation path user_id=%s path=%s",
                    user_id,
                    animation_path,
                    exc_info=True,
                )

    if video_file_id:
        try:
            await message.answer_video(
                video=video_file_id,
                caption=text,
                reply_markup=keyboard,
                supports_streaming=True,
            )
            return True
        except TelegramAPIError:
            logger.warning("Could not send welcome video user_id=%s", user_id, exc_info=True)

    return False


def start_message(bot_username: str) -> str:
    username = bot_username.lstrip("@")
    return (
        "<b>Привет! Я Wivio.</b>\n\n"
        "Я помогаю отправлять видео из TikTok, Instagram Reels,\n\n"
        "посты с фото из Instagram\n\n"
        "и YouTube Shorts прямо через inline-режим Telegram.\n\n"
        "<b>Как пользоваться:</b>\n"
        "1. Открой любой чат.\n"
        f"2. Напиши <code>@{username}</code> и вставь ссылку на видео.\n"
        "3. Дождись обработки: обычно это занимает до 5 секунд\n"
        "4. Когда появится видео, нажми на него, и Telegram отправит его в чат.\n\n"
    )


def private_fallback_message(bot_username: str, text: str) -> str:
    username = bot_username.lstrip("@")
    try:
        parsed = parse_video_url(text)
    except UnsupportedUrlError:
        if extract_first_url(text):
            return invalid_link_message(username)
        return usage_hint_message(username)

    return (
        "Чтобы отправить это видео, открой нужный чат и напиши:\n\n"
        f"<code>@{username} {parsed.original_url}</code>\n\n"
        "Потом дождись, когда появится видео, нажми на него, и Telegram отправит его в чат."
    )


def usage_hint_message(username: str) -> str:
    return (
        "Чтобы отправить видео:\n\n"
        "1. Открой чат, куда хочешь его отправить.\n"
        f"2. Напиши <code>@{username}</code> и вставь ссылку на видео.\n"
        "3. Дождись, когда появится видео, и нажми на него.\n\n"
        "Поддерживаются TikTok, Instagram Reels, посты с фото и YouTube Shorts."
    )


def invalid_link_message(username: str) -> str:
    return (
        "<b>Некорректная ссылка.</b>\n\n"
        "Проверь, что ссылка открывается и ведёт на видео из TikTok, "
        "Instagram Reels, Instagram-пост или YouTube Shorts.\n\n"
        "Чтобы отправить видео, напиши в нужном чате:\n"
        f"<code>@{username} ссылка</code>"
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
        f"Ошибок загрузки за 24 часа: <b>{stats.errors_24h}</b>\n"
        f"Ошибок загрузки за 7 дней: <b>{stats.errors_7d}</b>"
    )


def welcome_file_id_message(
    file_id: str | None,
    env_name: str = "WELCOME_VIDEO_FILE_ID",
) -> str:
    if not file_id:
        return "Отправьте видео или animation боту и ответьте на него командой /fileid."
    return f"File ID для {env_name}:\n<code>{file_id}</code>"


def welcome_forward_message_config(chat_id: int, message_id: int) -> str:
    return (
        "Чтобы пересылать это сообщение в /start:\n"
        f"<code>WELCOME_FORWARD_CHAT_ID={chat_id}</code>\n"
        f"<code>WELCOME_FORWARD_MESSAGE_ID={message_id}</code>"
    )


def video_file_id_message(file_id: str | None) -> str:
    return welcome_file_id_message(file_id, "WELCOME_VIDEO_FILE_ID")


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Попробовать в этом чате",
                    switch_inline_query_current_chat="",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔗 Наш канал",
                    url="https://t.me/wivio_ch",
                ),
                InlineKeyboardButton(
                    text="🆘 Поддержка",
                    url="https://t.me/ttdarr",
                ),
            ],
        ]
    )


router.message(CommandStart())(handle_start)
router.message(Command("help"))(handle_start)
router.message(Command("chatid"))(handle_chat_id)
router.message(Command("myid"))(handle_my_id)
router.message(Command("stats"))(handle_stats)
router.message(Command("fileid"))(handle_file_id)
router.message(F.chat.type == "private")(handle_private_fallback)
