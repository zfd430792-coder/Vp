"""Отправка карточек анкет в чат."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InputMediaPhoto

from app import views
from app.db import Database
from app.services import notify

CAPTION_LIMIT = 1000


async def send_card(
    bot: Bot,
    db: Database | None,
    chat_id: int,
    card: dict[str, Any],
    *,
    keyboard: InlineKeyboardMarkup | None = None,
    header: str | None = None,
    footer: str | None = None,
    show_activity: bool = True,
) -> list[int]:
    """Показывает анкету. Возвращает id отправленных сообщений."""
    photos: Sequence[dict[str, Any]] = card.get("photos") or []

    def build(bio_limit: int) -> str:
        return views.profile_caption(
            card,
            header=header,
            footer=footer,
            show_activity=show_activity,
            bio_limit=bio_limit,
        )

    caption = build(600)
    message_ids: list[int] = []

    if not photos:
        message = await notify.send_message(bot, db, chat_id, caption, reply_markup=keyboard)
        return [message.message_id] if message else []

    if len(photos) == 1:
        # Подпись к фото ограничена 1024 символами: укорачиваем описание,
        # а не готовый текст, иначе можно разорвать HTML-тег
        photo_caption = caption
        for limit in (400, 200, 0):
            if len(photo_caption) <= CAPTION_LIMIT:
                break
            photo_caption = build(limit)
        message = await notify.send_photo(
            bot,
            db,
            chat_id,
            str(photos[0]["file_id"]),
            caption=photo_caption,
            reply_markup=keyboard,
        )
        return [message.message_id] if message else []

    media = [InputMediaPhoto(media=str(photo["file_id"])) for photo in photos[:10]]
    album = await notify.send_media_group(bot, db, chat_id, media)
    if album:
        message_ids.extend(message.message_id for message in album)
    message = await notify.send_message(bot, db, chat_id, caption, reply_markup=keyboard)
    if message:
        message_ids.append(message.message_id)
    return message_ids
