"""Старт, правила, капча, справка."""
from __future__ import annotations

import random
import re
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import texts
from app.callbacks import CaptchaCB, RegCB
from app.config import Config
from app.constants import HOUR
from app.db import Database
from app.handlers import ui
from app.keyboards import inline, reply
from app.services import antifraud
from app.services import limits as limits_service
from app.services import profiles as profiles_service
from app.services import users as users_service
from app.services.settings import Settings
from app.states import Reg
from app.utils.text import plural
from app.utils.time import now

router = Router(name="common")

CAPTCHA_POOL = ["🍎", "🌙", "⚽️", "🚗", "🌻", "🐱", "🎈", "🍕", "⭐️", "🐬", "🎸", "🧩"]
CAPTCHA_ATTEMPTS = 3
CAPTCHA_BLOCK = HOUR

_REF_RE = re.compile(r"^ref(\d{5,15})$")
_SOURCE_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")


async def _captcha_locked(db: Database, user_id: int) -> bool:
    fails = await db.fetchval(
        "SELECT COUNT(*) FROM events WHERE user_id = ? AND kind = 'captcha_failed' "
        "AND created_at > ?",
        (user_id, now() - CAPTCHA_BLOCK),
        0,
    )
    return int(fails) >= CAPTCHA_ATTEMPTS


async def send_captcha(message: Message, state: FSMContext) -> None:
    options = random.sample(CAPTCHA_POOL, 6)
    answer = random.choice(options)
    nonce = random.randint(1, 999_999)
    await state.set_state(Reg.captcha)
    await state.update_data(captcha_answer=answer, captcha_nonce=nonce, captcha_tries=0)
    await message.answer(
        texts.CAPTCHA.format(symbol=answer),
        reply_markup=inline.captcha(options, nonce),
    )


async def start_registration(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    """Капча (если нужна) и первый шаг анкеты."""
    if await _captcha_locked(db, int(user["id"])):
        await message.answer(texts.CAPTCHA_BLOCKED)
        return
    if await antifraud.needs_captcha(db, settings, user):
        await send_captcha(message, state)
        return
    await begin_profile(message, state, db, user)


async def begin_profile(
    message: Message, state: FSMContext, db: Database, user: dict[str, Any]
) -> None:
    """Продолжает незаконченную анкету или начинает новую."""
    from app.handlers.registration import resume

    profile = await profiles_service.get(db, int(user["id"]))
    if profile and await resume(message, state, db, profile):
        return
    await state.set_state(Reg.name)
    await message.answer(texts.REG_INTRO, reply_markup=reply.remove)
    await message.answer(texts.ASK_NAME)


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject | None,
    state: FSMContext,
    bot: Bot,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    await ui.leave_chat_mode(state, int(user["id"]))

    payload = (command.args or "").strip() if command else ""
    if payload and not user.get("source"):
        await _remember_source(db, user, payload)

    profile = await profiles_service.get(db, int(user["id"]))
    if profile and profile.get("is_complete"):
        await ui.send_restriction_notice(bot, db, message.chat.id, user)
        await ui.show_menu(bot, db, message.chat.id, user, texts.MENU)
        return

    if not user.get("rules_accepted_at"):
        await message.answer(texts.WELCOME, reply_markup=reply.remove)
        await message.answer(texts.RULES, reply_markup=inline.rules())
        return

    if not settings.get_bool("registration_open", True):
        await message.answer(texts.REG_CLOSED)
        return

    await start_registration(message, state, db, settings, user)


async def _remember_source(db: Database, user: dict[str, Any], payload: str) -> None:
    """Запоминает, откуда пришёл человек: помогает вычислять ботофермы."""
    referrer_id: int | None = None
    match = _REF_RE.match(payload)
    source = payload if _SOURCE_RE.match(payload) else "other"
    if match:
        candidate = int(match.group(1))
        if candidate != int(user["id"]):
            referrer = await users_service.get(db, candidate)
            if referrer:
                referrer_id = candidate
                source = "referral"
    await db.execute(
        "UPDATE users SET source = ?, referrer_id = COALESCE(referrer_id, ?) WHERE id = ?",
        (source, referrer_id, int(user["id"])),
    )


# --------------------------------------------------------------------------- правила


@router.callback_query(RegCB.filter(F.action == "safety"))
async def show_safety_from_rules(query: CallbackQuery) -> None:
    if query.message:
        await query.message.edit_text(texts.SAFETY, reply_markup=inline.after_safety())
    await query.answer()


@router.callback_query(RegCB.filter(F.action == "rules_back"))
async def back_to_rules(query: CallbackQuery) -> None:
    if query.message:
        await query.message.edit_text(texts.RULES, reply_markup=inline.rules())
    await query.answer()


@router.callback_query(RegCB.filter(F.action == "rules_no"))
async def decline_rules(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if query.message:
        await query.message.edit_text(texts.RULES_DECLINED)
    await query.answer()


@router.callback_query(RegCB.filter(F.action == "rules_ok"))
async def accept_rules(
    query: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    await query.answer()
    if query.message is None:
        return

    if not user.get("rules_accepted_at"):
        await users_service.accept_rules(db, int(user["id"]))
        user["rules_accepted_at"] = now()

    await query.message.edit_text(
        texts.RULES + "\n\n✅ <b>Правила приняты.</b>",
    )

    if not settings.get_bool("registration_open", True):
        await query.message.answer(texts.REG_CLOSED)
        return

    profile = await profiles_service.get(db, int(user["id"]))
    if profile and profile.get("is_complete"):
        await ui.show_menu(query.bot, db, query.message.chat.id, user)
        return
    await start_registration(query.message, state, db, settings, user)


# --------------------------------------------------------------------------- капча


@router.callback_query(Reg.captcha, CaptchaCB.filter())
async def check_captcha(
    query: CallbackQuery,
    callback_data: CaptchaCB,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    data = await state.get_data()
    if int(data.get("captcha_nonce") or 0) != callback_data.nonce:
        await query.answer("Эта проверка устарела, начни заново: /start", show_alert=True)
        return

    if callback_data.value == data.get("captcha_answer"):
        await users_service.set_captcha_passed(db, int(user["id"]))
        user["captcha_passed"] = 1
        await query.answer(texts.CAPTCHA_OK)
        if query.message:
            await query.message.edit_text(texts.CAPTCHA_OK)
            await begin_profile(query.message, state, db, user)
        return

    tries = int(data.get("captcha_tries") or 0) + 1
    await state.update_data(captcha_tries=tries)
    await antifraud.log_event(db, int(user["id"]), "captcha_failed", weight=3)

    left = CAPTCHA_ATTEMPTS - tries
    if left <= 0:
        await antifraud.log_event(db, int(user["id"]), "captcha_failed")
        await state.clear()
        await query.answer("Проверка не пройдена", show_alert=True)
        if query.message:
            await query.message.edit_text(texts.CAPTCHA_BLOCKED)
        return

    await query.answer(
        texts.CAPTCHA_FAILED.format(
            left=left, attempts=plural(left, "попытка", "попытки", "попыток")
        ),
        show_alert=True,
    )
    if query.message:
        options = random.sample(CAPTCHA_POOL, 6)
        answer = random.choice(options)
        nonce = random.randint(1, 999_999)
        await state.update_data(captcha_answer=answer, captcha_nonce=nonce)
        await query.message.edit_text(
            texts.CAPTCHA.format(symbol=answer), reply_markup=inline.captcha(options, nonce)
        )


@router.message(Reg.captcha)
async def captcha_needs_button(message: Message) -> None:
    await message.answer("Нажми кнопку с нужным символом 👆 Если проверка исчезла — отправь /start.")


# --------------------------------------------------------------------------- справка


@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(
    message: Message, db: Database, settings: Settings, config: Config, user: dict[str, Any]
) -> None:
    limits = await limits_service.breakdown(db, user, settings)
    await message.answer(texts.help_text(limits.total, config.support_contact))


@router.message(Command("safety"))
@router.message(F.text == texts.BTN_SAFETY)
async def cmd_safety(message: Message) -> None:
    await message.answer(texts.SAFETY)


@router.message(Command("menu"))
async def cmd_menu(
    message: Message, bot: Bot, db: Database, user: dict[str, Any], state: FSMContext
) -> None:
    await ui.leave_chat_mode(state, int(user["id"]))
    await ui.show_menu(bot, db, message.chat.id, user)


@router.message(Command("cancel"))
@router.message(StateFilter("*"), F.text == "❌ Отмена")
async def cmd_cancel(
    message: Message, bot: Bot, state: FSMContext, db: Database, user: dict[str, Any]
) -> None:
    current = await state.get_state()
    await ui.leave_chat_mode(state, int(user["id"]))
    if current is None:
        await ui.show_menu(bot, db, message.chat.id, user)
        return
    await message.answer("Отменено.")
    await ui.show_menu(bot, db, message.chat.id, user)
