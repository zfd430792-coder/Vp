"""Жалобы: приём, авто-приоритеты и уведомление модераторов."""
from __future__ import annotations

from typing import Any

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import texts
from app.callbacks import FeedCB, LikesCB, MatchCB, ReportCB
from app.config import Config
from app.constants import REPORT_CATEGORIES
from app.db import Database
from app.handlers import ui
from app.handlers.likes import show_next_like
from app.keyboards import inline
from app.services import likes as likes_service
from app.services import moderation, notify
from app.services import profiles as profiles_service
from app.services import users as users_service
from app.services.settings import Settings
from app.states import Report
from app.utils.text import esc, shorten
from app.utils.time import now

router = Router(name="reports")


async def start_report(
    bot: Bot,
    db: Database,
    state: FSMContext,
    *,
    chat_id: int,
    reporter_id: int,
    target_id: int,
    context: str = "feed",
    match_id: int = 0,
) -> None:
    if target_id == reporter_id:
        await notify.send_message(bot, db, chat_id, texts.REPORT_SELF)
        return

    allowed, reason = await moderation.can_report(db, reporter_id)
    if not allowed:
        await notify.send_message(bot, db, chat_id, texts.REPORT_LIMIT)
        return

    if await moderation.has_open_report(db, reporter_id, target_id):
        await notify.send_message(bot, db, chat_id, texts.REPORT_DUPLICATE)
        return

    await state.update_data(
        report_target=target_id, report_ctx=context, report_match=match_id
    )
    await notify.send_message(
        bot,
        db,
        chat_id,
        texts.REPORT_ASK_CATEGORY,
        reply_markup=inline.report_categories(target_id),
    )


@router.callback_query(FeedCB.filter(F.action == "report"))
async def report_from_feed(
    query: CallbackQuery,
    callback_data: FeedCB,
    bot: Bot,
    state: FSMContext,
    db: Database,
    user: dict[str, Any],
) -> None:
    await query.answer()
    if query.message is None:
        return
    await start_report(
        bot,
        db,
        state,
        chat_id=query.message.chat.id,
        reporter_id=int(user["id"]),
        target_id=int(callback_data.target),
        context="feed",
    )


@router.callback_query(LikesCB.filter(F.action == "report"))
async def report_from_likes(
    query: CallbackQuery,
    callback_data: LikesCB,
    bot: Bot,
    state: FSMContext,
    db: Database,
    user: dict[str, Any],
) -> None:
    await query.answer()
    if query.message is None:
        return
    await start_report(
        bot,
        db,
        state,
        chat_id=query.message.chat.id,
        reporter_id=int(user["id"]),
        target_id=int(callback_data.target),
        context="likes",
    )


@router.callback_query(MatchCB.filter(F.action == "report"))
async def report_from_chat(
    query: CallbackQuery,
    callback_data: MatchCB,
    bot: Bot,
    state: FSMContext,
    db: Database,
    user: dict[str, Any],
) -> None:
    await query.answer()
    if query.message is None:
        return
    user_id = int(user["id"])
    match = await likes_service.match_by_id(db, int(callback_data.match_id))
    if not match or user_id not in (int(match["user_a"]), int(match["user_b"])):
        await notify.send_message(bot, db, query.message.chat.id, "Диалог не найден.")
        return
    await start_report(
        bot,
        db,
        state,
        chat_id=query.message.chat.id,
        reporter_id=user_id,
        target_id=likes_service.partner_id(match, user_id),
        context="chat",
        match_id=int(match["id"]),
    )


@router.callback_query(ReportCB.filter(F.action == "cat"))
async def choose_category(
    query: CallbackQuery, callback_data: ReportCB, state: FSMContext
) -> None:
    if callback_data.value not in REPORT_CATEGORIES:
        await query.answer()
        return
    await state.set_state(Report.comment)
    await state.update_data(report_category=callback_data.value)
    await query.answer()
    if query.message:
        await query.message.edit_text(
            f"{texts.REPORT_ASK_COMMENT}\n\nКатегория: <b>{REPORT_CATEGORIES[callback_data.value]}</b>",
            reply_markup=inline.report_comment(callback_data.target, callback_data.value),
        )


@router.callback_query(ReportCB.filter(F.action == "cancel"))
async def cancel_report(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    await state.update_data(report_target=0, report_category="")
    await query.answer("Отменено")
    if query.message:
        await query.message.edit_text("Жалоба отменена.")


@router.callback_query(ReportCB.filter(F.action == "send"))
async def send_without_comment(
    query: CallbackQuery,
    callback_data: ReportCB,
    bot: Bot,
    state: FSMContext,
    db: Database,
    settings: Settings,
    config: Config,
    user: dict[str, Any],
) -> None:
    await query.answer()
    if query.message is None:
        return
    if query.message.text:
        await query.message.edit_reply_markup(reply_markup=None)
    await _submit(
        bot, db, settings, config, state, query.message.chat.id, user, comment=None
    )


@router.message(Report.comment, F.text)
async def send_with_comment(
    message: Message,
    bot: Bot,
    state: FSMContext,
    db: Database,
    settings: Settings,
    config: Config,
    user: dict[str, Any],
) -> None:
    await _submit(
        bot,
        db,
        settings,
        config,
        state,
        message.chat.id,
        user,
        comment=shorten(message.text or "", 800),
    )


@router.message(Report.comment)
async def comment_wrong_type(message: Message) -> None:
    await message.answer("Опиши ситуацию текстом или нажми «Пропустить».")


async def _submit(
    bot: Bot,
    db: Database,
    settings: Settings,
    config: Config,
    state: FSMContext,
    chat_id: int,
    user: dict[str, Any],
    *,
    comment: str | None,
) -> None:
    data = await state.get_data()
    target_id = int(data.get("report_target") or 0)
    category = str(data.get("report_category") or "other")
    context = str(data.get("report_ctx") or "feed")
    match_id = int(data.get("report_match") or 0) or None
    reporter_id = int(user["id"])

    await state.set_state(None)
    await state.update_data(report_target=0, report_category="", report_ctx="", report_match=0)

    if not target_id:
        await notify.send_message(bot, db, chat_id, texts.ERROR_GENERIC)
        return

    report_id = await moderation.create_report(
        db,
        reporter_id=reporter_id,
        target_id=target_id,
        category=category,
        comment=comment,
        context=context,
        match_id=match_id,
    )

    if context in {"feed", "likes"}:
        await db.execute(
            "INSERT INTO blocks (user_id, target_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, target_id) DO NOTHING",
            (reporter_id, target_id, now()),
        )
        tail = texts.REPORT_TAIL_FEED
        markup = None
    else:
        tail = texts.REPORT_TAIL_CHAT
        markup = inline.after_report(target_id)

    await notify.send_message(
        bot,
        db,
        chat_id,
        texts.REPORT_SENT.format(report_id=report_id, tail=tail),
        reply_markup=markup,
    )

    action = await moderation.auto_moderate(db, settings, target_id)
    await users_service.recompute_trust(db, target_id)
    if action:
        await _notify_target(bot, db, target_id, action)

    # Возвращаем человека туда, откуда он пожаловался, чтобы не терять поток
    if context == "feed":
        profile = await profiles_service.get(db, reporter_id)
        if profile and profile.get("is_complete"):
            await ui.show_next_profile(
                bot, db, settings, state, chat_id=chat_id, user=user, profile=profile
            )
    elif context == "likes":
        await show_next_like(bot, db, state, chat_id, user)

    unique, weight = await moderation.report_pressure(db, target_id)
    await notify.notify_staff(
        bot,
        db,
        f"🚩 <b>Новая жалоба №{report_id}</b>\n"
        f"Категория: {REPORT_CATEGORIES.get(category, category)}\n"
        f"На пользователя: <code>{target_id}</code>\n"
        f"Жалобщиков за 30 дней: <b>{unique}</b> (вес {weight})\n"
        + (f"Автоматика: {esc(action.kind)}\n" if action else "")
        + (f"\n💬 <i>{esc(shorten(comment, 200))}</i>" if comment else ""),
        chat_id=config.moderation_chat_id,
        reply_markup=inline.report_notice(report_id),
    )


async def _notify_target(
    bot: Bot, db: Database, target_id: int, action: moderation.AutoAction
) -> None:
    """Честно сообщаем человеку, что именно изменилось и почему."""
    if action.kind == "hold":
        text = (
            "⏸ <b>Анкета временно скрыта из поиска</b>\n\n"
            f"Причина: {esc(action.reason)}.\n\n"
            "Это автоматическая мера, и она обратима: анкету уже смотрит модератор. "
            "Диалоги и симпатии продолжают работать.\n\n"
            "Если считаешь, что жалобы несправедливы — напиши апелляцию, её прочитает человек."
        )
    elif action.kind == "shadow":
        from app.utils.time import fmt_dt
        from app.utils.time import now as _now

        until = _now() + action.shadow_hours * 3600
        text = texts.SHADOW_NOTICE.format(
            reason=esc(action.reason),
            until=fmt_dt(until),
            left=f"{action.shadow_hours} ч",
        )
        await db.execute("UPDATE users SET shadow_notified = 1 WHERE id = ?", (target_id,))
    else:
        return

    await notify.send_message(bot, db, target_id, text, reply_markup=inline.appeal_button())


@router.callback_query(ReportCB.filter(F.action == "block"))
async def block_after_report(
    query: CallbackQuery,
    callback_data: ReportCB,
    bot: Bot,
    state: FSMContext,
    db: Database,
    user: dict[str, Any],
) -> None:
    from app.services import chat as chat_service

    user_id = int(user["id"])
    target_id = int(callback_data.target)
    await likes_service.block_user(db, user_id, target_id)
    chat_service.close_chat(user_id)
    await state.set_state(None)
    await query.answer("Заблокировано")
    if query.message:
        await query.message.edit_reply_markup(reply_markup=None)
    await ui.show_menu(
        bot,
        db,
        query.message.chat.id if query.message else user_id,
        user,
        "⛔️ Пользователь заблокирован. Диалог закрыт, анкеты друг друга вы больше не увидите.",
    )
