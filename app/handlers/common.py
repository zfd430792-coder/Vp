"""Старт, правила, капча, справка."""
from __future__ import annotations

import asyncio
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
from app.services import antifraud, notify
from app.services import limits as limits_service
from app.services import profiles as profiles_service
from app.services import users as users_service
from app.services.settings import Settings
from app.states import Reg
from app.utils.text import esc, plural
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


def warning_delay(settings: Settings) -> int:
    """Сколько секунд держать кнопку согласия закрытой."""
    return max(0, min(30, settings.get_int("warning_delay", 4)))


async def show_warning(
    bot: Bot, db: Database, settings: Settings, chat_id: int, state: FSMContext
) -> None:
    """Показывает предупреждение и открывает кнопку согласия через паузу.

    Кнопки под сообщением сначала нет вообще: она появляется отдельной правкой,
    когда отсчёт закончится. Так человек успевает прочитать текст, а ботоферма
    не может проскочить экран мгновенным нажатием.
    """
    delay = warning_delay(settings)

    def countdown(left: int) -> str:
        word = plural(left, "секунду", "секунды", "секунд")
        return texts.WARNING + texts.WARNING_WAIT.format(left=f"{left} {word}")

    if delay <= 0:
        await notify.send_message(
            bot,
            db,
            chat_id,
            texts.WARNING + texts.WARNING_READY,
            reply_markup=inline.warning_accept(),
        )
        await state.update_data(warning_at=0)
        return

    message = await notify.send_message(bot, db, chat_id, countdown(delay))
    if message is None:
        return

    await state.update_data(warning_at=now(), warning_msg=message.message_id)

    for left in range(delay - 1, 0, -1):
        await asyncio.sleep(1)
        await notify.safe_call(
            db,
            chat_id,
            bot.edit_message_text,
            text=countdown(left),
            chat_id=chat_id,
            message_id=message.message_id,
        )

    await asyncio.sleep(1)
    await notify.safe_call(
        db,
        chat_id,
        bot.edit_message_text,
        text=texts.WARNING + texts.WARNING_READY,
        chat_id=chat_id,
        message_id=message.message_id,
        reply_markup=inline.warning_accept(),
    )


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
    await ui.leave_chat_mode(db, state, int(user["id"]))

    payload = (command.args or "").strip() if command else ""
    if payload and not user.get("source"):
        await _remember_source(db, user, payload)

    profile = await profiles_service.get(db, int(user["id"]))

    # Анкета есть — здороваемся и сразу показываем меню
    if profile and profile.get("is_complete"):
        await ui.send_restriction_notice(bot, db, message.chat.id, user)
        name = profile.get("name") or user.get("tg_name") or "друг"
        await ui.show_menu(
            bot, db, message.chat.id, user, texts.WELCOME_BACK.format(name=esc(name))
        )
        return

    # Анкеты нет — здороваемся и даём кнопку. Памятку про мошенников показываем
    # не сразу: человек сначала решает, что хочет анкету, и только потом читает
    # предупреждение — так его читают, а не пролистывают вместе с приветствием.
    greeting = texts.WELCOME.format(name=_greeting_name(user))

    if not settings.get_bool("registration_open", True):
        await message.answer(greeting)
        await message.answer(texts.REG_CLOSED)
        return

    # Анкету уже начинали — зовём продолжить, а не начинать заново
    started = bool(profile)
    await message.answer(greeting, reply_markup=inline.start_profile(resume=started))


def _greeting_name(user: dict[str, Any]) -> str:
    """@юзернейм, если он есть, иначе имя из Telegram."""
    username = (user.get("username") or "").strip()
    if username:
        return "@" + esc(username)
    name = (user.get("tg_name") or "").strip()
    return esc(name) if name else "друг"


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


@router.callback_query(RegCB.filter(F.action == "begin"))
async def begin_from_welcome(
    query: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    """Кнопка «Создать анкету» под приветствием."""
    await query.answer()
    if query.message is None:
        return

    if not settings.get_bool("registration_open", True):
        await query.message.answer(texts.REG_CLOSED)
        return

    # Кнопка остаётся в переписке: могли нажать её и после того, как анкета
    # уже готова. Тогда предупреждение ни к чему — просто открываем меню.
    profile = await profiles_service.get(db, int(user["id"]))
    if profile and profile.get("is_complete"):
        await ui.show_menu(bot, db, query.message.chat.id, user)
        return

    # Убираем кнопку у приветствия, чтобы её не нажали второй раз
    await notify.safe_call(
        db,
        query.message.chat.id,
        bot.edit_message_reply_markup,
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        reply_markup=None,
    )
    await show_warning(bot, db, settings, query.message.chat.id, state)


@router.callback_query(RegCB.filter(F.action == "safety"))
async def show_safety_from_warning(query: CallbackQuery) -> None:
    if query.message:
        await query.message.edit_text(texts.SAFETY, reply_markup=inline.after_safety())
    await query.answer()


@router.callback_query(RegCB.filter(F.action == "rules_back"))
async def back_to_warning(query: CallbackQuery) -> None:
    if query.message:
        await query.message.edit_text(
            texts.WARNING + texts.WARNING_READY, reply_markup=inline.warning_accept()
        )
    await query.answer()


@router.callback_query(RegCB.filter(F.action == "rules_no"))
async def decline_rules(query: CallbackQuery) -> None:
    # Кнопки «Не согласен» больше нет, но она могла остаться в старых сообщениях.
    # Отвечаем, чтобы у человека не крутился вечный индикатор нажатия.
    await query.answer("Кнопка устарела — отправь /start", show_alert=True)


@router.callback_query(RegCB.filter(F.action == "rules_ok"))
async def accept_rules(
    query: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    if query.message is None:
        await query.answer()
        return

    # Подстраховка на случай нажатия в обход таймера: кнопки до срока и нет,
    # но старая клавиатура из другого сообщения могла сохраниться у клиента
    data = await state.get_data()
    shown_at = int(data.get("warning_at") or 0)
    delay = warning_delay(settings)
    if shown_at and now() - shown_at < delay:
        left = delay - (now() - shown_at)
        await query.answer(
            f"Дочитай, пожалуйста — кнопка станет активной через {left} сек",
            show_alert=True,
        )
        return

    await query.answer()

    if not user.get("rules_accepted_at"):
        await users_service.accept_rules(db, int(user["id"]))
        user["rules_accepted_at"] = now()

    await query.message.edit_text(
        texts.WARNING + "\n\n✅ <b>Правила приняты.</b>",
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
    await ui.leave_chat_mode(db, state, int(user["id"]))
    await ui.show_menu(bot, db, message.chat.id, user)


@router.message(Command("cancel"))
@router.message(StateFilter("*"), F.text == "❌ Отмена")
async def cmd_cancel(
    message: Message, bot: Bot, state: FSMContext, db: Database, user: dict[str, Any]
) -> None:
    current = await state.get_state()
    await ui.leave_chat_mode(db, state, int(user["id"]))
    if current is None:
        await ui.show_menu(bot, db, message.chat.id, user)
        return
    await message.answer("Отменено.")
    await ui.show_menu(bot, db, message.chat.id, user)
