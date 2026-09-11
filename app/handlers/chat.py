"""Пересылка сообщений между собеседниками и общий перехватчик."""
from __future__ import annotations

from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app import texts, views
from app.db import Database
from app.handlers import ui
from app.keyboards import inline
from app.services import antifraud
from app.services import chat as chat_service
from app.services import likes as likes_service
from app.services import notify
from app.services import profiles as profiles_service
from app.services import users as users_service
from app.states import Chat
from app.utils.text import analyze_text, esc
from app.utils.time import now

router = Router(name="chat")

SCAM_WARN_COOLDOWN = 1800
_scam_warned: dict[tuple[int, int], int] = {}

BLOCKED_KINDS = {
    "document": "Файлы пересылать нельзя — так безопаснее для обоих.",
    "audio": "Аудиофайлы пересылать нельзя. Можно записать голосовое сообщение.",
    "contact": "Контакты пересылать нельзя. Если готов поделиться — напиши текстом.",
    "location": "Геолокацию пересылать нельзя: не раскрывай, где ты находишься.",
}


def describe(message: Message) -> tuple[str | None, str | None, str | None]:
    """Возвращает (тип, текст, file_id) либо (None, причина отказа, None)."""
    if message.text:
        return "text", message.text, None
    if message.photo:
        return "photo", message.caption, message.photo[-1].file_id
    if message.video:
        return "video", message.caption, message.video.file_id
    if message.voice:
        return "voice", message.caption, message.voice.file_id
    if message.video_note:
        return "video_note", None, message.video_note.file_id
    if message.sticker:
        return "sticker", message.sticker.emoji, message.sticker.file_id
    if message.animation:
        return "animation", message.caption, message.animation.file_id
    if message.document:
        return None, BLOCKED_KINDS["document"], None
    if message.audio:
        return None, BLOCKED_KINDS["audio"], None
    if message.contact:
        return None, BLOCKED_KINDS["contact"], None
    if message.location or message.venue:
        return None, BLOCKED_KINDS["location"], None
    return None, texts.CHAT_UNSUPPORTED, None


@router.message(Chat.chatting, F.text == texts.BTN_CHAT_EXIT)
async def exit_chat(
    message: Message, bot: Bot, state: FSMContext, db: Database, user: dict[str, Any]
) -> None:
    chat_service.close_chat(int(user["id"]))
    await state.set_state(None)
    await state.update_data(chat_match=0, chat_partner=0)
    await ui.show_menu(bot, db, message.chat.id, user, texts.CHAT_CLOSED)


@router.message(Chat.chatting)
async def relay(
    message: Message,
    bot: Bot,
    state: FSMContext,
    db: Database,
    user: dict[str, Any],
) -> None:
    user_id = int(user["id"])
    data = await state.get_data()
    match_id = int(data.get("chat_match") or 0)
    partner_id = int(data.get("chat_partner") or 0)

    if not match_id or not partner_id:
        chat_service.close_chat(user_id)
        await state.set_state(None)
        await ui.show_menu(bot, db, message.chat.id, user, texts.CHAT_CLOSED)
        return

    match = await likes_service.match_by_id(db, match_id)
    if not match or not match.get("active") or user_id not in (
        int(match["user_a"]),
        int(match["user_b"]),
    ):
        chat_service.close_chat(user_id)
        await state.set_state(None)
        await ui.show_menu(bot, db, message.chat.id, user, texts.CHAT_PARTNER_GONE)
        return

    partner = await users_service.get(db, partner_id)
    partner_profile = await profiles_service.get(db, partner_id)
    if not partner or not partner_profile or users_service.is_banned(partner):
        chat_service.close_chat(user_id)
        await state.set_state(None)
        await ui.show_menu(bot, db, message.chat.id, user, texts.CHAT_PARTNER_GONE)
        return

    if await likes_service.is_blocked(db, user_id, partner_id):
        chat_service.close_chat(user_id)
        await state.set_state(None)
        await ui.show_menu(bot, db, message.chat.id, user, texts.CHAT_PARTNER_GONE)
        return

    kind, text, file_id = describe(message)
    if kind is None:
        await message.answer(f"⚠️ {text}")
        return

    my_profile = await profiles_service.get(db, user_id)
    my_name = str((my_profile or {}).get("name") or "Собеседник")
    silent = not partner.get("notify_messages")

    if chat_service.need_header(partner_id, match_id):
        await notify.send_message(
            bot,
            db,
            partner_id,
            texts.NEW_MESSAGE_HEADER.format(name=esc(my_name)),
            reply_markup=inline.new_message_notice(match_id),
            disable_notification=silent,
        )

    copied = await notify.copy_message(
        bot, db, partner_id, message.chat.id, message.message_id, disable_notification=silent
    )
    if copied is None:
        await message.answer(
            "⚠️ Сообщение не доставлено: собеседник недоступен или заблокировал бота."
        )
        return

    await chat_service.save_message(
        db,
        match_id=match_id,
        from_id=user_id,
        to_id=partner_id,
        kind=kind,
        text=text,
        file_id=file_id,
    )

    if text:
        await _maybe_warn_scam(bot, db, match_id, user_id, partner_id, text)


async def _maybe_warn_scam(
    bot: Bot, db: Database, match_id: int, from_id: int, to_id: int, text: str
) -> None:
    """Предупреждает получателя, если сообщение пахнет разводом."""
    score, reasons = analyze_text(text)
    if score < 45:
        return

    key = (to_id, match_id)
    moment = now()
    if moment - _scam_warned.get(key, 0) < SCAM_WARN_COOLDOWN:
        return
    _scam_warned[key] = moment
    if len(_scam_warned) > 5000:
        for stale in list(_scam_warned)[:1000]:
            _scam_warned.pop(stale, None)

    await antifraud.log_event(
        db, from_id, "spam_text", weight=10, meta={"where": "chat", "score": score}
    )
    await notify.send_message(
        bot,
        db,
        to_id,
        "⚠️ <b>Осторожно</b>\n\n"
        f"В последнем сообщении бот заметил: <i>{esc(', '.join(reasons[:3]))}</i>.\n\n"
        "Так часто начинаются разводы: ссылка на «сайт с верификацией», просьба перевести денег "
        "или увести разговор в другой мессенджер.\n\n"
        "Никогда не переводи деньги и не отправляй коды из SMS. Если это развод — жми «🚩».",
        reply_markup=inline.chat_actions(match_id, from_id),
    )


# --------------------------------------------------------------------------- перехватчик


@router.message(StateFilter(None), F.text)
async def unknown_text(
    message: Message, bot: Bot, db: Database, user: dict[str, Any]
) -> None:
    profile = await profiles_service.get(db, int(user["id"]))
    if not profile or not profile.get("is_complete"):
        await message.answer(texts.NOT_REGISTERED)
        return
    await ui.show_menu(bot, db, message.chat.id, user, texts.UNKNOWN_INPUT)


@router.message(StateFilter(None))
async def unknown_other(message: Message, bot: Bot, db: Database, user: dict[str, Any]) -> None:
    await ui.show_menu(bot, db, message.chat.id, user, texts.UNKNOWN_INPUT)
