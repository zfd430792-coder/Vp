"""Настройки пользователя: фильтры, уведомления, приватность, апелляции."""
from __future__ import annotations

from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app import texts, views
from app.callbacks import SettingsCB
from app.config import Config
from app.constants import MAX_AGE, MIN_AGE, RADIUS_ANY, RADIUS_CHOICES, RADIUS_CITY
from app.db import Database
from app.handlers import ui
from app.keyboards import inline
from app.services import export, moderation, notify
from app.services import profiles as profiles_service
from app.services import users as users_service
from app.states import Appeal, Edit
from app.utils.text import shorten

router = Router(name="settings")


@router.message(StateFilter(None), F.text == texts.BTN_SETTINGS)
@router.message(Command("settings"))
async def open_settings(
    message: Message, state: FSMContext, db: Database, user: dict[str, Any]
) -> None:
    await ui.leave_chat_mode(db, state, int(user["id"]))
    await message.answer(
        "⚙️ <b>Настройки</b>\n\nЗдесь можно настроить поиск, уведомления и приватность.",
        reply_markup=inline.settings_menu(),
    )


@router.callback_query(SettingsCB.filter(F.action == "menu"))
async def back_to_settings(query: CallbackQuery) -> None:
    await query.answer()
    if query.message:
        await query.message.edit_text(
            "⚙️ <b>Настройки</b>\n\nЗдесь можно настроить поиск, уведомления и приватность.",
            reply_markup=inline.settings_menu(),
        )


# --------------------------------------------------------------------------- фильтры


@router.callback_query(SettingsCB.filter(F.action == "filters"))
async def show_filters(query: CallbackQuery, db: Database, user: dict[str, Any]) -> None:
    await query.answer()
    profile = await profiles_service.get(db, int(user["id"]))
    if not profile or query.message is None:
        return
    await query.message.edit_text(
        views.preferences_text(profile), reply_markup=inline.filters_menu(profile)
    )


@router.callback_query(SettingsCB.filter(F.action == "seeking_menu"))
async def seeking_menu(query: CallbackQuery) -> None:
    await query.answer()
    if query.message:
        await query.message.edit_text(
            "👥 <b>Кого показывать в ленте?</b>", reply_markup=inline.seeking("settings")
        )


@router.callback_query(SettingsCB.filter(F.action == "seeking"))
async def set_seeking(
    query: CallbackQuery, callback_data: SettingsCB, db: Database, user: dict[str, Any]
) -> None:
    if callback_data.value not in {"m", "f", "any"}:
        await query.answer()
        return
    await profiles_service.update(db, int(user["id"]), seeking=callback_data.value)
    await query.answer("Сохранено")
    profile = await profiles_service.get(db, int(user["id"]))
    if profile and query.message:
        await query.message.edit_text(
            views.preferences_text(profile), reply_markup=inline.filters_menu(profile)
        )


@router.callback_query(SettingsCB.filter(F.action == "age_menu"))
async def age_menu(query: CallbackQuery) -> None:
    await query.answer()
    if query.message:
        await query.message.edit_text(
            "🎂 <b>Какой возраст показывать?</b>", reply_markup=inline.age_filter()
        )


@router.callback_query(SettingsCB.filter(F.action == "age_set"))
async def set_age_range(
    query: CallbackQuery, callback_data: SettingsCB, db: Database, user: dict[str, Any]
) -> None:
    try:
        low, high = (int(part) for part in callback_data.value.split("-", 1))
    except (ValueError, TypeError):
        await query.answer()
        return
    low = max(MIN_AGE, min(MAX_AGE, low))
    high = max(low, min(MAX_AGE, high))
    await profiles_service.update(db, int(user["id"]), age_min=low, age_max=high)
    await query.answer("Сохранено")
    profile = await profiles_service.get(db, int(user["id"]))
    if profile and query.message:
        await query.message.edit_text(
            views.preferences_text(profile), reply_markup=inline.filters_menu(profile)
        )


@router.callback_query(SettingsCB.filter(F.action == "age_manual"))
async def age_manual(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Edit.age_filter)
    await query.answer()
    if query.message:
        await query.message.answer(
            "Напиши диапазон возраста через дефис. Например: <code>22-34</code>"
        )


@router.message(Edit.age_filter, F.text)
async def save_age_range(
    message: Message, state: FSMContext, db: Database, user: dict[str, Any]
) -> None:
    raw = (message.text or "").replace("—", "-").replace(" ", "")
    parts = raw.split("-")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        await message.answer("Нужен формат <code>22-34</code>. Попробуй ещё раз.")
        return
    low, high = int(parts[0]), int(parts[1])
    if low < MIN_AGE or high > MAX_AGE or low > high:
        await message.answer(f"Диапазон должен быть от {MIN_AGE} до {MAX_AGE}, и «от» меньше «до».")
        return

    await profiles_service.update(db, int(user["id"]), age_min=low, age_max=high)
    await state.set_state(None)
    profile = await profiles_service.get(db, int(user["id"]))
    if profile:
        await message.answer(
            views.preferences_text(profile), reply_markup=inline.filters_menu(profile)
        )


@router.callback_query(SettingsCB.filter(F.action == "radius_menu"))
async def radius_menu(query: CallbackQuery, db: Database, user: dict[str, Any]) -> None:
    profile = await profiles_service.get(db, int(user["id"]))
    if not profile or query.message is None:
        await query.answer()
        return

    text = ["📍 <b>Где искать</b>", ""]
    if profile.get("lat") is None:
        text.append(
            "У анкеты нет координат, поэтому поиск по радиусу работать не будет. "
            "Укажи город из справочника или пришли местоположение в разделе «Моя анкета»."
        )
    else:
        text.append("Радиус считается от твоего города или присланной точки.")
        text.append("Люди, у которых радиус меньше, тебя не увидят — это честно в обе стороны.")
    await query.answer()
    await query.message.edit_text(
        "\n".join(text), reply_markup=inline.radius_menu(int(profile.get("search_radius") or 0))
    )


@router.callback_query(SettingsCB.filter(F.action == "radius"))
async def set_radius(
    query: CallbackQuery, callback_data: SettingsCB, db: Database, user: dict[str, Any]
) -> None:
    allowed = {value for value, _ in RADIUS_CHOICES}
    try:
        radius = int(callback_data.value)
    except (TypeError, ValueError):
        await query.answer()
        return
    if radius not in allowed:
        await query.answer()
        return

    profile = await profiles_service.get(db, int(user["id"]))
    if profile and radius not in (RADIUS_CITY, RADIUS_ANY) and profile.get("lat") is None:
        await query.answer(
            "Для поиска по радиусу нужны координаты: укажи город из справочника "
            "или пришли местоположение.",
            show_alert=True,
        )
        return

    await profiles_service.update(db, int(user["id"]), search_radius=radius)
    await query.answer("Сохранено")
    profile = await profiles_service.get(db, int(user["id"]))
    if profile and query.message:
        await query.message.edit_text(
            views.preferences_text(profile), reply_markup=inline.filters_menu(profile)
        )


# --------------------------------------------------------------------------- уведомления


@router.callback_query(SettingsCB.filter(F.action == "toggle_verified"))
async def toggle_verified(query: CallbackQuery, db: Database, user: dict[str, Any]) -> None:
    profile = await profiles_service.get(db, int(user["id"]))
    if not profile:
        await query.answer()
        return
    new_value = 0 if profile.get("only_verified") else 1
    await profiles_service.update(db, int(user["id"]), only_verified=new_value)
    if new_value:
        await query.answer(
            "Показываем только анкеты с ✅. Их меньше, зато они проверены живым селфи.",
            show_alert=True,
        )
    else:
        await query.answer("Показываем все анкеты")
    profile = await profiles_service.get(db, int(user["id"]))
    if profile and query.message:
        await query.message.edit_text(
            views.preferences_text(profile), reply_markup=inline.filters_menu(profile)
        )


@router.callback_query(SettingsCB.filter(F.action == "notify"))
async def notify_menu(query: CallbackQuery, db: Database, user: dict[str, Any]) -> None:
    await query.answer()
    fresh = await users_service.get(db, int(user["id"])) or user
    if query.message:
        await query.message.edit_text(
            views.notifications_text(fresh), reply_markup=inline.notify_menu(fresh)
        )


@router.callback_query(SettingsCB.filter(F.action == "toggle_notify"))
async def toggle_notify(
    query: CallbackQuery, callback_data: SettingsCB, db: Database, user: dict[str, Any]
) -> None:
    columns = {
        "likes": "notify_likes",
        "matches": "notify_matches",
        "messages": "notify_messages",
    }
    column = columns.get(callback_data.value)
    if not column:
        await query.answer()
        return
    await db.execute(
        f"UPDATE users SET {column} = CASE WHEN {column} = 1 THEN 0 ELSE 1 END WHERE id = ?",
        (int(user["id"]),),
    )
    fresh = await users_service.get(db, int(user["id"])) or user
    await query.answer("Сохранено")
    if query.message:
        await query.message.edit_text(
            views.notifications_text(fresh), reply_markup=inline.notify_menu(fresh)
        )


@router.callback_query(SettingsCB.filter(F.action == "safety"))
async def show_safety(query: CallbackQuery) -> None:
    await query.answer()
    if query.message:
        await query.message.answer(texts.SAFETY)


# --------------------------------------------------------------------------- апелляция


@router.callback_query(SettingsCB.filter(F.action == "appeal"))
async def start_appeal(
    query: CallbackQuery, state: FSMContext, db: Database, user: dict[str, Any]
) -> None:
    await query.answer()
    if await moderation.has_open_appeal(db, int(user["id"])):
        if query.message:
            await query.message.answer(texts.APPEAL_EXISTS)
        return
    await state.set_state(Appeal.text)
    if query.message:
        await query.message.answer(texts.APPEAL_ASK)


@router.message(Appeal.text, F.text)
async def save_appeal(
    message: Message,
    bot: Bot,
    state: FSMContext,
    db: Database,
    config: Config,
    user: dict[str, Any],
) -> None:
    text = shorten(message.text or "", 1000)
    if len(text) < 10:
        await message.answer("Опиши ситуацию чуть подробнее — так модератору будет понятнее.")
        return

    appeal_id = await moderation.create_appeal(db, int(user["id"]), text)
    await state.set_state(None)
    await message.answer(texts.APPEAL_SENT.format(appeal_id=appeal_id))
    await notify.notify_staff(
        bot,
        db,
        f"📨 <b>Новая апелляция №{appeal_id}</b>\nОт: <code>{user['id']}</code>",
        chat_id=config.moderation_chat_id,
    )


@router.message(Appeal.text)
async def appeal_wrong_type(message: Message) -> None:
    await message.answer("Опиши ситуацию текстом, пожалуйста.")


@router.callback_query(SettingsCB.filter(F.action == "export"))
async def export_data(query: CallbackQuery, db: Database, user: dict[str, Any]) -> None:
    """Отдаёт человеку всё, что бот о нём хранит, одним файлом."""
    await query.answer()
    if query.message is None:
        return
    payload = await export.to_json(db, int(user["id"]))
    if payload is None:
        await query.message.answer(texts.EXPORT_EMPTY)
        return
    await query.message.answer_document(
        BufferedInputFile(payload, filename=f"my-data-{user['id']}.json"),
        caption=texts.EXPORT_READY,
    )


# --------------------------------------------------------------------------- удаление данных


@router.callback_query(SettingsCB.filter(F.action == "delete"))
async def confirm_delete(query: CallbackQuery) -> None:
    await query.answer()
    if query.message:
        await query.message.edit_text(texts.DELETE_CONFIRM, reply_markup=inline.confirm_delete())


@router.message(Command("delete"))
async def cmd_delete(message: Message, state: FSMContext) -> None:
    await state.set_state(None)
    await message.answer(texts.DELETE_CONFIRM, reply_markup=inline.confirm_delete())


@router.callback_query(SettingsCB.filter(F.action == "delete_yes"))
async def do_delete(
    query: CallbackQuery, state: FSMContext, db: Database, user: dict[str, Any]
) -> None:
    from app.keyboards import reply as reply_kb

    await users_service.delete_account(db, int(user["id"]))
    await state.clear()
    await query.answer("Анкета удалена")
    if query.message:
        await query.message.edit_text(texts.DELETE_DONE)
        await query.message.answer("Меню скрыто.", reply_markup=reply_kb.remove)
