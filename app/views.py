"""Сборка текстов карточек — анкет, жалоб, админских сводок."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app import texts
from app.constants import (
    GENDER_ICONS,
    INTERESTS,
    MOD_HOLD,
    MOD_OK,
    MOD_REJECTED,
    MOD_REVIEW,
    REPORT_CATEGORIES,
    ROLE_NAMES,
    SEEKING,
    SHADOW_TITLES,
    STATUS_ACTIVE,
    STATUS_BANNED,
    STATUS_DELETED,
    STATUS_NEW,
)
from app.utils.text import esc, human_delta, plural, shorten
from app.utils.time import fmt_dt, now

MOD_LABELS = {
    MOD_OK: "✅ в порядке",
    MOD_REVIEW: "🔍 на проверке (охват снижен)",
    MOD_HOLD: "⏸ скрыта до решения модератора",
    MOD_REJECTED: "❌ отклонена",
}

STATUS_LABELS = {
    STATUS_NEW: "🆕 новый",
    STATUS_ACTIVE: "✅ активен",
    STATUS_BANNED: "🚫 заблокирован",
    STATUS_DELETED: "🗑 анкета удалена",
}


def interests_line(codes: str | None) -> str:
    if not codes:
        return ""
    names = [INTERESTS[code] for code in str(codes).split(",") if code in INTERESTS]
    return " · ".join(names)


def last_seen(ts: int | None) -> str:
    if not ts:
        return "давно"
    delta = now() - int(ts)
    if delta < 600:
        return "в сети"
    if delta < 3600:
        return f"{delta // 60} мин назад"
    if delta < 86400:
        return f"{delta // 3600} ч назад"
    days = delta // 86400
    return f"{days} {plural(days, 'день', 'дня', 'дней')} назад"


# --------------------------------------------------------------------------- анкета


def profile_caption(
    card: dict[str, Any],
    *,
    header: str | None = None,
    show_activity: bool = True,
    footer: str | None = None,
    bio_limit: int = 600,
) -> str:
    name = esc(card.get("name") or "Без имени")
    age = card.get("age")
    city = esc(card.get("city") or "")

    lines: list[str] = []
    if header:
        lines.append(header)
        lines.append("")

    title = f"<b>{name}</b>"
    if age:
        title += f", {age}"
    if card.get("verified"):
        title += " ✅"
    if city:
        title += f" · 📍 {city}"
    lines.append(title)

    interests = interests_line(card.get("interests"))
    if interests:
        lines.append(f"<i>{esc(interests)}</i>")

    bio = (card.get("bio") or "").strip()
    if bio and bio_limit > 0:
        lines.append("")
        lines.append(esc(shorten(bio, bio_limit)))

    if show_activity:
        lines.append("")
        lines.append(f"<i>🕐 {last_seen(card.get('last_active_at'))}</i>")

    if footer:
        lines.append("")
        lines.append(footer)

    return "\n".join(lines)


def incoming_like_header(card: dict[str, Any]) -> str:
    action = card.get("like_action")
    if action == "superlike":
        return "💥 <b>Тебе отправили суперлайк!</b>"
    return "💌 <b>Ты понравился этому человеку</b>"


def own_profile_text(
    card: dict[str, Any],
    *,
    likes_left: int,
    limit: int,
    incoming: int,
    matches: int,
) -> str:
    visible = bool(card.get("is_visible"))
    moderation = str(card.get("moderation") or MOD_OK)

    lines = [profile_caption(card, show_activity=False), ""]
    lines.append("─────────────")
    lines.append(
        f"👀 Показ в поиске: {'включён' if visible else '⏸ на паузе'}"
    )
    if moderation != MOD_OK:
        lines.append(f"🔍 Модерация: {MOD_LABELS.get(moderation, moderation)}")
        note = card.get("moderation_note")
        if note:
            lines.append(f"<i>{esc(note)}</i>")
    lines.append(f"❤️ Лайков сегодня: {likes_left} из {limit}")
    lines.append(f"💌 Входящих лайков: {incoming}")
    lines.append(f"💞 Симпатий: {matches}")
    return "\n".join(lines)


def preferences_text(profile: dict[str, Any]) -> str:
    seeking = SEEKING.get(profile.get("seeking") or "any", "всех")
    only_city = bool(profile.get("only_my_city"))
    return (
        "⚙️ <b>Фильтры поиска</b>\n\n"
        f"👥 Показывать: <b>{seeking}</b>\n"
        f"🎂 Возраст: <b>{profile.get('age_min', 18)}–{profile.get('age_max', 99)}</b>\n"
        f"📍 Только мой город: <b>{'да' if only_city else 'нет'}</b>"
        f"{' (' + esc(profile.get('city') or '') + ')' if only_city else ''}\n"
        f"✅ Только подтверждённые анкеты: <b>{'да' if profile.get('only_verified') else 'нет'}</b>\n\n"
        "Чем шире фильтры, тем больше анкет в ленте."
    )


def notifications_text(user: dict[str, Any]) -> str:
    def mark(value: Any) -> str:
        return "включены" if value else "выключены"

    return (
        "🔔 <b>Уведомления</b>\n\n"
        f"💌 О новых лайках: <b>{mark(user.get('notify_likes'))}</b>\n"
        f"💞 О взаимных симпатиях: <b>{mark(user.get('notify_matches'))}</b>\n"
        f"💬 О сообщениях: <b>{mark(user.get('notify_messages'))}</b>"
    )


def limit_details(limits: Any) -> str:
    """Расшифровка суточного лимита: за что дали и что можно добавить."""
    lines = [
        texts.LIMIT_DETAILS_HEADER.format(total=limits.total),
        texts.LIMIT_DETAILS_BASE.format(base=limits.base),
    ]
    for label, value in limits.parts:
        sign = "+" if value > 0 else "−"
        lines.append(f"• {esc(label)} — {sign}{abs(value)}")
    if limits.available:
        lines.append(texts.LIMIT_DETAILS_AVAILABLE)
        for label, value in limits.available:
            lines.append(f"• {esc(label)} — +{value}")
    if limits.capped:
        lines.append(texts.LIMIT_DETAILS_CAP.format(cap=limits.cap))
    lines.append(texts.LIMIT_DETAILS_FOOTER)
    return "\n".join(lines)


def insights_text(
    stats: dict[str, int],
    *,
    card: dict[str, Any],
    photos: int,
    verified: bool,
) -> str:
    """Статистика анкеты и конкретные советы, что подкрутить."""
    shown = int(stats.get("shown") or 0)
    likes = int(stats.get("likes_in") or 0)
    matches = int(stats.get("matches") or 0)
    if not shown and not likes:
        return texts.INSIGHTS_EMPTY

    rate = round(likes / shown * 100) if shown else 0
    text = texts.INSIGHTS.format(shown=shown, likes=likes, matches=matches, rate=rate)

    tips: list[str] = []
    if photos < 2:
        tips.append("добавь второе фото — анкеты с одним снимком листают чаще")
    if len(str(card.get("bio") or "").strip()) < 30:
        tips.append("напиши пару строк о себе: с описанием отвечают заметно охотнее")
    if not str(card.get("interests") or "").strip():
        tips.append("выбери интересы — по ним проще найти общую тему")
    if not verified:
        tips.append("подтверди анкету селфи: галочка и +лайки к лимиту")
    if shown and rate < 10 and photos >= 1:
        tips.append("попробуй сменить главное фото — на нём должно быть видно лицо")
    if shown < 20:
        tips.append("смотри ленту чаще: активных показываем первыми")
    if not card.get("is_visible"):
        tips.append("анкета на паузе — включи показ, иначе её никто не увидит")

    if not tips:
        return text + texts.INSIGHTS_ALL_GOOD
    return text + texts.INSIGHTS_TIPS_HEADER + "\n" + "\n".join(f"• {tip}" for tip in tips[:4])


# --------------------------------------------------------------------------- ограничения


def restriction_text(user: dict[str, Any]) -> str | None:
    """Текст про действующее ограничение охвата (или None)."""
    level = int(user.get("shadow_level") or 0)
    until = int(user.get("shadow_until") or 0)
    if level <= 0 or until <= now():
        return None
    return (
        f"⚠️ <b>Показы анкеты ограничены</b> ({SHADOW_TITLES.get(level, 'ограничение')})\n"
        f"Причина: {esc(user.get('shadow_reason') or 'жалобы пользователей')}\n"
        f"Снимется: <b>{fmt_dt(until)}</b> (через {human_delta(until - now())})"
    )


# --------------------------------------------------------------------------- админка


def admin_user_card(card: dict[str, Any], *, risk: int = 0, signals: Iterable[str] = ()) -> str:
    user_id = card.get("user_id") or card.get("id")
    username = card.get("username")
    lines = [
        "🗂 <b>Карточка пользователя</b>",
        f"ID: <code>{user_id}</code>"
        + (f" · @{esc(username)}" if username else " · без username"),
    ]

    name = card.get("name")
    if name:
        gender = card.get("gender")
        icon = GENDER_ICONS.get(str(gender), "")
        lines.append(
            f"{icon} <b>{esc(name)}</b>, {card.get('age') or '—'} · "
            f"📍 {esc(card.get('city') or '—')}"
        )
        interests = interests_line(card.get("interests"))
        if interests:
            lines.append(f"<i>{esc(interests)}</i>")
        bio = (card.get("bio") or "").strip()
        if bio:
            lines.append(f"💬 {esc(shorten(bio, 300))}")
    else:
        lines.append("<i>Анкета не заполнена</i>")

    lines.append("─────────────")
    status = str(card.get("status") or STATUS_NEW)
    lines.append(f"Статус: {STATUS_LABELS.get(status, status)}")

    role = int(card.get("role") or 0)
    if role:
        lines.append(f"Роль: <b>{ROLE_NAMES.get(role, role)}</b>")

    if card.get("ban_permanent"):
        lines.append("🚫 Блокировка: <b>бессрочная</b>")
    elif card.get("ban_until") and int(card["ban_until"]) > now():
        left = human_delta(int(card["ban_until"]) - now())
        lines.append(f"🚫 Блокировка до {fmt_dt(card['ban_until'])} (ещё {left})")

    shadow_level = int(card.get("shadow_level") or 0)
    shadow_until = int(card.get("shadow_until") or 0)
    if shadow_level and shadow_until > now():
        lines.append(
            f"🔅 Охват снижен ({SHADOW_TITLES.get(shadow_level, shadow_level)}) "
            f"до {fmt_dt(shadow_until)}"
        )

    moderation = str(card.get("moderation") or MOD_OK)
    if moderation != MOD_OK:
        lines.append(f"Анкета: {MOD_LABELS.get(moderation, moderation)}")
    if not card.get("is_visible", 1):
        lines.append("Анкета: ⏸ скрыта самим пользователем")
    if card.get("bot_blocked"):
        lines.append("📵 Пользователь заблокировал бота")

    lines.append("─────────────")
    lines.append(
        f"🤝 Доверие: <b>{card.get('trust_score', 0)}/100</b> · "
        f"⚠️ Риск: <b>{risk}</b> · Предупреждений: {card.get('warns', 0)}"
    )
    lines.append(
        f"❤️ Отправлено: {card.get('likes_sent', 0)} · "
        f"💌 Получено: {card.get('likes_received', 0)} · "
        f"💞 Симпатий: {card.get('matches_count', 0)}"
    )
    lines.append(f"🚩 Жалоб на него: {card.get('reports_count', 0)}")
    lines.append(
        f"📅 Регистрация: {fmt_dt(card.get('user_created_at') or card.get('created_at'))}"
    )
    lines.append(f"🕐 Последняя активность: {fmt_dt(card.get('last_active_at'))}")

    signal_list = [s for s in signals if s]
    if signal_list:
        lines.append("─────────────")
        lines.append("🤖 <b>Сигналы антифрода:</b>")
        for signal in signal_list[:6]:
            lines.append(f"• {esc(signal)}")

    return "\n".join(lines)


def report_card(report: dict[str, Any], *, position: int = 1, total: int = 1) -> str:
    category = REPORT_CATEGORIES.get(str(report.get("category")), "❓ Другое")
    lines = [
        f"🚩 <b>Жалоба №{report.get('id')}</b>  <i>({position} из {total})</i>",
        f"Категория: <b>{category}</b>",
        f"Когда: {fmt_dt(report.get('created_at'))}",
        f"Откуда: {'переписка' if report.get('context') == 'chat' else 'лента'}",
        "",
        f"👤 На кого: <code>{report.get('target_id')}</code>"
        + (f" · @{esc(report.get('target_username'))}" if report.get("target_username") else ""),
    ]
    if report.get("target_name"):
        lines.append(
            f"   {esc(report.get('target_name'))}, {report.get('target_age') or '—'} · "
            f"{esc(report.get('target_city') or '—')}"
        )
    lines.append(
        f"   Доверие: {report.get('trust_score', 0)}/100 · "
        f"предупреждений: {report.get('warns', 0)}"
    )
    open_on_target = int(report.get("open_on_target") or 0)
    if open_on_target > 1:
        lines.append(f"   ⚠️ Открытых жалоб на этого человека: <b>{open_on_target}</b>")

    lines.append(f"\n🙋 От кого: <code>{report.get('reporter_id')}</code>")

    comment = (report.get("comment") or "").strip()
    if comment:
        lines.append(f"\n💬 <b>Комментарий:</b>\n<i>{esc(shorten(comment, 600))}</i>")
    else:
        lines.append("\n💬 <i>Без комментария</i>")

    return "\n".join(lines)


def appeal_card(appeal: dict[str, Any], *, position: int = 1, total: int = 1) -> str:
    lines = [
        f"📨 <b>Апелляция №{appeal.get('id')}</b>  <i>({position} из {total})</i>",
        f"От: <code>{appeal.get('user_id')}</code>"
        + (f" · @{esc(appeal.get('username'))}" if appeal.get("username") else ""),
        f"Когда: {fmt_dt(appeal.get('created_at'))}",
    ]
    if appeal.get("ban_permanent"):
        lines.append("Текущее ограничение: 🚫 бессрочная блокировка")
    elif appeal.get("ban_until") and int(appeal["ban_until"]) > now():
        lines.append(f"Текущее ограничение: 🚫 блокировка до {fmt_dt(appeal['ban_until'])}")
    elif appeal.get("shadow_level") and int(appeal.get("shadow_until") or 0) > now():
        lines.append(
            f"Текущее ограничение: 🔅 снижен охват до {fmt_dt(appeal['shadow_until'])}"
        )
    else:
        lines.append("Текущее ограничение: <i>нет</i>")

    reason = appeal.get("ban_reason") or appeal.get("shadow_reason")
    if reason:
        lines.append(f"Причина ограничения: {esc(reason)}")

    lines.append(f"\n💬 <b>Текст обращения:</b>\n<i>{esc(shorten(appeal.get('text') or '', 800))}</i>")
    return "\n".join(lines)


def chat_history_text(messages: list[dict[str, Any]], names: dict[int, str]) -> str:
    if not messages:
        return "<i>Переписки нет — возможно, она уже удалена по сроку хранения.</i>"
    lines = ["📜 <b>Последние сообщения</b>", ""]
    for message in messages:
        author = names.get(int(message["from_id"]), str(message["from_id"]))
        stamp = fmt_dt(message["created_at"])
        kind = str(message.get("kind") or "text")
        if kind == "text":
            body = esc(shorten(message.get("text") or "", 300))
        else:
            icons = {
                "photo": "🖼 фото",
                "video": "🎬 видео",
                "voice": "🎤 голосовое",
                "video_note": "⭕️ кружок",
                "sticker": "🙂 стикер",
                "animation": "🎞 гифка",
                "document": "📎 файл",
            }
            body = f"<i>{icons.get(kind, kind)}</i>"
            if message.get("text"):
                body += " " + esc(shorten(message["text"], 200))
        lines.append(f"<b>{esc(author)}</b> <i>{stamp}</i>\n{body}")
    return "\n\n".join(lines)

