"""Взаимные симпатии и вход в диалог."""
from __future__ import annotations

from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import texts, views
from app.callbacks import MatchCB
from app.db import Database
from app.handlers import ui
from app.keyboards import inline, reply
from app.services import chat as chat_service
from app.services import likes as likes_service
from app.services import notify
from app.services import profiles as profiles_service
from app.services import users as users_service
from app.utils.keyboard_utils import page_bounds

router = Router(name="matches")

PER_PAGE = 8


async def show_matches(
    bot: Bot,
    db: Database,
    chat_id: int,
    user: dict[str, Any],
    *,
    page: int = 0,
    message: Message | None = None,
) -> None:
    user_id = int(user["id"])
    total = await likes_service.count_matches(db, user_id)
    if not total:
        text = texts.MATCHES_EMPTY
        if message is not None:
            await message.edit_text(text)
        else:
            await notify.send_message(bot, db, chat_id, text)
        return

    page, pages, offset = page_bounds(total, page, PER_PAGE)
    rows = await likes_service.list_matches(db, user_id, limit=PER_PAGE, offset=offset)
    unread = sum(int(row.get("unread") or 0) for row in rows)

    header = f"💞 <b>Взаимные симпатии: {total}</b>"
    if unread:
        header += f"\n🔴 Непрочитанных сообщений: {unread}"
    header += "\n\nВыбери, кому написать. Общение идёт внутри бота — номер телефона не раскрывается."

    markup = inline.matches_list(rows, page=page, pages=pages)
    if message is not None:
        await message.edit_text(header, reply_markup=markup)
    else:
        await notify.send_message(bot, db, chat_id, header, reply_markup=markup)


@router.message(StateFilter(None), F.text.startswith(texts.BTN_MATCHES))
@router.message(Command("matches"))
async def open_matches(
    message: Message, bot: Bot, state: FSMContext, db: Database, user: dict[str, Any]
) -> None:
    await ui.leave_chat_mode(db, state, int(user["id"]))
    profile = await ui.require_profile(bot, db, message.chat.id, user)
    if not profile:
        return
    await ui.clear_ui(bot, state, message.chat.id)
    await show_matches(bot, db, message.chat.id, user)


@router.callback_query(MatchCB.filter(F.action == "list"))
async def page_matches(
    query: CallbackQuery,
    callback_data: MatchCB,
    bot: Bot,
    db: Database,
    user: dict[str, Any],
) -> None:
    await query.answer()
    if query.message is None:
        return
    await show_matches(
        bot, db, query.message.chat.id, user, page=callback_data.page, message=query.message
    )


@router.callback_query(MatchCB.filter(F.action == "noop"))
async def noop(query: CallbackQuery) -> None:
    await query.answer()


@router.callback_query(MatchCB.filter(F.action == "open"))
async def open_chat(
    query: CallbackQuery,
    callback_data: MatchCB,
    bot: Bot,
    state: FSMContext,
    db: Database,
    user: dict[str, Any],
) -> None:
    if query.message is None:
        await query.answer()
        return

    user_id = int(user["id"])
    match = await likes_service.match_by_id(db, int(callback_data.match_id))
    if not match or user_id not in (int(match["user_a"]), int(match["user_b"])):
        await query.answer("Диалог не найден", show_alert=True)
        return
    if not match.get("active"):
        await query.answer("Этот диалог закрыт", show_alert=True)
        return

    partner_id = likes_service.partner_id(match, user_id)
    partner = await users_service.get(db, partner_id)
    partner_profile = await profiles_service.get(db, partner_id)
    if not partner or not partner_profile or users_service.is_banned(partner):
        await query.answer()
        await notify.send_message(bot, db, query.message.chat.id, texts.CHAT_PARTNER_GONE)
        return

    await query.answer()
    match_id = int(match["id"])
    await chat_service.open_chat(db, user_id, match_id)
    chat_service.reset_header(user_id, match_id)
    await chat_service.mark_read(db, match_id, user_id)
    await state.set_state(None)

    history = await chat_service.history(db, match_id, limit=6)
    await notify.send_message(
        bot,
        db,
        query.message.chat.id,
        texts.CHAT_OPENED.format(
            name=views.esc(partner_profile.get("name") or "собеседник"),
            exit_btn=texts.BTN_CHAT_EXIT,
        ),
        reply_markup=reply.chat_menu(),
    )
    if history:
        names = {
            user_id: "Ты",
            partner_id: str(partner_profile.get("name") or "Собеседник"),
        }
        await notify.send_message(
            bot,
            db,
            query.message.chat.id,
            views.chat_history_text(history, names),
            reply_markup=inline.chat_actions(match_id, partner_id),
        )
    else:
        await notify.send_message(
            bot,
            db,
            query.message.chat.id,
            "💡 Начни с вопроса по анкете — так отвечают гораздо охотнее, чем на «привет».",
            reply_markup=inline.chat_actions(match_id, partner_id),
        )


@router.callback_query(MatchCB.filter(F.action == "block"))
async def block_partner(
    query: CallbackQuery,
    callback_data: MatchCB,
    bot: Bot,
    state: FSMContext,
    db: Database,
    user: dict[str, Any],
) -> None:
    if query.message is None:
        await query.answer()
        return
    user_id = int(user["id"])
    match = await likes_service.match_by_id(db, int(callback_data.match_id))
    if not match or user_id not in (int(match["user_a"]), int(match["user_b"])):
        await query.answer("Диалог не найден", show_alert=True)
        return

    partner_id = likes_service.partner_id(match, user_id)
    await likes_service.block_user(db, user_id, partner_id)
    await ui.leave_chat_mode(db, state, user_id)
    await query.answer("Пользователь заблокирован")
    await notify.send_message(
        bot,
        db,
        query.message.chat.id,
        "⛔️ <b>Готово.</b> Вы больше не увидите анкеты друг друга, диалог закрыт.\n\n"
        "Если человек нарушал правила — отправь ещё и жалобу, мы разберёмся.",
        reply_markup=await ui.main_menu_markup(db, user),
    )
