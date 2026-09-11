"""Пошаговое создание анкеты."""
from __future__ import annotations

from typing import Any

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import texts
from app.callbacks import RegCB
from app.config import Config
from app.constants import MAX_INTERESTS, MAX_PHOTOS, MOD_HOLD, MOD_OK, MOD_REVIEW
from app.db import Database
from app.handlers import ui
from app.keyboards import inline
from app.services import antifraud, notify
from app.services import profiles as profiles_service
from app.services import render
from app.services import users as users_service
from app.services.settings import Settings
from app.states import Reg
from app.utils.text import ValidationError, clean_age, clean_bio, clean_city, clean_name, esc, has_contacts, normalize_city

router = Router(name="registration")



async def preload(state: FSMContext, profile: dict[str, Any]) -> None:
    """Переносит уже сохранённые поля анкеты в состояние диалога."""
    interests = [code for code in str(profile.get("interests") or "").split(",") if code]
    await state.update_data(
        reg_name=profile.get("name"),
        reg_age=profile.get("age"),
        reg_gender=profile.get("gender"),
        reg_seeking=profile.get("seeking"),
        reg_city=profile.get("city"),
        reg_bio=profile.get("bio") or "",
        reg_interests=interests,
    )


async def resume(
    message: Message, state: FSMContext, db: Database, profile: dict[str, Any]
) -> bool:
    """Продолжает анкету с того места, где она оборвалась.

    Возвращает False, если продолжать нечего (анкета пустая).
    """
    if not profile.get("name"):
        return False

    await preload(state, profile)
    photos = await profiles_service.count_photos(db, int(profile["user_id"]))

    if not profile.get("age"):
        await state.set_state(Reg.age)
        await message.answer(texts.ASK_AGE)
    elif not profile.get("gender"):
        await state.set_state(Reg.gender)
        await message.answer(texts.ASK_GENDER, reply_markup=inline.gender())
    elif not profile.get("city"):
        await state.set_state(Reg.city)
        await message.answer(texts.ASK_CITY)
    elif not photos:
        await state.set_state(Reg.photos)
        await state.update_data(reg_photos=[])
        await message.answer(
            "📷 <b>В анкете не хватает фотографии</b>\n\n"
            "Остальное уже сохранено — пришли фото, и анкета вернётся в поиск."
        )
    else:
        return False
    return True


@router.message(Reg.name, F.text)
async def step_name(message: Message, state: FSMContext) -> None:
    try:
        name = clean_name(message.text or "")
    except ValidationError as error:
        await message.answer(f"⚠️ {error}")
        return
    await state.update_data(reg_name=name)
    await state.set_state(Reg.age)
    await message.answer(texts.ASK_AGE)


@router.message(Reg.age, F.text)
async def step_age(message: Message, state: FSMContext, db: Database, user: dict[str, Any]) -> None:
    try:
        age = clean_age(message.text or "")
    except ValidationError as error:
        await message.answer(f"⚠️ {error}")
        if "18+" in str(error):
            await antifraud.log_event(db, int(user["id"]), "underage_attempt", weight=0)
        return
    await state.update_data(reg_age=age)
    await state.set_state(Reg.gender)
    await message.answer(texts.ASK_GENDER, reply_markup=inline.gender())


@router.callback_query(Reg.gender, RegCB.filter(F.action == "gender"))
async def step_gender(query: CallbackQuery, callback_data: RegCB, state: FSMContext) -> None:
    if callback_data.value not in {"m", "f"}:
        await query.answer()
        return
    await state.update_data(reg_gender=callback_data.value)
    await state.set_state(Reg.seeking)
    await query.answer()
    if query.message:
        await query.message.edit_text(texts.ASK_SEEKING, reply_markup=inline.seeking())


@router.callback_query(Reg.seeking, RegCB.filter(F.action == "seeking"))
async def step_seeking(query: CallbackQuery, callback_data: RegCB, state: FSMContext) -> None:
    if callback_data.value not in {"m", "f", "any"}:
        await query.answer()
        return
    await state.update_data(reg_seeking=callback_data.value)
    await state.set_state(Reg.city)
    await query.answer()
    if query.message:
        await query.message.edit_text(texts.ASK_CITY)


@router.message(Reg.city, F.text)
async def step_city(message: Message, state: FSMContext) -> None:
    try:
        city = clean_city(message.text or "")
    except ValidationError as error:
        await message.answer(f"⚠️ {error}")
        return
    await state.update_data(reg_city=city)
    await state.set_state(Reg.interests)
    await message.answer(texts.ASK_INTERESTS, reply_markup=inline.interests(set()))


@router.callback_query(Reg.interests, RegCB.filter(F.action == "interest"))
async def step_interests(query: CallbackQuery, callback_data: RegCB, state: FSMContext) -> None:
    data = await state.get_data()
    chosen: list[str] = list(data.get("reg_interests") or [])
    code = callback_data.value

    if code in chosen:
        chosen.remove(code)
    elif len(chosen) >= MAX_INTERESTS:
        await query.answer(f"Можно выбрать не больше {MAX_INTERESTS} тем", show_alert=True)
        return
    else:
        chosen.append(code)

    await state.update_data(reg_interests=chosen)
    await query.answer()
    if query.message:
        await query.message.edit_reply_markup(reply_markup=inline.interests(chosen))


@router.callback_query(Reg.interests, RegCB.filter(F.action == "interests_done"))
async def step_interests_done(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Reg.bio)
    await query.answer()
    if query.message:
        await query.message.edit_text(texts.ASK_BIO, reply_markup=inline.skip_step("bio_skip"))


@router.callback_query(Reg.bio, RegCB.filter(F.action == "bio_skip"))
async def step_bio_skip(query: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(reg_bio="")
    await state.set_state(Reg.photos)
    await query.answer()
    if query.message:
        await query.message.edit_text(texts.ASK_BIO)
        await query.message.answer(texts.ASK_PHOTO)


@router.message(Reg.bio, F.text)
async def step_bio(message: Message, state: FSMContext) -> None:
    raw = message.text or ""
    if has_contacts(raw):
        await message.answer(
            "⚠️ В анкете нельзя оставлять ссылки, ники и номера телефонов.\n"
            "Так мы держим бота чистым от рекламы и мошенников. Расскажи лучше о себе 🙂"
        )
        return
    try:
        bio = clean_bio(raw)
    except ValidationError as error:
        await message.answer(f"⚠️ {error}")
        return
    await state.update_data(reg_bio=bio)
    await state.set_state(Reg.photos)
    await message.answer(texts.ASK_PHOTO)


@router.message(Reg.photos, F.photo)
async def step_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photos: list[dict[str, str]] = list(data.get("reg_photos") or [])
    if len(photos) >= MAX_PHOTOS:
        await message.answer(f"Больше {MAX_PHOTOS} фото не нужно. Нажми «Готово» ниже 👇")
        return

    best = message.photo[-1]
    if any(item["unique"] == best.file_unique_id for item in photos):
        await message.answer("Это фото уже добавлено. Пришли другое или нажми «Готово».")
        return

    photos.append({"file_id": best.file_id, "unique": best.file_unique_id})
    await state.update_data(reg_photos=photos)

    left = MAX_PHOTOS - len(photos)
    text = f"📷 Добавлено фото: {len(photos)} из {MAX_PHOTOS}."
    text += f"\nМожно прислать ещё {left} или нажать «Готово»." if left else "\nЭто максимум."
    await message.answer(text, reply_markup=inline.photos_done(len(photos)))


@router.message(Reg.photos, F.video | F.document | F.animation)
async def step_photo_wrong(message: Message) -> None:
    await message.answer("Нужна именно фотография — пришли её как фото, не файлом 🙂")


@router.callback_query(Reg.photos, RegCB.filter(F.action == "photos_done"))
async def step_photos_done(
    query: CallbackQuery, state: FSMContext, db: Database, user: dict[str, Any]
) -> None:
    data = await state.get_data()
    photos = list(data.get("reg_photos") or [])
    if not photos:
        await query.answer("Нужна хотя бы одна фотография", show_alert=True)
        return

    await query.answer()
    await state.set_state(Reg.preview)

    card = {
        "name": data.get("reg_name"),
        "age": data.get("reg_age"),
        "city": data.get("reg_city"),
        "bio": data.get("reg_bio") or "",
        "interests": ",".join(data.get("reg_interests") or []),
        "photos": [{"file_id": item["file_id"]} for item in photos],
    }
    if query.message:
        await query.message.answer(texts.PROFILE_PREVIEW)
        await render.send_card(
            query.bot,
            db,
            query.message.chat.id,
            card,
            keyboard=inline.preview(),
            show_activity=False,
        )


@router.callback_query(Reg.preview, RegCB.filter(F.action == "restart"))
async def step_restart(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Reg.name)
    await state.update_data(reg_photos=[], reg_interests=[])
    await query.answer()
    if query.message:
        await query.message.answer(texts.ASK_NAME)


@router.callback_query(Reg.preview, RegCB.filter(F.action == "publish"))
async def step_publish(
    query: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    db: Database,
    settings: Settings,
    config: Config,
    user: dict[str, Any],
) -> None:
    data = await state.get_data()
    photos = list(data.get("reg_photos") or [])
    if not photos or not data.get("reg_name"):
        await query.answer("Данные потерялись, начни заново: /start", show_alert=True)
        await state.clear()
        return

    user_id = int(user["id"])
    await query.answer()

    await profiles_service.ensure(db, user_id)
    await profiles_service.update(
        db,
        user_id,
        name=data.get("reg_name"),
        age=int(data.get("reg_age") or 18),
        gender=data.get("reg_gender") or "m",
        seeking=data.get("reg_seeking") or "any",
        city=data.get("reg_city"),
        city_norm=normalize_city(str(data.get("reg_city") or "")),
        bio=data.get("reg_bio") or "",
        interests=",".join(data.get("reg_interests") or []),
        is_visible=1,
        moderation=MOD_OK,
        moderation_note=None,
    )

    await profiles_service.clear_photos(db, user_id)
    for item in photos:
        await profiles_service.add_photo(db, user_id, item["file_id"], item["unique"])
        await antifraud.screen_photo(db, user_id, item["unique"])

    await profiles_service.mark_complete(db, user_id)
    profile = await profiles_service.get(db, user_id)
    status, reasons = await antifraud.screen_profile(db, user_id, profile or {})
    if status != MOD_OK:
        await profiles_service.set_moderation(db, user_id, status, "; ".join(reasons[:3]) or None)
    await users_service.recompute_trust(db, user_id)

    await state.clear()
    chat_id = query.message.chat.id if query.message else user_id

    if status == MOD_HOLD:
        await notify.send_message(
            bot,
            db,
            chat_id,
            "🔍 <b>Анкета отправлена на проверку</b>\n\n"
            "Автоматика заметила кое-что необычное, поэтому анкету посмотрит живой модератор — "
            "обычно это занимает несколько часов.\n\n"
            f"Что смутило: <i>{esc('; '.join(reasons[:3]) or 'нужна ручная проверка')}</i>\n\n"
            "Это не блокировка. Как только проверка пройдёт, анкета появится в поиске, "
            "и мы сообщим об этом здесь.",
        )
        await notify.notify_staff(
            bot,
            db,
            f"🔍 Новая анкета на проверке: <code>{user_id}</code>\n"
            f"Причина: {esc('; '.join(reasons[:3]))}",
            chat_id=config.moderation_chat_id,
        )
    else:
        if status == MOD_REVIEW:
            await notify.notify_staff(
                bot,
                db,
                f"👀 Анкета в очереди на проверку: <code>{user_id}</code>\n"
                f"Сигналы: {esc('; '.join(reasons[:3]))}",
                chat_id=config.moderation_chat_id,
            )
        await notify.send_message(bot, db, chat_id, texts.PROFILE_SAVED)

    fresh_user = await users_service.get(db, user_id) or user
    await ui.show_menu(bot, db, chat_id, fresh_user, texts.MENU)


@router.message(Reg.name)
@router.message(Reg.age)
@router.message(Reg.city)
@router.message(Reg.bio)
async def step_wrong_type(message: Message) -> None:
    await message.answer("Здесь нужен текст 🙂 Напиши ответ сообщением.")


@router.message(Reg.photos)
async def step_photo_needed(message: Message) -> None:
    await message.answer(f"Пришли фотографию (до {MAX_PHOTOS} штук) или нажми «Готово».")


@router.message(Reg.gender)
@router.message(Reg.seeking)
@router.message(Reg.interests)
@router.message(Reg.preview)
async def step_use_buttons(message: Message) -> None:
    await message.answer("Выбери вариант кнопкой выше 👆")
