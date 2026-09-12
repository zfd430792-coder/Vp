"""Безопасная отправка сообщений: Telegram часто отвечает ошибками, и это нормально."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import Message

from app.db import Database
from app.services import users as users_service

log = logging.getLogger(__name__)


async def safe_call(
    db: Database | None,
    chat_id: int | None,
    func: Callable[..., Awaitable[Any]],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Вызывает метод Bot API, переживая типовые ошибки.

    Возвращает результат либо None, если доставить сообщение не удалось.
    """
    for attempt in range(2):
        try:
            return await func(*args, **kwargs)
        except TelegramRetryAfter as error:
            delay = min(int(error.retry_after) + 1, 30)
            log.warning("Лимит Telegram, жду %s с (chat_id=%s)", delay, chat_id)
            await asyncio.sleep(delay)
        except TelegramForbiddenError:
            # Пользователь заблокировал бота или удалил аккаунт
            if db is not None and chat_id is not None and chat_id > 0:
                await users_service.mark_bot_blocked(db, chat_id)
            return None
        except TelegramBadRequest as error:
            log.warning("Bad request для chat_id=%s: %s", chat_id, error)
            return None
        except TelegramNetworkError as error:
            log.warning("Сеть недоступна (попытка %s): %s", attempt + 1, error)
            await asyncio.sleep(1)
    return None


async def send_message(bot: Bot, db: Database | None, chat_id: int, text: str, **kwargs: Any):
    return await safe_call(db, chat_id, bot.send_message, chat_id, text, **kwargs)


async def send_photo(bot: Bot, db: Database | None, chat_id: int, photo: str, **kwargs: Any):
    return await safe_call(db, chat_id, bot.send_photo, chat_id, photo, **kwargs)


async def send_media_group(bot: Bot, db: Database | None, chat_id: int, media: list, **kwargs: Any):
    return await safe_call(db, chat_id, bot.send_media_group, chat_id, media, **kwargs)


async def copy_message(
    bot: Bot, db: Database | None, chat_id: int, from_chat_id: int, message_id: int, **kwargs: Any
):
    return await safe_call(
        db, chat_id, bot.copy_message, chat_id, from_chat_id, message_id, **kwargs
    )


async def delete_messages(bot: Bot, chat_id: int, message_ids: Iterable[int]) -> None:
    """Удаляет сообщения, молча игнорируя уже удалённые."""
    ids = [int(mid) for mid in message_ids if mid]
    if not ids:
        return
    try:
        await bot.delete_messages(chat_id, ids)
        return
    except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError):
        pass
    for message_id in ids:
        try:
            await bot.delete_message(chat_id, message_id)
        except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError):
            continue


async def cleanup_messages(bot: Bot, chat_id: int, messages: Sequence[Message | None]) -> None:
    await delete_messages(bot, chat_id, [m.message_id for m in messages if m is not None])


async def notify_staff(
    bot: Bot,
    db: Database,
    text: str,
    *,
    chat_id: int | None = None,
    reply_markup: Any = None,
    min_role: int = 1,
) -> None:
    """Сообщает модераторам: в отдельный чат, если он настроен, иначе каждому лично."""
    if chat_id:
        await send_message(bot, None, chat_id, text, reply_markup=reply_markup)
        return
    rows = await db.fetchall(
        "SELECT id FROM users WHERE role >= ? AND bot_blocked = 0", (min_role,)
    )
    for row in rows:
        await send_message(bot, db, int(row["id"]), text, reply_markup=reply_markup)
        await asyncio.sleep(0.05)
