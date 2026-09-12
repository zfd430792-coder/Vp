"""Лента анкет: лайки, дизлайки, суперлайки."""
from __future__ import annotations

from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import texts
from app.callbacks import FeedCB
from app.constants import ACT_LIKE, ACT_PASS, ACT_SUPERLIKE, HOUR
from app.db import Database
from app.handlers import ui
from app.keyboards import inline
from app.services import antifraud, notify, render
from app.services import likes as likes_service
from app.services import limits as limits_service
from app.services import profiles as profiles_service
from app.services import users as users_service
from app.services.settings import Settings
from app.states import Like
from app.utils.text import has_contacts, human_delta, shorten
from app.utils.time import now

router = Router(name="feed")

LIKE_NOTIFY_COOLDOWN = 3 * HOUR
NOTE_MAX_LEN = 200


@router.message(StateFilter(None), F.text == texts.BTN_FEED)
@router.message(Command("feed"))
async def open_feed(
    message: Message,
    bot: Bot,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    await ui.leave_chat_mode(db, state, int(user["id"]))
    profile = await ui.require_profile(bot, db, message.chat.id, user)
    if not profile:
        return
    await ui.send_restriction_notice(bot, db, message.chat.id, user)
    await ui.show_next_profile(
        bot, db, settings, state, chat_id=message.chat.id, user=user, profile=profile
    )


@router.callback_query(FeedCB.filter(F.action == "refresh"))
async def refresh_feed(
    query: CallbackQuery,
    bot: Bot,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    await query.answer()
    if query.message is None:
        return
    profile = await ui.require_profile(bot, db, query.message.chat.id, user)
    if not profile:
        return
    await ui.show_next_profile(
        bot, db, settings, state, chat_id=query.message.chat.id, user=user, profile=profile
    )


@router.callback_query(FeedCB.filter(F.action.in_({"like", "super", "pass"})))
async def feed_action(
    query: CallbackQuery,
    callback_data: FeedCB,
    bot: Bot,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    if query.message is None:
        await query.answer()
        return

    chat_id = query.message.chat.id
    user_id = int(user["id"])
    target_id = int(callback_data.target)

    await ui.leave_chat_mode(db, state, user_id)
    profile = await profiles_service.get(db, user_id)
    if not profile or not profile.get("is_complete"):
        await query.answer(texts.NOT_REGISTERED, show_alert=True)
        return

    if target_id == user_id:
        await query.answer()
        return

    action_map = {"like": ACT_LIKE, "super": ACT_SUPERLIKE, "pass": ACT_PASS}
    action = action_map[callback_data.action]

    # Защита от автокликеров: слишком ровный темп — действие не засчитывается
    problem = await antifraud.check_swipe(db, user_id, action)
    if problem == "fast_swipes":
        await users_service.recompute_trust(db, user_id)
        await query.answer(
            "Слишком быстро — так листают только скрипты.\n"
            "Сделай паузу пару секунд и продолжай 🙂",
            show_alert=True,
        )
        return

    already = await likes_service.existing(db, user_id, target_id)
    if already and already["action"] in (ACT_LIKE, ACT_SUPERLIKE):
        await query.answer("Ты уже оценил эту анкету", show_alert=False)
        await ui.show_next_profile(
            bot, db, settings, state, chat_id=chat_id, user=user, profile=profile
        )
        return

    notice: str | None = None

    if action in (ACT_LIKE, ACT_SUPERLIKE):
        quota = await limits_service.quota(db, user, settings)
        if quota.exhausted:
            await query.answer(
                f"Лимит лайков исчерпан. Новый — через {human_delta(quota.reset_in)}",
                show_alert=True,
            )
            await notify.send_message(
                bot,
                db,
                chat_id,
                texts.LIMIT_REACHED.format(
                    used=quota.used, limit=quota.limit, reset=human_delta(quota.reset_in)
                ),
            )
            return
        if action == ACT_SUPERLIKE and quota.superlikes_left <= 0:
            await query.answer(
                texts.SUPERLIKE_LIMIT.format(limit=quota.superlike_limit, left=quota.left),
                show_alert=True,
            )
            return

    result = await likes_service.act(db, user_id, target_id, action)
    if not result.recorded:
        await query.answer()
        return

    if problem == "like_everyone":
        notice = (
            "🤖 <i>Ты лайкаешь почти всех подряд. Это не увеличивает шанс на взаимность, "
            "зато выглядит как накрутка — анкете это только вредит.</i>"
        )
        await users_service.recompute_trust(db, user_id)

    if action == ACT_PASS:
        await query.answer(texts.PASS_DONE)
    elif action == ACT_SUPERLIKE:
        await query.answer(texts.SUPERLIKE_SENT)
    else:
        await query.answer(texts.LIKE_SENT)

    if result.matched and result.created_match and result.match_id:
        await celebrate_match(bot, db, user_id, target_id, result.match_id)
    elif action in (ACT_LIKE, ACT_SUPERLIKE):
        await _maybe_notify_like(bot, db, target_id, superlike=action == ACT_SUPERLIKE)
        if action == ACT_SUPERLIKE:
            await notify.send_message(
                bot,
                db,
                chat_id,
                "💥 <b>Суперлайк отправлен</b>\n\n"
                "Хочешь добавить пару слов? С сообщением отвечают заметно чаще.",
                reply_markup=inline.superlike_note(target_id),
            )

    await ui.show_next_profile(
        bot, db, settings, state, chat_id=chat_id, user=user, profile=profile, notice=notice
    )


@router.callback_query(FeedCB.filter(F.action == "note"))
async def superlike_note_start(
    query: CallbackQuery,
    callback_data: FeedCB,
    state: FSMContext,
    db: Database,
    user: dict[str, Any],
) -> None:
    """Пара слов к суперлайку — бесплатно и без «премиума»."""
    row = await likes_service.existing(db, int(user["id"]), int(callback_data.target))
    if not row or row["action"] != ACT_SUPERLIKE:
        await query.answer("Суперлайк уже неактуален", show_alert=True)
        return
    if row["responded"]:
        await query.answer("Человек уже ответил — напиши ему в диалоге", show_alert=True)
        return

    await state.set_state(Like.note)
    await state.update_data(note_target=int(callback_data.target))
    await query.answer()
    if query.message:
        await query.message.answer(
            f"💬 Напиши пару строк (до {NOTE_MAX_LEN} символов) — человек увидит их вместе "
            "с твоим суперлайком.\n\nОтменить: /cancel"
        )


@router.message(Like.note, F.text)
async def superlike_note_save(
    message: Message, state: FSMContext, db: Database, user: dict[str, Any]
) -> None:
    data = await state.get_data()
    target_id = int(data.get("note_target") or 0)
    await state.set_state(None)
    if not target_id:
        await message.answer(texts.ERROR_GENERIC)
        return

    note = (message.text or "").strip()
    if has_contacts(note):
        await message.answer(
            "⚠️ Ссылки, ники и номера телефонов отправлять нельзя. Напиши обычным текстом."
        )
        await state.set_state(Like.note)
        return
    note = shorten(note, NOTE_MAX_LEN)

    updated = await db.modify(
        "UPDATE likes SET message = ? WHERE from_id = ? AND to_id = ? AND action = ? "
        "AND responded = 0",
        (note, int(user["id"]), target_id, ACT_SUPERLIKE),
    )
    if updated:
        await message.answer("✅ Сообщение добавлено к суперлайку.")
    else:
        await message.answer("Суперлайк уже неактуален — сообщение не добавлено.")


@router.message(Like.note)
async def superlike_note_wrong(message: Message) -> None:
    await message.answer("Здесь нужен текст. Отменить — /cancel")


async def _maybe_notify_like(bot: Bot, db: Database, target_id: int, *, superlike: bool) -> None:
    """Уведомление о новом лайке — не чаще раза в несколько часов."""
    target = await users_service.get(db, target_id)
    if not target or not target.get("notify_likes") or target.get("bot_blocked"):
        return
    if users_service.is_banned(target):
        return
    last = int(target.get("last_like_notify_at") or 0)
    if not superlike and now() - last < LIKE_NOTIFY_COOLDOWN:
        return

    count = await likes_service.incoming_count(db, target_id)
    if superlike:
        text = (
            "💥 <b>Тебе отправили суперлайк!</b>\n\n"
            "Это значит, что человек очень заинтересован. Загляни в «Мои лайки»."
        )
    else:
        text = (
            f"💌 <b>Ты кому-то понравился!</b>\n\n"
            f"Входящих лайков: <b>{count}</b>. Смотреть можно бесплатно — без подписок и «открыть за 199₽»."
        )
    sent = await notify.send_message(bot, db, target_id, text, reply_markup=inline.likes_notify())
    if sent:
        await db.execute(
            "UPDATE users SET last_like_notify_at = ? WHERE id = ?", (now(), target_id)
        )


async def celebrate_match(
    bot: Bot, db: Database, user_id: int, target_id: int, match_id: int
) -> None:
    """Сообщает обоим о взаимной симпатии."""
    for me, partner in ((user_id, target_id), (target_id, user_id)):
        partner_card = await profiles_service.card(db, partner)
        if not partner_card:
            continue
        me_user = await users_service.get(db, me)
        if not me_user or users_service.is_banned(me_user):
            continue
        if me != user_id and not me_user.get("notify_matches"):
            continue

        await render.send_card(
            bot,
            db,
            me,
            partner_card,
            keyboard=inline.new_match(match_id),
            header=texts.match_text(str(partner_card.get("name") or "")),
            show_activity=False,
        )
        matches_total = int(
            await db.fetchval(
                "SELECT matches_count FROM profiles WHERE user_id = ?", (me,), 0
            )
        )
        if matches_total <= 1:
            await notify.send_message(bot, db, me, texts.MATCH_SAFETY_HINT)
