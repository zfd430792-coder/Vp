"""Входящие лайки — открыты бесплатно, без «покажем за подписку»."""
from __future__ import annotations

from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import texts, views
from app.callbacks import LikesCB
from app.constants import ACT_LIKE, ACT_PASS
from app.db import Database
from app.handlers import ui
from app.handlers.feed import celebrate_match
from app.keyboards import inline
from app.services import likes as likes_service
from app.services import notify, render
from app.services import profiles as profiles_service

router = Router(name="likes")


async def show_next_like(
    bot: Bot,
    db: Database,
    state: FSMContext,
    chat_id: int,
    user: dict[str, Any],
) -> None:
    await ui.clear_ui(bot, state, chat_id)
    user_id = int(user["id"])

    candidate = await likes_service.next_incoming(db, user_id)
    if candidate is None:
        data = await state.get_data()
        # Если только что разбирали лайки — это «всё просмотрено», а не «пусто»
        reviewed = bool(data.get("likes_target"))
        text = texts.LIKES_DONE if reviewed else texts.LIKES_EMPTY
        message = await notify.send_message(bot, db, chat_id, text)
        await ui.remember_ui(state, [message.message_id] if message else [])
        await state.update_data(likes_target=0)
        return

    target_id = int(candidate["user_id"])
    candidate["photos"] = await profiles_service.photos(db, target_id)
    remaining = await likes_service.incoming_count(db, user_id)

    footer = None
    if candidate.get("like_message"):
        footer = f"💬 <i>{views.esc(candidate['like_message'])}</i>"

    message_ids = await render.send_card(
        bot,
        db,
        chat_id,
        candidate,
        keyboard=inline.incoming_like(target_id),
        header=views.incoming_like_header(candidate)
        + (f"\n<i>Ещё входящих: {remaining - 1}</i>" if remaining > 1 else ""),
        footer=footer,
    )
    await ui.remember_ui(state, message_ids)
    await state.update_data(likes_target=target_id)
    await likes_service.mark_seen(db, target_id, user_id)


@router.message(StateFilter(None), F.text.startswith(texts.BTN_LIKES))
@router.message(Command("likes"))
async def open_likes(
    message: Message,
    bot: Bot,
    state: FSMContext,
    db: Database,
    user: dict[str, Any],
) -> None:
    await ui.leave_chat_mode(db, state, int(user["id"]))
    profile = await ui.require_profile(bot, db, message.chat.id, user)
    if not profile:
        return
    count = await likes_service.incoming_count(db, int(user["id"]))
    if count:
        await message.answer(texts.LIKES_HEADER.format(count=count))
    await show_next_like(bot, db, state, message.chat.id, user)


@router.callback_query(LikesCB.filter(F.action == "open"))
async def open_likes_inline(
    query: CallbackQuery,
    bot: Bot,
    state: FSMContext,
    db: Database,
    user: dict[str, Any],
) -> None:
    await query.answer()
    if query.message is None:
        return
    await show_next_like(bot, db, state, query.message.chat.id, user)


@router.callback_query(LikesCB.filter(F.action.in_({"like", "pass"})))
async def answer_like(
    query: CallbackQuery,
    callback_data: LikesCB,
    bot: Bot,
    state: FSMContext,
    db: Database,
    user: dict[str, Any],
) -> None:
    if query.message is None:
        await query.answer()
        return

    user_id = int(user["id"])
    target_id = int(callback_data.target)
    if target_id == user_id:
        await query.answer()
        return

    await ui.leave_chat_mode(db, state, user_id)

    action = ACT_LIKE if callback_data.action == "like" else ACT_PASS
    result = await likes_service.act(db, user_id, target_id, action, source="inbox")

    if action == ACT_PASS:
        await query.answer(texts.PASS_DONE)
    else:
        await query.answer("❤️ Взаимно!" if result.matched else texts.LIKE_SENT)

    if result.matched and result.created_match and result.match_id:
        await celebrate_match(bot, db, user_id, target_id, result.match_id)

    await show_next_like(bot, db, state, query.message.chat.id, user)
