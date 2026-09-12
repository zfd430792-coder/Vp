"""Админ-панель: жалобы, пользователи, антифрод, настройки, рассылка."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto, Message

from app import texts, views
from app.callbacks import AdminCB
from app.constants import (
    DAY,
    DEFAULT_SETTINGS,
    HOUR,
    MOD_HOLD,
    MOD_OK,
    REPORT_CATEGORIES,
    ROLE_MODERATOR,
    ROLE_OWNER,
    ROLE_USER,
    SHADOW_TITLES,
)
from app.db import Database
from app.filters import IsStaff
from app.keyboards import inline
from app.services import antifraud, moderation, notify, render, stats, verification
from app.services import chat as chat_service
from app.services import likes as likes_service
from app.services import profiles as profiles_service
from app.services import users as users_service
from app.services.settings import Settings
from app.states import Admin
from app.utils.keyboard_utils import page_bounds
from app.utils.text import esc, human_delta, shorten
from app.utils.time import fmt_dt, now

log = logging.getLogger(__name__)

router = Router(name="admin")
router.message.filter(IsStaff())
router.callback_query.filter(IsStaff())

BAN_DURATIONS: dict[str, int | None] = {
    "1h": HOUR,
    "1d": DAY,
    "7d": 7 * DAY,
    "30d": 30 * DAY,
    "perm": None,
}
BAN_TITLES = {"1h": "1 час", "1d": "1 день", "7d": "7 дней", "30d": "30 дней", "perm": "навсегда"}

SETTING_TITLES = {
    "likes_per_day": "❤️ Лайков в сутки",
    "likes_per_day_new": "🌱 Лайков новичкам",
    "superlikes_per_day": "💥 Суперлайков в сутки",
    "pass_ttl_days": "🔁 Возврат пропущенных, дней",
    "inactive_days": "😴 Скрывать неактивных, дней",
    "reports_for_review": "🔍 Жалоб до проверки",
    "reports_for_shadow": "🔅 Жалоб до снижения охвата",
    "reports_for_hold": "🙈 Жалоб до скрытия анкеты",
    "shadow_hours": "⏳ Часов ограничения охвата",
    "registration_open": "🚪 Регистрация (1/0)",
    "captcha_enabled": "🤖 Капча (1/0)",
    "reg_burst_limit": "📈 Регистраций в час до капчи",
    "report_sla_hours": "⏰ Часов на разбор жалобы",
    "message_keep_days": "🗄 Хранить переписку, дней",
    "min_trust_for_full_limits": "🤝 Доверие для полных лимитов",
    "trust_for_bonus": "⭐️ Доверие для надбавки к лимиту",
    "verification_enabled": "✅ Верификация анкет (1/0)",
    "fresh_profile_boost_days": "🌱 Дней приоритета новым анкетам",
    "nudge_enabled": "💌 Напоминания о лайках (1/0)",
    "nudge_after_days": "😴 Дней молчания до напоминания",
    "nudge_every_days": "🔁 Не чаще одного раза в дней",
    "nudge_batch": "📦 Напоминаний за проход",
}


# --------------------------------------------------------------------------- утилиты


async def _show(query: CallbackQuery, text: str, markup: Any = None) -> None:
    """Меняет текст сообщения, а если это медиа — присылает новое."""
    if query.message is None:
        return
    if query.message.text is not None:
        try:
            await query.message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as error:
            if "message is not modified" in str(error):
                return
    await query.message.answer(text, reply_markup=markup)


async def _menu_text(db: Database, settings: Settings) -> tuple[str, Any]:
    reports = await moderation.queue_count(db)
    appeals = await moderation.appeals_count(db)
    profiles_pending = await moderation.profile_queue_count(db)
    verify_pending = await verification.queue_count(db)
    flagged = await antifraud.flagged_count(db)
    overdue = await moderation.overdue_reports(db, settings)

    text = ["🛠 <b>Админ-панель</b>", ""]
    if overdue:
        text.append(
            f"⏰ <b>{overdue} {'жалоба ждёт' if overdue == 1 else 'жалоб ждут'} дольше "
            f"{settings.get_int('report_sla_hours', 6)} ч — разберите в первую очередь.</b>"
        )
        text.append("")
    text.append(f"🚩 Открытых жалоб: <b>{reports}</b>")
    text.append(f"📨 Апелляций: <b>{appeals}</b>")
    text.append(f"🔍 Анкет на проверке: <b>{profiles_pending}</b>")
    text.append(f"✅ Заявок на верификацию: <b>{verify_pending}</b>")
    text.append(f"🤖 Подозрительных аккаунтов: <b>{flagged}</b>")
    markup = inline.admin_menu(
        reports=reports,
        appeals=appeals,
        moderation=profiles_pending,
        verify=verify_pending,
        flagged=flagged,
        overdue=overdue,
    )
    return "\n".join(text), markup


async def _user_card(db: Database, target_id: int) -> tuple[str, Any] | None:
    card = await profiles_service.card(db, target_id)
    if card is None:
        user = await users_service.get(db, target_id)
        if user is None:
            return None
        card = dict(user)
        card["user_id"] = user["id"]
        card["user_created_at"] = user["created_at"]

    risk = await antifraud.risk_score(db, target_id)
    events = await antifraud.user_events(db, target_id, limit=6)
    signals = [f"{event['kind']} ({fmt_dt(event['created_at'])})" for event in events]
    text = views.admin_user_card(card, risk=risk, signals=signals)

    user = await users_service.get(db, target_id)
    queue_size = await moderation.queue_count(db)
    markup = inline.user_actions(
        card,
        banned=users_service.is_banned(user or {}),
        shadowed=users_service.is_shadowed(user or {}),
        queue_size=queue_size,
        photos_blocked=await antifraud.has_banned_photos(db, target_id),
    )
    return text, markup


async def _reason_from_reports(db: Database, target_id: int) -> str:
    rows = await db.fetchall(
        "SELECT DISTINCT category FROM reports WHERE target_id = ? AND status IN ('open','in_review')",
        (target_id,),
    )
    if not rows:
        return "нарушение правил"
    names = [
        REPORT_CATEGORIES.get(str(row["category"]), "нарушение").split(" ", 1)[-1].lower()
        for row in rows
    ]
    return ", ".join(dict.fromkeys(names))[:150]


async def _close_reports(
    bot: Bot,
    db: Database,
    *,
    admin_id: int,
    target_id: int,
    resolution: str,
    status: str = "resolved",
) -> int:
    """Закрывает жалобы и сообщает каждому жалобщику результат."""
    closed = await moderation.close_reports(
        db, target_id, admin_id=admin_id, status=status, resolution=resolution
    )
    for row in closed:
        await notify.send_message(
            bot,
            db,
            row["reporter_id"],
            texts.REPORT_RESOLVED_NOTICE.format(
                report_id=row["id"], resolution=esc(resolution)
            ),
        )
        await asyncio.sleep(0.05)
    return len(closed)


# --------------------------------------------------------------------------- вход


@router.message(Command("admin"))
@router.message(StateFilter(None), F.text == texts.BTN_ADMIN)
async def admin_menu(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    chat_service.close_chat(int(user["id"]))
    await state.set_state(None)
    text, markup = await _menu_text(db, settings)
    await message.answer(text, reply_markup=markup)


@router.callback_query(AdminCB.filter(F.action == "menu"))
async def admin_menu_cb(
    query: CallbackQuery, state: FSMContext, db: Database, settings: Settings
) -> None:
    await state.set_state(None)
    await query.answer()
    text, markup = await _menu_text(db, settings)
    await _show(query, text, markup)


@router.callback_query(AdminCB.filter(F.action == "noop"))
async def admin_noop(query: CallbackQuery) -> None:
    await query.answer()


# --------------------------------------------------------------------------- статистика


@router.callback_query(AdminCB.filter(F.action == "stats"))
async def admin_stats(query: CallbackQuery, db: Database) -> None:
    await query.answer()
    data = await stats.overview(db)
    sources = await stats.top_sources(db)

    lines = [
        "📊 <b>Статистика</b>",
        "",
        "<b>Люди</b>",
        f"Всего: {data['users_total']} · активны за сутки: {data['users_active_day']}",
        f"Новые сегодня: {data['users_new_today']} · за неделю: {data['users_new_week']}",
        f"Регистраций за час: {data['registrations_hour']}",
        f"Заблокировали бота: {data['bot_blocked']}",
        "",
        "<b>Анкеты</b>",
        f"Заполнено: {data['profiles_complete']} · на паузе: {data['profiles_hidden']}",
        f"На проверке: {data['profiles_moderation']}",
        "",
        "<b>Активность за сутки</b>",
        f"Лайков: {data['likes_day']} · пропусков: {data['passes_day']}",
        f"Симпатий: {data['matches_day']} (всего {data['matches_total']})",
        f"Сообщений: {data['messages_day']}",
        "",
        "<b>Модерация</b>",
        f"Открытых жалоб: {data['reports_open']} · за сутки: {data['reports_day']}",
        f"Решено за неделю: {data['reports_resolved_week']}",
        f"Апелляций открыто: {data['appeals_open']}",
        f"Блокировок: {data['bans_active']} · ограничений охвата: {data['shadow_active']}",
        f"Подтверждённых анкет: {data['verified']} · в стоп-листе: {await antifraud.stoplist_size(db)}",
    ]
    if sources:
        lines.append("")
        lines.append("<b>Источники за неделю</b>")
        for row in sources:
            lines.append(f"• {esc(row['source'])}: {row['total']}")

    await _show(query, "\n".join(lines), inline.admin_back())


# --------------------------------------------------------------------------- жалобы


async def _render_report(query: CallbackQuery, db: Database, page: int) -> None:
    total = await moderation.queue_count(db)
    if not total:
        await _show(
            query,
            "✅ <b>Очередь пуста</b>\n\nВсе жалобы разобраны. Так держать!",
            inline.admin_back(),
        )
        return

    page = max(0, min(page, total - 1))
    rows = await moderation.queue(db, limit=1, offset=page)
    if not rows:
        await _show(query, "✅ Очередь пуста.", inline.admin_back())
        return

    report = rows[0]
    unique, weight = await moderation.report_pressure(db, int(report["target_id"]))
    text = views.report_card(report, position=page + 1, total=total)
    text += f"\n\n📈 Жалобщиков за 30 дней: <b>{unique}</b> (вес {weight})"

    await _show(
        query,
        text,
        inline.report_actions(int(report["id"]), int(report["target_id"]), page=page),
    )


@router.callback_query(AdminCB.filter(F.action == "reports"))
async def admin_reports(query: CallbackQuery, callback_data: AdminCB, db: Database) -> None:
    await query.answer()
    await _render_report(query, db, callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "rep_open"))
async def admin_report_open(
    query: CallbackQuery, callback_data: AdminCB, db: Database
) -> None:
    await query.answer()
    report = await moderation.get_report(db, callback_data.target)
    if not report:
        await _show(query, "Жалоба не найдена.", inline.admin_back())
        return
    if report["status"] != "open":
        await _show(
            query,
            f"Жалоба №{report['id']} уже обработана "
            f"({esc(str(report.get('resolution') or report['status']))}).",
            inline.admin_back(),
        )
        return

    position = int(
        await db.fetchval(
            "SELECT COUNT(*) FROM reports WHERE status = 'open' AND "
            "(priority > ? OR (priority = ? AND created_at < ?))",
            (report["priority"], report["priority"], report["created_at"]),
            0,
        )
    )
    await _render_report(query, db, position)


@router.callback_query(AdminCB.filter(F.action == "rep_profile"))
async def admin_report_profile(
    query: CallbackQuery, callback_data: AdminCB, bot: Bot, db: Database
) -> None:
    await query.answer()
    report = await moderation.get_report(db, callback_data.target)
    if not report or query.message is None:
        return
    card = await profiles_service.card(db, int(report["target_id"]))
    if not card:
        await query.message.answer("У пользователя нет анкеты.")
        return
    await render.send_card(
        bot,
        db,
        query.message.chat.id,
        card,
        keyboard=inline.report_actions(
            int(report["id"]), int(report["target_id"]), page=callback_data.page
        ),
        header="👤 <b>Анкета нарушителя</b>",
    )


@router.callback_query(AdminCB.filter(F.action == "rep_chat"))
async def admin_report_chat(
    query: CallbackQuery, callback_data: AdminCB, bot: Bot, db: Database
) -> None:
    await query.answer()
    report = await moderation.get_report(db, callback_data.target)
    if not report or query.message is None:
        return

    match_id = report.get("match_id")
    if not match_id:
        match = await likes_service.match_between(
            db, int(report["reporter_id"]), int(report["target_id"])
        )
        match_id = int(match["id"]) if match else None
    if not match_id:
        await query.message.answer("Переписки между этими людьми нет.")
        return

    history = await chat_service.history(db, int(match_id), limit=25)
    names: dict[int, str] = {}
    for user_id in (int(report["reporter_id"]), int(report["target_id"])):
        profile = await profiles_service.get(db, user_id)
        label = str((profile or {}).get("name") or user_id)
        names[user_id] = (
            f"{label} (жалобщик)" if user_id == int(report["reporter_id"]) else f"{label} (нарушитель)"
        )

    media = await chat_service.media_from_history(db, int(match_id))
    markup = None
    if media:
        builder_markup = inline.report_actions(
            int(report["id"]), int(report["target_id"]), page=callback_data.page
        )
        markup = builder_markup
    await query.message.answer(
        views.chat_history_text(history, names)
        + (f"\n\n📎 Вложений в переписке: {len(media)}" if media else ""),
        reply_markup=markup,
    )
    for item in media[:5]:
        kind = str(item["kind"])
        file_id = str(item["file_id"])
        try:
            if kind == "photo":
                await bot.send_photo(query.message.chat.id, file_id)
            elif kind == "video":
                await bot.send_video(query.message.chat.id, file_id)
            elif kind == "voice":
                await bot.send_voice(query.message.chat.id, file_id)
            elif kind == "video_note":
                await bot.send_video_note(query.message.chat.id, file_id)
            elif kind == "animation":
                await bot.send_animation(query.message.chat.id, file_id)
            else:
                continue
        except TelegramBadRequest:
            continue
        await asyncio.sleep(0.1)


@router.callback_query(AdminCB.filter(F.action == "rep_reject"))
async def admin_report_reject(
    query: CallbackQuery,
    callback_data: AdminCB,
    bot: Bot,
    db: Database,
    user: dict[str, Any],
) -> None:
    report = await moderation.get_report(db, callback_data.target)
    if not report:
        await query.answer("Жалоба не найдена", show_alert=True)
        return

    target_id = int(report["target_id"])
    await moderation.resolve_report(
        db,
        int(report["id"]),
        admin_id=int(user["id"]),
        status="rejected",
        resolution="нарушений не найдено",
    )
    still_open = await db.fetchval(
        "SELECT COUNT(*) FROM reports WHERE target_id = ? AND status = 'open'", (target_id,), 0
    )
    if not int(still_open):
        profile = await profiles_service.get(db, target_id)
        if profile and profile.get("moderation") != MOD_OK:
            await profiles_service.set_moderation(db, target_id, MOD_OK, None)
        target_user = await users_service.get(db, target_id)
        if target_user and users_service.is_shadowed(target_user):
            await users_service.clear_shadow(db, target_id)
            await notify.send_message(bot, db, target_id, texts.SHADOW_LIFTED)

    await moderation.log_action(
        db, int(user["id"]), "report_rejected", target_id=target_id, details=report["id"]
    )
    await users_service.recompute_trust(db, target_id)
    await query.answer("Жалоба отклонена")
    await _render_report(query, db, callback_data.page)


# --------------------------------------------------------------------------- меры


@router.callback_query(AdminCB.filter(F.action == "warn"))
async def admin_warn_start(
    query: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    await state.set_state(Admin.warn_reason)
    await state.update_data(adm_target=callback_data.target, adm_page=callback_data.page)
    await query.answer()
    if query.message:
        await query.message.answer(
            "✍️ Напиши текст предупреждения — его увидит пользователь.\n"
            "Отправь «-», чтобы использовать стандартную формулировку."
        )


@router.message(Admin.warn_reason, F.text)
async def admin_warn_apply(
    message: Message,
    bot: Bot,
    state: FSMContext,
    db: Database,
    user: dict[str, Any],
) -> None:
    data = await state.get_data()
    target_id = int(data.get("adm_target") or 0)
    await state.set_state(None)
    if not target_id:
        await message.answer(texts.ERROR_GENERIC)
        return

    reason = (message.text or "").strip()
    if reason == "-":
        reason = (
            "Нам поступили жалобы на твоё поведение. Пожалуйста, перечитай правила — "
            "следующее нарушение приведёт к ограничению доступа."
        )
    reason = shorten(reason, 600)

    warns = await users_service.add_warn(db, target_id)
    await notify.send_message(
        bot, db, target_id, texts.WARN_NOTICE.format(reason=esc(reason), warns=warns)
    )
    await _close_reports(
        bot,
        db,
        admin_id=int(user["id"]),
        target_id=target_id,
        resolution="пользователь получил предупреждение",
    )
    await moderation.log_action(
        db, int(user["id"]), "warn", target_id=target_id, details=reason[:200]
    )
    await users_service.recompute_trust(db, target_id)

    await message.answer(f"⚠️ Предупреждение отправлено. Всего у пользователя: {warns}.")
    card = await _user_card(db, target_id)
    if card:
        await message.answer(card[0], reply_markup=card[1])


@router.callback_query(AdminCB.filter(F.action == "shadow_menu"))
async def admin_shadow_menu(query: CallbackQuery, callback_data: AdminCB) -> None:
    await query.answer()
    await _show(
        query,
        "🔅 <b>Снижение охвата</b>\n\n"
        "Анкета останется видимой, но будет реже попадать в ленту. "
        "Люди, которые уже лайкнули человека, продолжат его видеть.\n\n"
        "Выбери уровень:",
        inline.shadow_menu(callback_data.target, page=callback_data.page),
    )


@router.callback_query(AdminCB.filter(F.action == "shadow"))
async def admin_shadow_apply(
    query: CallbackQuery,
    callback_data: AdminCB,
    bot: Bot,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    target_id = callback_data.target
    try:
        level = int(callback_data.value)
    except ValueError:
        await query.answer()
        return

    hours = max(1, settings.get_int("shadow_hours", 24)) * level
    reason = await _reason_from_reports(db, target_id)
    await users_service.set_shadow(db, target_id, level=level, hours=hours, reason=reason)
    await _close_reports(
        bot,
        db,
        admin_id=int(user["id"]),
        target_id=target_id,
        resolution=f"охват анкеты снижен на {hours} ч",
    )
    await moderation.log_action(
        db,
        int(user["id"]),
        "shadow",
        target_id=target_id,
        details={"level": level, "hours": hours, "reason": reason},
    )

    await notify.send_message(
        bot,
        db,
        target_id,
        texts.SHADOW_NOTICE.format(
            reason=esc(reason), until=fmt_dt(now() + hours * 3600), left=human_delta(hours * 3600)
        ),
        reply_markup=inline.appeal_button(),
    )
    await db.execute("UPDATE users SET shadow_notified = 1 WHERE id = ?", (target_id,))
    await users_service.recompute_trust(db, target_id)

    await query.answer(f"Охват снижен: {SHADOW_TITLES.get(level, level)}")
    card = await _user_card(db, target_id)
    if card:
        await _show(query, card[0], card[1])


@router.callback_query(AdminCB.filter(F.action == "unshadow"))
async def admin_unshadow(
    query: CallbackQuery,
    callback_data: AdminCB,
    bot: Bot,
    db: Database,
    user: dict[str, Any],
) -> None:
    target_id = callback_data.target
    await users_service.clear_shadow(db, target_id)
    profile = await profiles_service.get(db, target_id)
    if profile and profile.get("moderation") != MOD_OK:
        await profiles_service.set_moderation(db, target_id, MOD_OK, None)
    await moderation.log_action(db, int(user["id"]), "unshadow", target_id=target_id)
    await notify.send_message(bot, db, target_id, texts.SHADOW_LIFTED)
    await query.answer("Ограничение снято")
    card = await _user_card(db, target_id)
    if card:
        await _show(query, card[0], card[1])


@router.callback_query(AdminCB.filter(F.action == "ban_menu"))
async def admin_ban_menu(query: CallbackQuery, callback_data: AdminCB, db: Database) -> None:
    await query.answer()
    reason = await _reason_from_reports(db, callback_data.target)
    await _show(
        query,
        f"🚫 <b>Блокировка</b>\n\nПричина: <i>{esc(reason)}</i>\n\nНа какой срок?",
        inline.ban_menu(callback_data.target, page=callback_data.page),
    )


@router.callback_query(AdminCB.filter(F.action == "ban"))
async def admin_ban_apply(
    query: CallbackQuery,
    callback_data: AdminCB,
    bot: Bot,
    db: Database,
    user: dict[str, Any],
) -> None:
    target_id = callback_data.target
    code = callback_data.value
    if code not in BAN_DURATIONS:
        await query.answer()
        return

    target = await users_service.get(db, target_id)
    if not target:
        await query.answer("Пользователь не найден", show_alert=True)
        return
    if int(target.get("role") or 0) >= int(user.get("role") or 0) and int(target["id"]) != int(
        user["id"]
    ):
        await query.answer("Нельзя ограничить коллегу с равной или высшей ролью", show_alert=True)
        return

    seconds = BAN_DURATIONS[code]
    reason = await _reason_from_reports(db, target_id)
    await users_service.ban(
        db, target_id, reason=reason, seconds=seconds, by=int(user["id"])
    )
    await _close_reports(
        bot,
        db,
        admin_id=int(user["id"]),
        target_id=target_id,
        resolution=f"пользователь заблокирован ({BAN_TITLES[code]})",
    )
    await moderation.log_action(
        db,
        int(user["id"]),
        "ban",
        target_id=target_id,
        details={"duration": code, "reason": reason},
    )

    if seconds is None:
        until_line = texts.BAN_PERMANENT_LINE
    else:
        until_line = texts.BAN_UNTIL_LINE.format(
            date=fmt_dt(now() + seconds), left=human_delta(seconds)
        )
    await notify.send_message(
        bot,
        db,
        target_id,
        texts.BANNED.format(reason=esc(reason), until=until_line),
        reply_markup=inline.appeal_button(),
    )

    await query.answer(f"Заблокирован: {BAN_TITLES[code]}")
    card = await _user_card(db, target_id)
    if card:
        await _show(query, card[0], card[1])


@router.callback_query(AdminCB.filter(F.action == "unban"))
async def admin_unban(
    query: CallbackQuery,
    callback_data: AdminCB,
    bot: Bot,
    db: Database,
    user: dict[str, Any],
) -> None:
    target_id = callback_data.target
    await users_service.unban(db, target_id)
    await users_service.clear_shadow(db, target_id)
    profile = await profiles_service.get(db, target_id)
    if profile and profile.get("moderation") != MOD_OK:
        await profiles_service.set_moderation(db, target_id, MOD_OK, None)
    await moderation.log_action(db, int(user["id"]), "unban", target_id=target_id)
    await notify.send_message(bot, db, target_id, texts.BAN_LIFTED)
    await users_service.recompute_trust(db, target_id)
    await query.answer("Разблокирован")
    card = await _user_card(db, target_id)
    if card:
        await _show(query, card[0], card[1])


@router.callback_query(AdminCB.filter(F.action == "hold"))
async def admin_hold_profile(
    query: CallbackQuery,
    callback_data: AdminCB,
    bot: Bot,
    db: Database,
    user: dict[str, Any],
) -> None:
    target_id = callback_data.target
    await profiles_service.set_moderation(db, target_id, MOD_HOLD, "решение модератора")
    await moderation.log_action(db, int(user["id"]), "hold_profile", target_id=target_id)
    await notify.send_message(
        bot,
        db,
        target_id,
        "⏸ <b>Анкета скрыта из поиска модератором</b>\n\n"
        "Скорее всего, дело в фотографиях или описании. Обнови анкету и отправь апелляцию — "
        "мы проверим ещё раз.",
        reply_markup=inline.appeal_button(),
    )
    await query.answer("Анкета скрыта")
    card = await _user_card(db, target_id)
    if card:
        await _show(query, card[0], card[1])


@router.callback_query(AdminCB.filter(F.action == "mod_ok"))
async def admin_approve_profile(
    query: CallbackQuery,
    callback_data: AdminCB,
    bot: Bot,
    db: Database,
    user: dict[str, Any],
) -> None:
    target_id = callback_data.target
    was_hidden = False
    profile = await profiles_service.get(db, target_id)
    if profile:
        was_hidden = profile.get("moderation") == MOD_HOLD
    await profiles_service.set_moderation(db, target_id, MOD_OK, None)
    # Раз анкету одобрили — снимаем и автоматическое снижение охвата
    target_user = await users_service.get(db, target_id)
    if target_user and users_service.is_shadowed(target_user):
        await users_service.clear_shadow(db, target_id)
        await notify.send_message(bot, db, target_id, texts.SHADOW_LIFTED)
    await moderation.log_action(db, int(user["id"]), "approve_profile", target_id=target_id)
    if was_hidden:
        await notify.send_message(
            bot,
            db,
            target_id,
            "✅ <b>Анкета проверена и снова в поиске.</b> Спасибо за ожидание!",
        )
    await query.answer("Одобрено")
    await _render_profile_queue(query, db, callback_data.page)


# --------------------------------------------------------------------------- очередь анкет


async def _render_profile_queue(query: CallbackQuery, db: Database, page: int) -> None:
    total = await moderation.profile_queue_count(db)
    if not total:
        await _show(query, "✅ Анкет на проверке нет.", inline.admin_back())
        return
    page = max(0, min(page, total - 1))
    rows = await moderation.profile_queue(db, limit=1, offset=page)
    if not rows:
        await _show(query, "✅ Анкет на проверке нет.", inline.admin_back())
        return

    card = rows[0]
    card["user_created_at"] = card.get("user_created_at")
    risk = await antifraud.risk_score(db, int(card["user_id"]))
    events = await antifraud.user_events(db, int(card["user_id"]), limit=5)
    signals = [str(event["kind"]) for event in events]
    text = f"🔍 <b>Проверка анкеты</b> ({page + 1} из {total})\n\n" + views.admin_user_card(
        card, risk=risk, signals=signals
    )
    if card.get("moderation_note"):
        text += f"\n\n📌 Причина проверки: <i>{esc(card['moderation_note'])}</i>"
    await _show(query, text, inline.moderation_actions(int(card["user_id"]), page=page))


@router.callback_query(AdminCB.filter(F.action == "mod"))
async def admin_moderation_queue(
    query: CallbackQuery, callback_data: AdminCB, db: Database
) -> None:
    await query.answer()
    await _render_profile_queue(query, db, callback_data.page)


# --------------------------------------------------------------------------- пользователи


@router.callback_query(AdminCB.filter(F.action == "find"))
async def admin_find(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Admin.find_user)
    await query.answer()
    if query.message:
        await query.message.answer(
            "🔎 Отправь ID пользователя или @username.\n"
            "Например: <code>123456789</code> или <code>@nickname</code>"
        )


@router.message(Admin.find_user, F.text)
async def admin_find_result(message: Message, state: FSMContext, db: Database) -> None:
    raw = (message.text or "").strip()
    await state.set_state(None)

    target: dict[str, Any] | None = None
    if raw.lstrip("-").isdigit():
        target = await users_service.get(db, int(raw))
    else:
        target = await users_service.get_by_username(db, raw)

    if not target:
        await message.answer("Никого не нашёл. Проверь ID или @username.")
        return

    card = await _user_card(db, int(target["id"]))
    if card:
        await message.answer(card[0], reply_markup=card[1])


@router.callback_query(AdminCB.filter(F.action == "user"))
async def admin_user(query: CallbackQuery, callback_data: AdminCB, db: Database) -> None:
    await query.answer()
    card = await _user_card(db, callback_data.target)
    if not card:
        await _show(query, "Пользователь не найден.", inline.admin_back())
        return
    await _show(query, card[0], card[1])


@router.callback_query(AdminCB.filter(F.action == "show_profile"))
async def admin_show_profile(
    query: CallbackQuery, callback_data: AdminCB, bot: Bot, db: Database
) -> None:
    await query.answer()
    if query.message is None:
        return
    card = await profiles_service.card(db, callback_data.target)
    if not card:
        await query.message.answer("Анкеты нет.")
        return
    await render.send_card(bot, db, query.message.chat.id, card, header="👤 <b>Анкета</b>")


@router.callback_query(AdminCB.filter(F.action == "user_reports"))
async def admin_user_reports(
    query: CallbackQuery, callback_data: AdminCB, db: Database
) -> None:
    await query.answer()
    rows = await moderation.reports_about(db, callback_data.target, limit=10)
    if not rows:
        await _show(query, "На этого пользователя жалоб нет.", inline.admin_back())
        return

    lines = [f"🚩 <b>Жалобы на {callback_data.target}</b>", ""]
    for row in rows:
        status_icon = {"open": "🟡", "resolved": "🔴", "rejected": "🟢", "in_review": "🟠"}.get(
            str(row["status"]), "•"
        )
        lines.append(
            f"{status_icon} №{row['id']} · {REPORT_CATEGORIES.get(str(row['category']), '—')} · "
            f"{fmt_dt(row['created_at'])}"
        )
        if row.get("comment"):
            lines.append(f"   <i>{esc(shorten(str(row['comment']), 150))}</i>")
        if row.get("resolution"):
            lines.append(f"   ↳ {esc(str(row['resolution']))}")
    await _show(query, "\n".join(lines), inline.admin_back())


@router.callback_query(AdminCB.filter(F.action == "wipe_photos"))
async def admin_wipe_photos(
    query: CallbackQuery,
    callback_data: AdminCB,
    bot: Bot,
    db: Database,
    user: dict[str, Any],
) -> None:
    target_id = callback_data.target
    await profiles_service.clear_photos(db, target_id)
    await profiles_service.mark_complete(db, target_id)
    await profiles_service.set_moderation(db, target_id, MOD_HOLD, "удалены фотографии")
    await moderation.log_action(db, int(user["id"]), "wipe_photos", target_id=target_id)
    await notify.send_message(
        bot,
        db,
        target_id,
        "🗑 <b>Фотографии удалены модератором</b>\n\n"
        "Причина: снимки нарушают правила (чужие фото, реклама или запрещённый контент).\n"
        "Загрузи свои фотографии в разделе «Моя анкета», и она вернётся в поиск.",
    )
    await query.answer("Фото удалены")
    card = await _user_card(db, target_id)
    if card:
        await _show(query, card[0], card[1])


@router.callback_query(AdminCB.filter(F.action == "ban_photos"))
async def admin_ban_photos(
    query: CallbackQuery,
    callback_data: AdminCB,
    bot: Bot,
    db: Database,
    user: dict[str, Any],
) -> None:
    """Стоп-лист по хэшу файла: такие снимки не пройдут ни у кого."""
    target_id = callback_data.target
    added = await antifraud.ban_user_photos(
        db, target_id, "решение модератора", int(user["id"])
    )
    if not added:
        await query.answer("У пользователя нет фотографий", show_alert=True)
        return

    await profiles_service.clear_photos(db, target_id)
    await profiles_service.mark_complete(db, target_id)
    await profiles_service.set_moderation(db, target_id, MOD_HOLD, "фото в стоп-листе")
    await moderation.log_action(
        db, int(user["id"]), "photos_blacklisted", target_id=target_id, details={"hashes": added}
    )
    await notify.send_message(
        bot,
        db,
        target_id,
        "🗑 <b>Фотографии удалены модератором</b>\n\n"
        "Эти снимки нарушают правила и больше не принимаются — ни здесь, ни на другом "
        "аккаунте. Загрузи свои фотографии, и анкета вернётся в поиск.",
        reply_markup=inline.appeal_button(),
    )
    await query.answer(f"В стоп-лист добавлено хэшей: {added}")
    card = await _user_card(db, target_id)
    if card:
        await _show(query, card[0], card[1])


@router.callback_query(AdminCB.filter(F.action == "unban_photos"))
async def admin_unban_photos(
    query: CallbackQuery, callback_data: AdminCB, db: Database, user: dict[str, Any]
) -> None:
    target_id = callback_data.target
    removed = await antifraud.unban_user_photos(db, target_id)
    await moderation.log_action(
        db,
        int(user["id"]),
        "photos_unblacklisted",
        target_id=target_id,
        details={"hashes": removed},
    )
    await query.answer(f"Убрано из стоп-листа: {removed}")
    card = await _user_card(db, target_id)
    if card:
        await _show(query, card[0], card[1])


@router.callback_query(AdminCB.filter(F.action == "clear_flags"))
async def admin_clear_flags(
    query: CallbackQuery, callback_data: AdminCB, db: Database, user: dict[str, Any]
) -> None:
    target_id = callback_data.target
    await db.execute("DELETE FROM events WHERE user_id = ? AND weight > 0", (target_id,))
    await users_service.recompute_trust(db, target_id)
    await moderation.log_action(db, int(user["id"]), "clear_flags", target_id=target_id)
    await query.answer("Сигналы антифрода сняты")
    card = await _user_card(db, target_id)
    if card:
        await _show(query, card[0], card[1])


# --------------------------------------------------------------------------- апелляции


async def _render_appeal(query: CallbackQuery, db: Database, page: int) -> None:
    total = await moderation.appeals_count(db)
    if not total:
        await _show(query, "✅ Апелляций нет.", inline.admin_back())
        return
    page = max(0, min(page, total - 1))
    rows = await moderation.appeals_queue(db, limit=1, offset=page)
    if not rows:
        await _show(query, "✅ Апелляций нет.", inline.admin_back())
        return
    appeal = rows[0]
    await _show(
        query,
        views.appeal_card(appeal, position=page + 1, total=total),
        inline.appeal_actions(int(appeal["id"]), int(appeal["user_id"]), page=page),
    )


@router.callback_query(AdminCB.filter(F.action == "appeals"))
async def admin_appeals(query: CallbackQuery, callback_data: AdminCB, db: Database) -> None:
    await query.answer()
    await _render_appeal(query, db, callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "ap_accept"))
async def admin_appeal_accept(
    query: CallbackQuery,
    callback_data: AdminCB,
    bot: Bot,
    db: Database,
    user: dict[str, Any],
) -> None:
    appeal = await moderation.get_appeal(db, callback_data.target)
    if not appeal:
        await query.answer("Апелляция не найдена", show_alert=True)
        return

    target_id = int(appeal["user_id"])
    await users_service.unban(db, target_id)
    await users_service.clear_shadow(db, target_id)
    profile = await profiles_service.get(db, target_id)
    if profile and profile.get("moderation") != MOD_OK:
        await profiles_service.set_moderation(db, target_id, MOD_OK, None)
    await moderation.answer_appeal(
        db,
        int(appeal["id"]),
        admin_id=int(user["id"]),
        answer="Ограничения сняты. Извини за неудобства!",
    )
    await moderation.log_action(
        db, int(user["id"]), "appeal_accepted", target_id=target_id, details=appeal["id"]
    )
    await notify.send_message(
        bot,
        db,
        target_id,
        texts.APPEAL_ANSWER.format(
            appeal_id=appeal["id"],
            answer="✅ Мы пересмотрели решение — все ограничения сняты. Извини за неудобства!",
        ),
    )
    await users_service.recompute_trust(db, target_id)
    await query.answer("Ограничения сняты")
    await _render_appeal(query, db, callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "ap_reject"))
async def admin_appeal_reject(
    query: CallbackQuery,
    callback_data: AdminCB,
    bot: Bot,
    db: Database,
    user: dict[str, Any],
) -> None:
    appeal = await moderation.get_appeal(db, callback_data.target)
    if not appeal:
        await query.answer("Апелляция не найдена", show_alert=True)
        return
    await moderation.answer_appeal(
        db,
        int(appeal["id"]),
        admin_id=int(user["id"]),
        answer="Решение оставлено в силе.",
        status="rejected",
    )
    await moderation.log_action(
        db, int(user["id"]), "appeal_rejected", target_id=int(appeal["user_id"])
    )
    await notify.send_message(
        bot,
        db,
        int(appeal["user_id"]),
        texts.APPEAL_ANSWER.format(
            appeal_id=appeal["id"],
            answer="Мы ещё раз всё проверили и оставили решение в силе. "
            "Ограничение снимется автоматически по истечении срока.",
        ),
    )
    await query.answer("Отклонено")
    await _render_appeal(query, db, callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "ap_reply"))
async def admin_appeal_reply_start(
    query: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    await state.set_state(Admin.appeal_reply)
    await state.update_data(adm_appeal=callback_data.target, adm_page=callback_data.page)
    await query.answer()
    if query.message:
        await query.message.answer("✍️ Напиши ответ — пользователь получит его дословно.")


@router.message(Admin.appeal_reply, F.text)
async def admin_appeal_reply_send(
    message: Message,
    bot: Bot,
    state: FSMContext,
    db: Database,
    user: dict[str, Any],
) -> None:
    data = await state.get_data()
    appeal_id = int(data.get("adm_appeal") or 0)
    await state.set_state(None)

    appeal = await moderation.get_appeal(db, appeal_id) if appeal_id else None
    if not appeal:
        await message.answer("Апелляция не найдена.")
        return

    answer = shorten(message.text or "", 1000)
    await moderation.answer_appeal(
        db, appeal_id, admin_id=int(user["id"]), answer=answer, status="closed"
    )
    await moderation.log_action(
        db, int(user["id"]), "appeal_answered", target_id=int(appeal["user_id"])
    )
    await notify.send_message(
        bot,
        db,
        int(appeal["user_id"]),
        texts.APPEAL_ANSWER.format(appeal_id=appeal_id, answer=esc(answer)),
    )
    await message.answer("✅ Ответ отправлен.")


# --------------------------------------------------------------------------- верификация


async def _render_verify(
    query: CallbackQuery, bot: Bot, db: Database, page: int
) -> None:
    total = await verification.queue_count(db)
    if not total:
        await _show(query, "✅ Заявок на верификацию нет.", inline.admin_back())
        return

    page = max(0, min(page, total - 1))
    rows = await verification.queue(db, limit=1, offset=page)
    if not rows or query.message is None:
        await _show(query, "✅ Заявок на верификацию нет.", inline.admin_back())
        return

    item = rows[0]
    user_id = int(item["user_id"])
    photos = await profiles_service.photos(db, user_id)

    # Селфи и фото из анкеты в одном альбоме — так сравнивать проще всего
    media: list[InputMediaPhoto] = []
    if item.get("verify_file_id"):
        media.append(
            InputMediaPhoto(media=str(item["verify_file_id"]), caption="👆 селфи с жестом")
        )
    for photo in photos[:2]:
        media.append(InputMediaPhoto(media=str(photo["file_id"])))
    if media:
        await notify.send_media_group(bot, db, query.message.chat.id, media)

    lines = [
        f"✅ <b>Заявка на верификацию</b> ({page + 1} из {total})",
        "",
        f"Просили показать: <b>{esc(item.get('verify_gesture') or 'жест не сохранён')}</b>",
        "",
        f"👤 <code>{user_id}</code>"
        + (f" · @{esc(item['username'])}" if item.get("username") else ""),
        f"{esc(item.get('name') or 'без имени')}, {item.get('age') or '—'} · "
        f"{esc(item.get('city') or '—')}",
        f"Доверие: {item.get('trust_score', 0)}/100 · регистрация: "
        f"{fmt_dt(item.get('user_created_at'))}",
        "",
        "Первое фото — селфи, следующие — из анкеты. Проверь, что это один человек "
        "и что жест совпадает.",
    ]
    await notify.send_message(
        bot,
        db,
        query.message.chat.id,
        "\n".join(lines),
        reply_markup=inline.verify_actions(user_id, page=page),
    )


@router.callback_query(AdminCB.filter(F.action == "verify"))
async def admin_verify_queue(
    query: CallbackQuery, callback_data: AdminCB, bot: Bot, db: Database
) -> None:
    await query.answer()
    await _render_verify(query, bot, db, callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "vf_ok"))
async def admin_verify_approve(
    query: CallbackQuery,
    callback_data: AdminCB,
    bot: Bot,
    db: Database,
    user: dict[str, Any],
) -> None:
    target_id = callback_data.target
    await verification.approve(db, target_id)
    await users_service.recompute_trust(db, target_id)
    await moderation.log_action(db, int(user["id"]), "verify_approved", target_id=target_id)
    await notify.send_message(bot, db, target_id, texts.VERIFY_DONE)
    await query.answer("Анкета подтверждена")
    await _render_verify(query, bot, db, callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "vf_no"))
async def admin_verify_reject(
    query: CallbackQuery,
    callback_data: AdminCB,
    bot: Bot,
    db: Database,
    user: dict[str, Any],
) -> None:
    target_id = callback_data.target
    reason = "на селфи не видно лица или нужного жеста"
    await verification.reject(db, target_id, reason)
    await moderation.log_action(db, int(user["id"]), "verify_rejected", target_id=target_id)
    await notify.send_message(
        bot, db, target_id, texts.VERIFY_FAILED.format(reason=esc(reason))
    )
    await query.answer("Отклонено")
    await _render_verify(query, bot, db, callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "vf_fake"))
async def admin_verify_fake(
    query: CallbackQuery,
    callback_data: AdminCB,
    bot: Bot,
    db: Database,
    user: dict[str, Any],
) -> None:
    """Селфи и анкета — разные люди: прячем анкету и ставим её в очередь разбора."""
    target_id = callback_data.target
    reason = "селфи не совпадает с фотографиями анкеты"
    await verification.reject(db, target_id, reason)
    await profiles_service.set_moderation(db, target_id, MOD_HOLD, "чужие фотографии")
    await antifraud.log_event(db, target_id, "photo_reuse", meta={"source": "verification"})
    await users_service.recompute_trust(db, target_id)
    await moderation.log_action(db, int(user["id"]), "verify_fake", target_id=target_id)
    await notify.send_message(
        bot,
        db,
        target_id,
        "⏸ <b>Анкета скрыта из поиска</b>\n\n"
        f"Причина: {esc(reason)}.\n\n"
        "Если на фото действительно ты — загрузи свои снимки и отправь апелляцию, "
        "модератор проверит ещё раз.",
        reply_markup=inline.appeal_button(),
    )
    await query.answer("Анкета скрыта")
    await _render_verify(query, bot, db, callback_data.page)


# --------------------------------------------------------------------------- антифрод


@router.callback_query(AdminCB.filter(F.action == "fraud"))
async def admin_fraud(query: CallbackQuery, callback_data: AdminCB, db: Database) -> None:
    await query.answer()
    per_page = 8
    total = await antifraud.flagged_count(db)
    if not total:
        await _show(query, "🤖 Подозрительных аккаунтов нет.", inline.admin_back())
        return

    page, pages, offset = page_bounds(total, callback_data.page, per_page)
    rows = await antifraud.flagged_users(db, limit=per_page, offset=offset)

    lines = [f"🤖 <b>Антифрод</b> · найдено: {total}", ""]
    for row in rows:
        username = f"@{row['username']}" if row.get("username") else "без username"
        lines.append(
            f"<code>{row['user_id']}</code> · {esc(str(row.get('name') or 'без анкеты'))} · {username}\n"
            f"   риск <b>{row['risk']}</b> · доверие {row.get('trust_score', 0)} · "
            f"сигналы: {esc(str(row.get('kinds') or ''))}"
        )
    lines.append("")
    lines.append("Чтобы разобрать аккаунт — «👤 Найти пользователя» и его ID.")

    await _show(query, "\n".join(lines), inline.pager("fraud", page, pages))


# --------------------------------------------------------------------------- журнал


@router.callback_query(AdminCB.filter(F.action == "log"))
async def admin_log(query: CallbackQuery, callback_data: AdminCB, db: Database) -> None:
    await query.answer()
    per_page = 10
    total = await moderation.log_count(db)
    if not total:
        await _show(query, "📜 Журнал пуст.", inline.admin_back())
        return

    page, pages, offset = page_bounds(total, callback_data.page, per_page)
    rows = await moderation.recent_log(db, limit=per_page, offset=offset)

    lines = ["📜 <b>Журнал действий</b>", ""]
    for row in rows:
        who = f"@{row['admin_username']}" if row.get("admin_username") else str(row["admin_id"])
        target = f" → <code>{row['target_id']}</code>" if row.get("target_id") else ""
        details = f" · {esc(shorten(str(row['details']), 80))}" if row.get("details") else ""
        lines.append(f"{fmt_dt(row['created_at'])} · {esc(who)} · <b>{esc(str(row['action']))}</b>{target}{details}")

    await _show(query, "\n".join(lines), inline.pager("log", page, pages))


# --------------------------------------------------------------------------- настройки бота


@router.callback_query(AdminCB.filter(F.action == "cfg"))
async def admin_config(query: CallbackQuery, settings: Settings) -> None:
    await query.answer()
    await _show(
        query,
        "⚙️ <b>Настройки бота</b>\n\nНажми на параметр, чтобы изменить значение.",
        inline.settings_list(settings.all(), SETTING_TITLES),
    )


@router.callback_query(AdminCB.filter(F.action == "cfg_edit"))
async def admin_config_edit(
    query: CallbackQuery, callback_data: AdminCB, state: FSMContext, settings: Settings
) -> None:
    key = callback_data.value
    if key not in DEFAULT_SETTINGS:
        await query.answer()
        return
    await state.set_state(Admin.setting_value)
    await state.update_data(adm_setting=key)
    await query.answer()
    if query.message:
        await query.message.answer(
            f"⚙️ <b>{SETTING_TITLES.get(key, key)}</b>\n\n"
            f"Текущее значение: <code>{settings.get(key)}</code>\n"
            f"По умолчанию: <code>{DEFAULT_SETTINGS[key]}</code>\n\n"
            "Пришли новое число."
        )


@router.message(Admin.setting_value, F.text)
async def admin_config_save(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user: dict[str, Any],
) -> None:
    data = await state.get_data()
    key = str(data.get("adm_setting") or "")
    await state.set_state(None)
    if key not in DEFAULT_SETTINGS:
        await message.answer(texts.ERROR_GENERIC)
        return

    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужно целое число. Попробуй ещё раз через меню настроек.")
        return

    await settings.set(key, raw)
    await moderation.log_action(
        db, int(user["id"]), "setting_changed", details={"key": key, "value": raw}
    )
    await message.answer(
        f"✅ {SETTING_TITLES.get(key, key)} = <code>{raw}</code>",
        reply_markup=inline.settings_list(settings.all(), SETTING_TITLES),
    )


# --------------------------------------------------------------------------- команда


@router.callback_query(AdminCB.filter(F.action == "staff"))
async def admin_staff(query: CallbackQuery, db: Database, user: dict[str, Any]) -> None:
    await query.answer()
    members = await users_service.staff(db)
    can_manage = int(user.get("role") or 0) >= ROLE_OWNER
    lines = ["👮 <b>Команда</b>", ""]
    if not members:
        lines.append("Пока только владельцы из настроек.")
    lines.append(
        "Модератор разбирает жалобы и апелляции. Владелец может назначать модераторов."
    )
    await _show(query, "\n".join(lines), inline.staff_list(members, can_manage=can_manage))


@router.callback_query(AdminCB.filter(F.action == "staff_add"))
async def admin_staff_add(
    query: CallbackQuery, state: FSMContext, user: dict[str, Any]
) -> None:
    if int(user.get("role") or 0) < ROLE_OWNER:
        await query.answer("Только владелец может назначать модераторов", show_alert=True)
        return
    await state.set_state(Admin.staff_add)
    await query.answer()
    if query.message:
        await query.message.answer(
            "Отправь ID или @username человека, которого назначить модератором.\n"
            "Он должен хотя бы раз запустить бота."
        )


@router.message(Admin.staff_add, F.text)
async def admin_staff_add_save(
    message: Message, state: FSMContext, db: Database, user: dict[str, Any]
) -> None:
    await state.set_state(None)
    if int(user.get("role") or 0) < ROLE_OWNER:
        return

    raw = (message.text or "").strip()
    target = (
        await users_service.get(db, int(raw))
        if raw.lstrip("-").isdigit()
        else await users_service.get_by_username(db, raw)
    )
    if not target:
        await message.answer("Не нашёл такого пользователя. Он должен сначала запустить бота.")
        return

    await users_service.set_role(db, int(target["id"]), ROLE_MODERATOR)
    await moderation.log_action(
        db, int(user["id"]), "staff_added", target_id=int(target["id"])
    )
    await message.answer(f"✅ Назначен модератором: <code>{target['id']}</code>")


@router.callback_query(AdminCB.filter(F.action == "staff_del"))
async def admin_staff_del(
    query: CallbackQuery, callback_data: AdminCB, db: Database, user: dict[str, Any]
) -> None:
    if int(user.get("role") or 0) < ROLE_OWNER:
        await query.answer("Только владелец может снимать модераторов", show_alert=True)
        return
    await users_service.set_role(db, callback_data.target, ROLE_USER)
    await moderation.log_action(
        db, int(user["id"]), "staff_removed", target_id=callback_data.target
    )
    await query.answer("Снят с должности")
    members = await users_service.staff(db)
    await _show(query, "👮 <b>Команда</b>", inline.staff_list(members, can_manage=True))


@router.message(Admin.find_user)
@router.message(Admin.setting_value)
@router.message(Admin.warn_reason)
@router.message(Admin.appeal_reply)
@router.message(Admin.staff_add)
async def admin_expects_text(message: Message) -> None:
    await message.answer("Здесь нужен текст. Отменить — /cancel")


# --------------------------------------------------------------------------- рассылка


@router.callback_query(AdminCB.filter(F.action == "bc"))
async def admin_broadcast_start(
    query: CallbackQuery, state: FSMContext, user: dict[str, Any]
) -> None:
    if int(user.get("role") or 0) < ROLE_OWNER:
        await query.answer("Рассылку может запускать только владелец", show_alert=True)
        return
    await state.set_state(Admin.broadcast)
    await query.answer()
    if query.message:
        await query.message.answer(
            "📣 <b>Рассылка</b>\n\n"
            "Пришли сообщение — текст, фото или что угодно. Я покажу предпросмотр "
            "и спрошу подтверждение.\n\n"
            "Отменить: /cancel"
        )


@router.message(Admin.broadcast)
async def admin_broadcast_preview(
    message: Message, state: FSMContext, db: Database, user: dict[str, Any]
) -> None:
    if int(user.get("role") or 0) < ROLE_OWNER:
        await state.set_state(None)
        return

    audience = int(
        await db.fetchval(
            "SELECT COUNT(*) FROM users WHERE status != 'deleted' AND bot_blocked = 0", (), 0
        )
    )
    await state.set_state(Admin.broadcast_confirm)
    await state.update_data(bc_chat=message.chat.id, bc_message=message.message_id)
    await message.answer(
        f"👆 Так увидят сообщение получатели.\n\nАудитория: <b>{audience}</b> человек.\n"
        "Отправляем?",
        reply_markup=inline.broadcast_confirm(),
    )


@router.callback_query(Admin.broadcast_confirm, AdminCB.filter(F.action == "bc_send"))
async def admin_broadcast_send(
    query: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    db: Database,
    user: dict[str, Any],
) -> None:
    data = await state.get_data()
    from_chat = int(data.get("bc_chat") or 0)
    message_id = int(data.get("bc_message") or 0)
    await state.set_state(None)

    if not from_chat or not message_id:
        await query.answer("Сообщение потерялось, начни заново", show_alert=True)
        return

    await query.answer("Запустил рассылку")
    if query.message:
        await query.message.edit_text("📣 Рассылка запущена. Отчёт придёт сюда же.")

    await moderation.log_action(db, int(user["id"]), "broadcast_started")
    asyncio.create_task(
        _run_broadcast(bot, db, from_chat, message_id, admin_id=int(user["id"]))
    )


async def _run_broadcast(
    bot: Bot, db: Database, from_chat: int, message_id: int, *, admin_id: int
) -> None:
    rows = await db.fetchall(
        "SELECT id FROM users WHERE status != 'deleted' AND bot_blocked = 0 ORDER BY id"
    )
    sent = 0
    failed = 0
    for row in rows:
        target_id = int(row["id"])
        result = await notify.copy_message(bot, db, target_id, from_chat, message_id)
        if result is None:
            failed += 1
        else:
            sent += 1
        await asyncio.sleep(0.05)  # ~20 сообщений в секунду, лимит Telegram — 30

    await notify.send_message(
        bot,
        db,
        admin_id,
        f"📣 <b>Рассылка завершена</b>\n\nДоставлено: <b>{sent}</b>\nНе доставлено: <b>{failed}</b>",
    )
    await moderation.log_action(
        db, admin_id, "broadcast_finished", details={"sent": sent, "failed": failed}
    )
