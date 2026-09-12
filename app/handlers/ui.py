"""Общие элементы интерфейса, которыми пользуются разные хендлеры."""
from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.fsm.context import FSMContext

from app import texts
from app.constants import ROLE_MODERATOR
from app.db import Database
from app.keyboards import inline, reply
from app.services import feed as feed_service
from app.services import geo, insights, notify, render
from app.services import likes as likes_service
from app.services import limits as limits_service
from app.services import profiles as profiles_service
from app.services import users as users_service
from app.services.settings import Settings
from app.utils.text import esc, human_delta
from app.utils.time import fmt_dt, now

UI_MESSAGES_KEY = "ui_msgs"


async def leave_chat_mode(db: Database, state: FSMContext, user_id: int) -> None:
    """Выход в обычный режим: закрывает открытый диалог и сбрасывает состояние."""
    from app.services import chat as chat_service

    await chat_service.close_chat(db, user_id)
    await state.set_state(None)


def attach_distance(card: dict[str, Any], viewer: dict[str, Any] | None) -> None:
    """Дописывает в карточку примерное расстояние, если координаты есть у обоих."""
    if not viewer:
        return
    values = (viewer.get("lat"), viewer.get("lon"), card.get("lat"), card.get("lon"))
    if any(value is None for value in values):
        return
    my_lat, my_lon, their_lat, their_lon = (float(value) for value in values)
    card["distance_text"] = geo.format_distance(
        geo.distance_km(my_lat, my_lon, their_lat, their_lon)
    )


async def clear_ui(bot: Bot, state: FSMContext, chat_id: int) -> None:
    """Удаляет ранее показанные карточки, чтобы чат не превращался в ленту мусора."""
    data = await state.get_data()
    message_ids = data.get(UI_MESSAGES_KEY) or []
    if message_ids:
        await notify.delete_messages(bot, chat_id, message_ids)
        await state.update_data(**{UI_MESSAGES_KEY: []})


async def remember_ui(state: FSMContext, message_ids: list[int]) -> None:
    await state.update_data(**{UI_MESSAGES_KEY: message_ids})


async def main_menu_markup(db: Database, user: dict[str, Any]):
    user_id = int(user["id"])
    incoming = await likes_service.incoming_count(db, user_id)
    unread = await likes_service.total_unread(db, user_id)
    return reply.main_menu(
        is_staff=int(user.get("role") or 0) >= ROLE_MODERATOR,
        likes_badge=incoming,
        unread=unread,
    )


async def show_menu(
    bot: Bot,
    db: Database,
    chat_id: int,
    user: dict[str, Any],
    text: str = texts.MENU,
) -> None:
    markup = await main_menu_markup(db, user)
    await notify.send_message(bot, db, chat_id, text, reply_markup=markup)


async def show_next_profile(
    bot: Bot,
    db: Database,
    settings: Settings,
    state: FSMContext,
    *,
    chat_id: int,
    user: dict[str, Any],
    profile: dict[str, Any],
    notice: str | None = None,
) -> bool:
    """Показывает следующую анкету из ленты. False — анкеты закончились."""
    await clear_ui(bot, state, chat_id)

    candidate = await feed_service.next_candidate(db, settings, user, profile)
    if candidate is None:
        available = await feed_service.count_available(db, settings, user, profile)
        text = texts.feed_empty_text(max(1, settings.get_int("pass_ttl_days", 14)))
        if available:
            text = texts.FEED_EMPTY_SHORT
        message = await notify.send_message(
            bot, db, chat_id, text, reply_markup=inline.feed_empty()
        )
        await remember_ui(state, [message.message_id] if message else [])
        await state.update_data(feed_target=0)
        return False

    target_id = int(candidate["user_id"])
    candidate["photos"] = await profiles_service.photos(db, target_id)
    attach_distance(candidate, profile)
    quota = await limits_service.quota(db, user, settings)

    footer_parts = []
    if notice:
        footer_parts.append(notice)
    if candidate.get("liked_me"):
        footer_parts.append("💌 <i>Ты уже понравился этому человеку</i>")
    footer_parts.append(
        f"<i>❤️ Осталось лайков: {quota.left} из {quota.limit}</i>"
    )

    message_ids = await render.send_card(
        bot,
        db,
        chat_id,
        candidate,
        keyboard=inline.feed(target_id, superlikes_left=quota.superlikes_left),
        footer="\n".join(footer_parts),
    )
    await remember_ui(state, message_ids)
    await state.update_data(feed_target=target_id)
    if message_ids:
        await insights.bump(db, target_id, "shown")
    return True


async def require_profile(
    bot: Bot, db: Database, chat_id: int, user: dict[str, Any]
) -> dict[str, Any] | None:
    """Возвращает заполненную анкету или подсказывает зарегистрироваться."""
    profile = await profiles_service.get(db, int(user["id"]))
    if profile and profile.get("is_complete"):
        return profile
    await notify.send_message(bot, db, chat_id, texts.NOT_REGISTERED)
    return None


async def send_restriction_notice(
    bot: Bot, db: Database, chat_id: int, user: dict[str, Any]
) -> None:
    """Честно предупреждает о снижении охвата — без молчаливых теневых банов."""
    if not users_service.is_shadowed(user):
        return
    if user.get("shadow_notified"):
        return
    until = int(user.get("shadow_until") or 0)
    text = texts.SHADOW_NOTICE.format(
        reason=esc(user.get("shadow_reason") or "жалобы пользователей"),
        until=fmt_dt(until),
        left=human_delta(max(0, until - now())),
    )
    await notify.send_message(bot, db, chat_id, text, reply_markup=inline.appeal_button())
    await db.execute("UPDATE users SET shadow_notified = 1 WHERE id = ?", (int(user["id"]),))
    user["shadow_notified"] = 1
