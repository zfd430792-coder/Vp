"""Просмотр и редактирование своей анкеты."""
from __future__ import annotations

from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import texts, views
from app.callbacks import ProfileCB
from app.config import Config
from app.constants import (
    BIO_MAX_LEN,
    BONUS_VERIFIED,
    MAX_INTERESTS,
    MAX_PHOTOS,
    MOD_HOLD,
    VERIFY_PENDING,
    VERIFY_RETRY_COOLDOWN,
)
from app.db import Database
from app.handlers import ui
from app.keyboards import inline
from app.services import antifraud, insights, notify, render, verification
from app.services import likes as likes_service
from app.services import limits as limits_service
from app.services import profiles as profiles_service
from app.services.settings import Settings
from app.states import Edit, Verify
from app.utils.text import (
    ValidationError,
    clean_age,
    clean_bio,
    clean_city,
    clean_name,
    has_contacts,
    human_delta,
)
from app.utils.time import now

router = Router(name="profile")


async def show_profile(
    bot: Bot,
    db: Database,
    settings: Settings,
    state: FSMContext,
    chat_id: int,
    user: dict[str, Any],
) -> None:
    user_id = int(user["id"])
    card = await profiles_service.card(db, user_id)
    if not card or not card.get("is_complete"):
        await notify.send_message(bot, db, chat_id, texts.NOT_REGISTERED)
        return

    await ui.clear_ui(bot, state, chat_id)
    quota = await limits_service.quota(db, user, settings)
    incoming = await likes_service.incoming_count(db, user_id)
    matches = await likes_service.count_matches(db, user_id)

    text = views.own_profile_text(
        card,
        likes_left=quota.left,
        limit=quota.limit,
        incoming=incoming,
        matches=matches,
    )
    restriction = views.restriction_text(user)
    if restriction:
        text += "\n\n" + restriction

    message_ids = await render.send_card(
        bot,
        db,
        chat_id,
        card,
        keyboard=inline.profile_menu(
            card,
            verified=bool(card.get("verified")),
            verify_pending=verification.status_of(user) == VERIFY_PENDING,
        ),
        show_activity=False,
    )
    # Карточка показана целиком, отдельным сообщением — состояние и статистика
    message = await notify.send_message(bot, db, chat_id, text)
    if message:
        message_ids.append(message.message_id)
    await ui.remember_ui(state, message_ids)


@router.message(StateFilter(None), F.text == texts.BTN_PROFILE)
@router.message(Command("profile"))
async def open_profile(
    message: Message,
    bot: Bot,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    await ui.leave_chat_mode(db, state, int(user["id"]))
    await show_profile(bot, db, settings, state, message.chat.id, user)


@router.callback_query(ProfileCB.filter(F.action == "menu"))
async def back_to_profile(
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
    await show_profile(bot, db, settings, state, query.message.chat.id, user)


@router.callback_query(ProfileCB.filter(F.action == "insights"))
async def show_insights(
    query: CallbackQuery, bot: Bot, db: Database, user: dict[str, Any]
) -> None:
    """Честная статистика анкеты и конкретные советы вместо продажи «буста»."""
    await query.answer()
    if query.message is None:
        return
    user_id = int(user["id"])
    card = await profiles_service.get(db, user_id)
    if not card:
        return
    stats = await insights.summary(db, user_id)
    photos = await profiles_service.count_photos(db, user_id)
    await notify.send_message(
        bot,
        db,
        query.message.chat.id,
        views.insights_text(
            stats, card=card, photos=photos, verified=bool(user.get("verified"))
        ),
    )


@router.callback_query(ProfileCB.filter(F.action == "limits"))
async def show_limits(
    query: CallbackQuery, bot: Bot, db: Database, settings: Settings, user: dict[str, Any]
) -> None:
    await query.answer()
    if query.message is None:
        return
    limits = await limits_service.breakdown(db, user, settings)
    await notify.send_message(bot, db, query.message.chat.id, views.limit_details(limits))


# --------------------------------------------------------------------------- верификация


@router.callback_query(ProfileCB.filter(F.action == "verify"))
async def verify_intro(
    query: CallbackQuery, db: Database, settings: Settings, user: dict[str, Any]
) -> None:
    if not settings.get_bool("verification_enabled", True):
        await query.answer(texts.VERIFY_OFF, show_alert=True)
        return

    allowed, reason = await verification.can_request(db, user)
    if not allowed:
        messages = {
            "already": texts.VERIFY_ALREADY,
            "pending": texts.VERIFY_IN_PROGRESS,
            "attempts": texts.VERIFY_ATTEMPTS,
            "cooldown": texts.VERIFY_COOLDOWN.format(
                left=human_delta(
                    VERIFY_RETRY_COOLDOWN - (now() - int(user.get("verify_at") or 0))
                )
            ),
        }
        await query.answer(messages.get(reason, texts.VERIFY_OFF), show_alert=True)
        return

    await query.answer()
    if query.message:
        await query.message.answer(
            texts.VERIFY_INTRO.format(bonus=BONUS_VERIFIED),
            reply_markup=inline.verify_start(),
        )


@router.callback_query(ProfileCB.filter(F.action == "verify_go"))
async def verify_ask(
    query: CallbackQuery, state: FSMContext, db: Database, user: dict[str, Any]
) -> None:
    allowed, _ = await verification.can_request(db, user)
    if not allowed:
        await query.answer(texts.VERIFY_IN_PROGRESS, show_alert=True)
        return

    gesture = verification.pick_gesture()
    await verification.start(db, int(user["id"]), gesture)
    await state.set_state(Verify.selfie)
    await query.answer()
    if query.message:
        await query.message.answer(texts.VERIFY_ASK.format(gesture=gesture))


@router.message(Verify.selfie, F.photo)
async def verify_selfie(
    message: Message,
    bot: Bot,
    state: FSMContext,
    db: Database,
    config: Config,
    user: dict[str, Any],
) -> None:
    user_id = int(user["id"])
    best = message.photo[-1]

    # Селфи не должно совпадать с фото из анкеты: так проверка теряет смысл
    own = await db.fetchone(
        "SELECT 1 FROM photos WHERE user_id = ? AND file_unique_id = ?",
        (user_id, best.file_unique_id),
    )
    if own:
        await message.answer(
            "⚠️ Это фотография из твоей анкеты. Нужно новое селфи, сделанное сейчас, "
            "с нужным жестом."
        )
        return

    await verification.submit(db, user_id, best.file_id)
    await antifraud.log_event(db, user_id, "verify_sent", weight=0)
    await state.set_state(None)
    await message.answer(texts.VERIFY_SENT)
    await notify.notify_staff(
        bot,
        db,
        f"✅ <b>Новая заявка на верификацию</b>\nОт: <code>{user_id}</code>",
        chat_id=config.moderation_chat_id,
    )


@router.message(Verify.selfie)
async def verify_wrong_type(message: Message) -> None:
    await message.answer(texts.VERIFY_WRONG_TYPE)


# --------------------------------------------------------------------------- фотографии


@router.callback_query(ProfileCB.filter(F.action == "photos"))
async def photos_menu(
    query: CallbackQuery, bot: Bot, db: Database, user: dict[str, Any]
) -> None:
    await query.answer()
    if query.message is None:
        return
    photos = await profiles_service.photos(db, int(user["id"]))
    text = (
        f"📷 <b>Фотографии ({len(photos)} из {MAX_PHOTOS})</b>\n\n"
        "Первая фотография — главная, её видят в ленте первой."
    )
    await notify.send_message(
        bot,
        db,
        query.message.chat.id,
        text,
        reply_markup=inline.photos_menu(photos, can_add=len(photos) < MAX_PHOTOS),
    )


@router.callback_query(ProfileCB.filter(F.action == "photo_add"))
async def photo_add(query: CallbackQuery, state: FSMContext, db: Database, user: dict[str, Any]) -> None:
    count = await profiles_service.count_photos(db, int(user["id"]))
    if count >= MAX_PHOTOS:
        await query.answer(f"Уже {MAX_PHOTOS} фото — сначала удали лишнее", show_alert=True)
        return
    await state.set_state(Edit.photo)
    await query.answer()
    if query.message:
        await query.message.answer("Пришли новую фотографию 📷")


@router.message(Edit.photo, F.photo)
async def photo_received(
    message: Message,
    bot: Bot,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    user_id = int(user["id"])
    best = message.photo[-1]
    if await profiles_service.is_photo_blocked(db, best.file_unique_id):
        await antifraud.log_event(db, user_id, "banned_content", meta={"kind": "photo"})
        await message.answer(
            "⚠️ Это фото заблокировано модерацией и больше не принимается. "
            "Пришли, пожалуйста, другой снимок."
        )
        return
    added = await profiles_service.add_photo(db, user_id, best.file_id, best.file_unique_id)
    if not added:
        await message.answer(f"Больше {MAX_PHOTOS} фото нельзя.")
    else:
        owners = await antifraud.screen_photo(db, user_id, best.file_unique_id)
        if owners:
            await notify.notify_staff(
                bot,
                db,
                f"♻️ Пользователь <code>{user_id}</code> добавил фото, "
                f"которое уже используют {len(owners)} других аккаунтов.",
            )
        await profiles_service.mark_complete(db, user_id)
        await message.answer("✅ Фото добавлено.")
    await state.set_state(None)
    await show_profile(bot, db, settings, state, message.chat.id, user)


@router.message(Edit.photo)
async def photo_wrong(message: Message) -> None:
    await message.answer("Нужна именно фотография. Или нажми /profile, чтобы выйти.")


@router.callback_query(ProfileCB.filter(F.action == "photo_del"))
async def photo_delete(
    query: CallbackQuery,
    callback_data: ProfileCB,
    bot: Bot,
    db: Database,
    user: dict[str, Any],
) -> None:
    user_id = int(user["id"])
    photos = await profiles_service.photos(db, user_id)
    if len(photos) <= 1:
        await query.answer(
            "Это единственное фото. Сначала добавь новое, потом удаляй старое.", show_alert=True
        )
        return
    try:
        photo_id = int(callback_data.value)
    except ValueError:
        await query.answer()
        return

    deleted = await profiles_service.delete_photo(db, photo_id, user_id)
    if not deleted:
        await query.answer("Фото уже удалено")
    else:
        await query.answer("Удалено")
    await profiles_service.mark_complete(db, user_id)
    photos = await profiles_service.photos(db, user_id)
    if query.message:
        await query.message.edit_text(
            f"📷 <b>Фотографии ({len(photos)} из {MAX_PHOTOS})</b>",
            reply_markup=inline.photos_menu(photos, can_add=len(photos) < MAX_PHOTOS),
        )


# --------------------------------------------------------------------------- текстовые поля


@router.callback_query(ProfileCB.filter(F.action.in_({"name", "age", "city", "bio"})))
async def edit_field(
    query: CallbackQuery, callback_data: ProfileCB, state: FSMContext
) -> None:
    prompts = {
        "name": ("Как тебя зовут? Напиши новое имя.", Edit.name),
        "age": ("Сколько тебе лет? Напиши числом.", Edit.age),
        "city": ("Из какого ты города?", Edit.city),
        "bio": (
            f"Расскажи о себе (до {BIO_MAX_LEN} символов).\n"
            "Отправь «-», чтобы очистить описание.",
            Edit.bio,
        ),
    }
    prompt, target_state = prompts[callback_data.action]
    await state.set_state(target_state)
    await query.answer()
    if query.message:
        await query.message.answer(prompt)


@router.message(Edit.name, F.text)
async def save_name(
    message: Message,
    bot: Bot,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    try:
        name = clean_name(message.text or "")
    except ValidationError as error:
        await message.answer(f"⚠️ {error}")
        return
    await profiles_service.update(db, int(user["id"]), name=name)
    await state.set_state(None)
    await message.answer("✅ Имя обновлено.")
    await show_profile(bot, db, settings, state, message.chat.id, user)


@router.message(Edit.age, F.text)
async def save_age(
    message: Message,
    bot: Bot,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    try:
        age = clean_age(message.text or "")
    except ValidationError as error:
        await message.answer(f"⚠️ {error}")
        return
    await profiles_service.update(db, int(user["id"]), age=age)
    await state.set_state(None)
    await message.answer("✅ Возраст обновлён.")
    await show_profile(bot, db, settings, state, message.chat.id, user)


@router.message(Edit.city, F.text)
async def save_city(
    message: Message,
    bot: Bot,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    try:
        city = clean_city(message.text or "")
    except ValidationError as error:
        await message.answer(f"⚠️ {error}")
        return
    await profiles_service.update(db, int(user["id"]), city=city)
    await state.set_state(None)
    await message.answer("✅ Город обновлён.")
    await show_profile(bot, db, settings, state, message.chat.id, user)


@router.message(Edit.bio, F.text)
async def save_bio(
    message: Message,
    bot: Bot,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    raw = (message.text or "").strip()
    if raw == "-":
        raw = ""
    elif has_contacts(raw):
        await message.answer(
            "⚠️ Ссылки, ники и номера телефонов в анкете запрещены — так мы держим бота "
            "чистым от рекламы. Расскажи лучше о себе."
        )
        return
    try:
        bio = clean_bio(raw)
    except ValidationError as error:
        await message.answer(f"⚠️ {error}")
        return

    user_id = int(user["id"])
    await profiles_service.update(db, user_id, bio=bio)
    profile = await profiles_service.get(db, user_id)
    status, reasons = await antifraud.screen_profile(db, user_id, profile or {})
    if status == MOD_HOLD:
        await profiles_service.set_moderation(db, user_id, status, "; ".join(reasons[:3]))
        await message.answer(
            "🔍 Описание отправлено на проверку модератору — в нём нашлись признаки рекламы. "
            "Анкета временно скрыта из поиска."
        )
    else:
        await profiles_service.set_moderation(db, user_id, status, "; ".join(reasons[:3]) or None)
        await message.answer("✅ Описание обновлено.")

    await state.set_state(None)
    await show_profile(bot, db, settings, state, message.chat.id, user)


# --------------------------------------------------------------------------- интересы


@router.callback_query(ProfileCB.filter(F.action == "interests"))
async def edit_interests(
    query: CallbackQuery, db: Database, user: dict[str, Any]
) -> None:
    profile = await profiles_service.get(db, int(user["id"]))
    chosen = [code for code in str((profile or {}).get("interests") or "").split(",") if code]
    await query.answer()
    if query.message:
        await query.message.answer(
            f"🎯 Выбери до {MAX_INTERESTS} тем:",
            reply_markup=inline.interests(chosen, editing=True),
        )


@router.callback_query(ProfileCB.filter(F.action == "edit_interest"))
async def toggle_interest(
    query: CallbackQuery, callback_data: ProfileCB, db: Database, user: dict[str, Any]
) -> None:
    user_id = int(user["id"])
    profile = await profiles_service.get(db, user_id)
    chosen = [code for code in str((profile or {}).get("interests") or "").split(",") if code]
    code = callback_data.value

    if code in chosen:
        chosen.remove(code)
    elif len(chosen) >= MAX_INTERESTS:
        await query.answer(f"Не больше {MAX_INTERESTS} тем", show_alert=True)
        return
    else:
        chosen.append(code)

    await profiles_service.update(db, user_id, interests=",".join(chosen))
    await query.answer()
    if query.message:
        await query.message.edit_reply_markup(
            reply_markup=inline.interests(chosen, editing=True)
        )


@router.callback_query(ProfileCB.filter(F.action == "interests_done"))
async def interests_done(
    query: CallbackQuery,
    bot: Bot,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    await query.answer("Сохранено")
    if query.message:
        await query.message.delete()
        await show_profile(bot, db, settings, state, query.message.chat.id, user)


# --------------------------------------------------------------------------- пауза


@router.callback_query(ProfileCB.filter(F.action.in_({"pause", "resume"})))
async def toggle_visibility(
    query: CallbackQuery,
    callback_data: ProfileCB,
    bot: Bot,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    visible = callback_data.action == "resume"
    await profiles_service.set_visible(db, int(user["id"]), visible)
    await query.answer()
    if query.message:
        await notify.send_message(
            bot,
            db,
            query.message.chat.id,
            texts.PROFILE_SHOWN if visible else texts.PROFILE_HIDDEN,
        )
        await show_profile(bot, db, settings, state, query.message.chat.id, user)


@router.message(Command("stop"))
async def cmd_stop(
    message: Message,
    bot: Bot,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    await profiles_service.set_visible(db, int(user["id"]), False)
    await message.answer(texts.PROFILE_HIDDEN)
    await show_profile(bot, db, settings, state, message.chat.id, user)


@router.message(Edit.name)
@router.message(Edit.age)
@router.message(Edit.city)
@router.message(Edit.bio)
async def edit_wrong_type(message: Message) -> None:
    await message.answer("Здесь нужен текст 🙂")
