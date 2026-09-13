"""Инлайн-клавиатуры."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks import (
    AdminCB,
    CaptchaCB,
    FeedCB,
    LikesCB,
    MatchCB,
    ProfileCB,
    RegCB,
    ReportCB,
    SettingsCB,
)
from app.constants import (
    GENDER_ICONS,
    GENDERS,
    INTERESTS,
    RADIUS_CHOICES,
    REPORT_CATEGORIES,
    ROLE_NAMES,
)
from app.services import geo
from app.utils.text import shorten

# --------------------------------------------------------------------------- регистрация


def start_profile(*, resume: bool = False) -> InlineKeyboardMarkup:
    """Кнопка под приветствием. Предупреждение показываем только после нажатия."""
    builder = InlineKeyboardBuilder()
    label = "📝 Продолжить анкету" if resume else "📝 Создать анкету"
    builder.button(text=label, callback_data=RegCB(action="begin"))
    return builder.as_markup()


def warning_accept() -> InlineKeyboardMarkup:
    """Кнопки под предупреждением. Показываются не сразу — после паузы на чтение."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принять и продолжить", callback_data=RegCB(action="rules_ok"))
    builder.button(text="🛡 Подробнее о безопасности", callback_data=RegCB(action="safety"))
    builder.adjust(1)
    return builder.as_markup()


def after_safety() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принять и продолжить", callback_data=RegCB(action="rules_ok"))
    builder.button(text="⬅️ Назад", callback_data=RegCB(action="rules_back"))
    builder.adjust(1)
    return builder.as_markup()


def captcha(options: Sequence[str], nonce: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for option in options:
        builder.button(text=option, callback_data=CaptchaCB(value=option, nonce=nonce))
    builder.adjust(3)
    return builder.as_markup()


def gender() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code, title in GENDERS.items():
        builder.button(
            text=f"{GENDER_ICONS.get(code, '')} Я {title}",
            callback_data=RegCB(action="gender", value=code),
        )
    builder.adjust(2)
    return builder.as_markup()


def seeking(prefix: str = "reg") -> InlineKeyboardMarkup:
    options = [("m", "👨 Парней"), ("f", "👩 Девушек"), ("any", "🌈 Всех")]
    builder = InlineKeyboardBuilder()
    for code, title in options:
        if prefix == "reg":
            builder.button(text=title, callback_data=RegCB(action="seeking", value=code))
        else:
            builder.button(text=title, callback_data=SettingsCB(action="seeking", value=code))
    builder.adjust(3)
    return builder.as_markup()


def interests(selected: Iterable[str], *, editing: bool = False) -> InlineKeyboardMarkup:
    chosen = set(selected)
    builder = InlineKeyboardBuilder()
    action = "edit_interest" if editing else "interest"
    for code, title in INTERESTS.items():
        mark = "✅ " if code in chosen else ""
        builder.button(
            text=f"{mark}{title}",
            callback_data=(ProfileCB(action=action, value=code) if editing else RegCB(action=action, value=code)),
        )
    builder.adjust(2)
    done = (
        ProfileCB(action="interests_done")
        if editing
        else RegCB(action="interests_done")
    )
    builder.row(InlineKeyboardButton(text="➡️ Готово", callback_data=done.pack()))
    return builder.as_markup()


def city_choice(matches: Sequence[Any], *, editing: bool = False) -> InlineKeyboardMarkup:
    """Выбор города из найденных совпадений."""
    builder = InlineKeyboardBuilder()
    for city in matches:
        value = geo.normalize(city.name)
        callback = (
            ProfileCB(action="city_pick", value=value)
            if editing
            else RegCB(action="city", value=value)
        )
        builder.button(text=city.title, callback_data=callback)
    builder.adjust(1)
    return builder.as_markup()


def radius_menu(current: int) -> InlineKeyboardMarkup:
    """Радиус поиска: от «только мой город» до «без ограничений»."""
    builder = InlineKeyboardBuilder()
    for value, title in RADIUS_CHOICES:
        mark = "✅ " if value == current else ""
        builder.button(
            text=f"{mark}{title}", callback_data=SettingsCB(action="radius", value=str(value))
        )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=SettingsCB(action="filters").pack())
    )
    return builder.as_markup()


def skip_step(action: str = "skip") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⏭ Пропустить", callback_data=RegCB(action=action))
    return builder.as_markup()


def photos_done(count: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if count:
        builder.button(text=f"✅ Готово ({count})", callback_data=RegCB(action="photos_done"))
    return builder.as_markup()


def preview() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Всё верно, опубликовать", callback_data=RegCB(action="publish"))
    builder.button(text="✏️ Изменить", callback_data=RegCB(action="restart"))
    builder.adjust(1)
    return builder.as_markup()


# --------------------------------------------------------------------------- лента


def feed(target_id: int, *, superlikes_left: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❤️ Нравится", callback_data=FeedCB(action="like", target=target_id))
    builder.button(text="👎 Дальше", callback_data=FeedCB(action="pass", target=target_id))
    super_text = f"💥 Суперлайк ({superlikes_left})" if superlikes_left else "💥 Суперлайк"
    builder.button(text=super_text, callback_data=FeedCB(action="super", target=target_id))
    builder.button(text="🚩 Жалоба", callback_data=FeedCB(action="report", target=target_id))
    builder.adjust(2, 2)
    return builder.as_markup()


def superlike_note(target_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="💬 Добавить сообщение", callback_data=FeedCB(action="note", target=target_id)
    )
    return builder.as_markup()


def feed_empty() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⚙️ Расширить фильтры", callback_data=SettingsCB(action="filters"))
    builder.button(text="🔄 Обновить ленту", callback_data=FeedCB(action="refresh"))
    builder.adjust(1)
    return builder.as_markup()


def incoming_like(target_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❤️ Взаимно", callback_data=LikesCB(action="like", target=target_id))
    builder.button(text="👎 Не моё", callback_data=LikesCB(action="pass", target=target_id))
    builder.button(text="🚩 Жалоба", callback_data=LikesCB(action="report", target=target_id))
    builder.adjust(2, 1)
    return builder.as_markup()


def new_match(match_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Написать", callback_data=MatchCB(action="open", match_id=match_id))
    return builder.as_markup()


def likes_notify() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💌 Посмотреть", callback_data=LikesCB(action="open"))
    return builder.as_markup()


# --------------------------------------------------------------------------- симпатии и диалоги


def matches_list(
    matches: Sequence[dict[str, Any]], *, page: int, pages: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for match in matches:
        unread = int(match.get("unread") or 0)
        badge = f" 🔴{unread}" if unread else ""
        title = f"{shorten(str(match.get('name') or 'Аноним'), 20)}, {match.get('age') or '—'}{badge}"
        builder.row(
            InlineKeyboardButton(
                text=f"💬 {title}",
                callback_data=MatchCB(action="open", match_id=int(match["match_id"])).pack(),
            )
        )
    if pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️", callback_data=MatchCB(action="list", page=page - 1).pack()
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{pages}", callback_data=MatchCB(action="noop").pack()
            )
        )
        if page < pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="➡️", callback_data=MatchCB(action="list", page=page + 1).pack()
                )
            )
        builder.row(*nav)
    return builder.as_markup()


def chat_actions(match_id: int, partner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚩 Пожаловаться", callback_data=MatchCB(action="report", match_id=match_id))
    builder.button(text="⛔️ Заблокировать", callback_data=MatchCB(action="block", match_id=match_id))
    builder.adjust(2)
    return builder.as_markup()


def new_message_notice(match_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Ответить", callback_data=MatchCB(action="open", match_id=match_id))
    return builder.as_markup()


# --------------------------------------------------------------------------- жалобы


def report_categories(target_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code, title in REPORT_CATEGORIES.items():
        builder.button(
            text=title, callback_data=ReportCB(action="cat", target=target_id, value=code)
        )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Отмена", callback_data=ReportCB(action="cancel", target=target_id).pack()
        )
    )
    return builder.as_markup()


def report_comment(target_id: int, category: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⏭ Пропустить и отправить",
        callback_data=ReportCB(action="send", target=target_id, value=category),
    )
    builder.button(text="⬅️ Отмена", callback_data=ReportCB(action="cancel", target=target_id))
    builder.adjust(1)
    return builder.as_markup()


def report_notice(report_id: int) -> InlineKeyboardMarkup:
    """Кнопка для модератора: открыть жалобу прямо из уведомления."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Открыть жалобу", callback_data=AdminCB(action="rep_open", target=report_id))
    return builder.as_markup()


def after_report(target_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⛔️ Ещё и заблокировать",
        callback_data=ReportCB(action="block", target=target_id),
    )
    return builder.as_markup()


# --------------------------------------------------------------------------- анкета и настройки


def profile_menu(
    profile: dict[str, Any], *, verified: bool = False, verify_pending: bool = False
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📷 Фотографии", callback_data=ProfileCB(action="photos"))
    builder.button(text="✏️ О себе", callback_data=ProfileCB(action="bio"))
    builder.button(text="🏷 Имя", callback_data=ProfileCB(action="name"))
    builder.button(text="🎂 Возраст", callback_data=ProfileCB(action="age"))
    builder.button(text="📍 Город", callback_data=ProfileCB(action="city"))
    builder.button(text="🎯 Интересы", callback_data=ProfileCB(action="interests"))
    builder.button(text="📊 Статистика анкеты", callback_data=ProfileCB(action="insights"))
    builder.button(text="❤️ Мой лимит лайков", callback_data=ProfileCB(action="limits"))
    if not verified:
        title = "⏳ Проверка селфи идёт" if verify_pending else "✅ Подтвердить анкету"
        builder.button(text=title, callback_data=ProfileCB(action="verify"))
    if profile.get("is_visible"):
        builder.button(text="⏸ Скрыть из поиска", callback_data=ProfileCB(action="pause"))
    else:
        builder.button(text="▶️ Вернуть в поиск", callback_data=ProfileCB(action="resume"))
    builder.adjust(2, 2, 2, 2, 1, 1, 1)
    return builder.as_markup()


def verify_start() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📸 Сделать селфи", callback_data=ProfileCB(action="verify_go"))
    builder.button(text="⬅️ Назад", callback_data=ProfileCB(action="menu"))
    builder.adjust(1)
    return builder.as_markup()


def verify_actions(user_id: int, *, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Подтвердить", callback_data=AdminCB(action="vf_ok", target=user_id, page=page)
    )
    builder.button(
        text="❌ Не подходит", callback_data=AdminCB(action="vf_no", target=user_id, page=page)
    )
    builder.button(
        text="🎭 Фейк: скрыть анкету", callback_data=AdminCB(action="vf_fake", target=user_id, page=page)
    )
    builder.button(text="👤 Карточка", callback_data=AdminCB(action="user", target=user_id))
    builder.button(text="⏭ Следующая", callback_data=AdminCB(action="verify", page=page + 1))
    builder.button(text="🏠 Меню", callback_data=AdminCB(action="menu"))
    builder.adjust(2, 2, 2)
    return builder.as_markup()


def photos_menu(photos: Sequence[dict[str, Any]], *, can_add: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, photo in enumerate(photos, start=1):
        builder.button(
            text=f"🗑 Удалить фото {index}",
            callback_data=ProfileCB(action="photo_del", value=str(photo["id"])),
        )
    builder.adjust(1)
    if can_add:
        builder.row(
            InlineKeyboardButton(
                text="➕ Добавить фото", callback_data=ProfileCB(action="photo_add").pack()
            )
        )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=ProfileCB(action="menu").pack())
    )
    return builder.as_markup()


def settings_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎯 Фильтры поиска", callback_data=SettingsCB(action="filters"))
    builder.button(text="🔔 Уведомления", callback_data=SettingsCB(action="notify"))
    builder.button(text="🛡 Безопасность", callback_data=SettingsCB(action="safety"))
    builder.button(text="📨 Написать модератору", callback_data=SettingsCB(action="appeal"))
    builder.button(text="📄 Скачать мои данные", callback_data=SettingsCB(action="export"))
    builder.button(text="🗑 Удалить анкету", callback_data=SettingsCB(action="delete"))
    builder.adjust(1)
    return builder.as_markup()


def filters_menu(profile: dict[str, Any]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Кого показывать", callback_data=SettingsCB(action="seeking_menu"))
    builder.button(text="🎂 Возраст", callback_data=SettingsCB(action="age_menu"))
    radius = int(profile.get("search_radius") or 0)
    radius_title = dict(RADIUS_CHOICES).get(radius, f"до {radius} км")
    builder.button(
        text=f"📍 Где искать: {radius_title}",
        callback_data=SettingsCB(action="radius_menu"),
    )
    verified_state = "да" if profile.get("only_verified") else "нет"
    builder.button(
        text=f"✅ Только подтверждённые: {verified_state}",
        callback_data=SettingsCB(action="toggle_verified"),
    )
    builder.button(text="⬅️ Назад", callback_data=SettingsCB(action="menu"))
    builder.adjust(1)
    return builder.as_markup()


def notify_menu(user: dict[str, Any]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    items = [
        ("likes", "💌 Лайки", user.get("notify_likes")),
        ("matches", "💞 Симпатии", user.get("notify_matches")),
        ("messages", "💬 Сообщения", user.get("notify_messages")),
    ]
    for code, title, value in items:
        state = "🔔" if value else "🔕"
        builder.button(
            text=f"{state} {title}", callback_data=SettingsCB(action="toggle_notify", value=code)
        )
    builder.button(text="⬅️ Назад", callback_data=SettingsCB(action="menu"))
    builder.adjust(1)
    return builder.as_markup()


def age_filter() -> InlineKeyboardMarkup:
    presets = [("18-25", "18–25"), ("18-35", "18–35"), ("25-40", "25–40"), ("18-99", "любой")]
    builder = InlineKeyboardBuilder()
    for value, title in presets:
        builder.button(text=title, callback_data=SettingsCB(action="age_set", value=value))
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(
            text="✏️ Указать вручную", callback_data=SettingsCB(action="age_manual").pack()
        )
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=SettingsCB(action="filters").pack())
    )
    return builder.as_markup()


def confirm_delete() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Да, удалить всё", callback_data=SettingsCB(action="delete_yes"))
    builder.button(text="⏸ Лучше поставить на паузу", callback_data=ProfileCB(action="pause"))
    builder.button(text="⬅️ Отмена", callback_data=SettingsCB(action="menu"))
    builder.adjust(1)
    return builder.as_markup()


def appeal_button() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📨 Отправить апелляцию", callback_data=SettingsCB(action="appeal"))
    return builder.as_markup()


# --------------------------------------------------------------------------- админ-панель


def admin_menu(
    *,
    reports: int = 0,
    appeals: int = 0,
    moderation: int = 0,
    verify: int = 0,
    flagged: int = 0,
    overdue: int = 0,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    reports_text = f"🚩 Жалобы ({reports})" if reports else "🚩 Жалобы"
    if overdue:
        reports_text += f" ⏰{overdue}"
    builder.button(text=reports_text, callback_data=AdminCB(action="reports"))
    builder.button(
        text=f"📨 Апелляции ({appeals})" if appeals else "📨 Апелляции",
        callback_data=AdminCB(action="appeals"),
    )
    builder.button(
        text=f"🔍 Анкеты на проверке ({moderation})" if moderation else "🔍 Анкеты на проверке",
        callback_data=AdminCB(action="mod"),
    )
    builder.button(
        text=f"✅ Верификация ({verify})" if verify else "✅ Верификация",
        callback_data=AdminCB(action="verify"),
    )
    builder.button(
        text=f"🤖 Антифрод ({flagged})" if flagged else "🤖 Антифрод",
        callback_data=AdminCB(action="fraud"),
    )
    builder.button(text="👤 Найти пользователя", callback_data=AdminCB(action="find"))
    builder.button(text="📊 Статистика", callback_data=AdminCB(action="stats"))
    builder.button(text="📣 Рассылка", callback_data=AdminCB(action="bc"))
    builder.button(text="⚙️ Настройки бота", callback_data=AdminCB(action="cfg"))
    builder.button(text="👮 Команда", callback_data=AdminCB(action="staff"))
    builder.button(text="📜 Журнал действий", callback_data=AdminCB(action="log"))
    builder.button(text="🔄 Обновление бота", callback_data=AdminCB(action="upd"))
    builder.adjust(1, 1, 1, 1, 1, 1, 2, 2, 2)
    return builder.as_markup()


def admin_back(action: str = "menu", page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В админ-панель", callback_data=AdminCB(action=action, page=page))
    return builder.as_markup()


def report_actions(report_id: int, target_id: int, *, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="👤 Анкета", callback_data=AdminCB(action="rep_profile", target=report_id, page=page)
    )
    builder.button(
        text="📜 Переписка", callback_data=AdminCB(action="rep_chat", target=report_id, page=page)
    )
    builder.button(
        text="⚠️ Предупредить", callback_data=AdminCB(action="warn", target=target_id, page=page)
    )
    builder.button(
        text="🔅 Снизить охват", callback_data=AdminCB(action="shadow_menu", target=target_id, page=page)
    )
    builder.button(
        text="🚫 Заблокировать", callback_data=AdminCB(action="ban_menu", target=target_id, page=page)
    )
    builder.button(
        text="✅ Нарушений нет", callback_data=AdminCB(action="rep_reject", target=report_id, page=page)
    )
    builder.button(text="⏭ Следующая", callback_data=AdminCB(action="reports", page=page + 1))
    builder.button(text="🏠 Меню", callback_data=AdminCB(action="menu"))
    builder.adjust(2, 2, 2, 2)
    return builder.as_markup()


def ban_menu(target_id: int, *, back: str = "user", page: int = 0) -> InlineKeyboardMarkup:
    durations = [("1h", "1 час"), ("1d", "1 день"), ("7d", "7 дней"), ("30d", "30 дней")]
    builder = InlineKeyboardBuilder()
    for code, title in durations:
        builder.button(
            text=title, callback_data=AdminCB(action="ban", target=target_id, value=code, page=page)
        )
    builder.adjust(2, 2)
    builder.row(
        InlineKeyboardButton(
            text="⛔️ Навсегда",
            callback_data=AdminCB(action="ban", target=target_id, value="perm", page=page).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=AdminCB(action=back, target=target_id, page=page).pack(),
        )
    )
    return builder.as_markup()


def shadow_menu(target_id: int, *, back: str = "user", page: int = 0) -> InlineKeyboardMarkup:
    levels = [("1", "🔅 Лёгкое (70% охвата)"), ("2", "🔆 Среднее (40%)"), ("3", "🌑 Сильное (15%)")]
    builder = InlineKeyboardBuilder()
    for code, title in levels:
        builder.button(
            text=title,
            callback_data=AdminCB(action="shadow", target=target_id, value=code, page=page),
        )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=AdminCB(action=back, target=target_id, page=page).pack(),
        )
    )
    return builder.as_markup()


def user_actions(
    card: dict[str, Any],
    *,
    banned: bool,
    shadowed: bool,
    queue_size: int = 0,
    photos_blocked: bool = False,
) -> InlineKeyboardMarkup:
    target_id = int(card.get("user_id") or card.get("id"))
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Анкета", callback_data=AdminCB(action="show_profile", target=target_id))
    builder.button(text="🚩 Жалобы", callback_data=AdminCB(action="user_reports", target=target_id))
    builder.button(text="⚠️ Предупредить", callback_data=AdminCB(action="warn", target=target_id))
    if shadowed:
        builder.button(
            text="☀️ Снять ограничение охвата",
            callback_data=AdminCB(action="unshadow", target=target_id),
        )
    else:
        builder.button(
            text="🔅 Снизить охват", callback_data=AdminCB(action="shadow_menu", target=target_id)
        )
    if banned:
        builder.button(text="♻️ Разблокировать", callback_data=AdminCB(action="unban", target=target_id))
    else:
        builder.button(text="🚫 Заблокировать", callback_data=AdminCB(action="ban_menu", target=target_id))
    builder.button(
        text="🙈 Скрыть анкету", callback_data=AdminCB(action="hold", target=target_id)
    )
    builder.button(
        text="✅ Снять флаги", callback_data=AdminCB(action="clear_flags", target=target_id)
    )
    builder.button(text="🗑 Удалить фото", callback_data=AdminCB(action="wipe_photos", target=target_id))
    if photos_blocked:
        builder.button(
            text="♻️ Убрать фото из стоп-листа",
            callback_data=AdminCB(action="unban_photos", target=target_id),
        )
    else:
        builder.button(
            text="🚫 Фото в стоп-лист",
            callback_data=AdminCB(action="ban_photos", target=target_id),
        )
    if queue_size:
        builder.button(
            text=f"🚩 К жалобам ({queue_size})", callback_data=AdminCB(action="reports")
        )
    builder.button(text="🏠 Меню", callback_data=AdminCB(action="menu"))
    builder.adjust(2, 2, 2, 2, 1, 1, 1)
    return builder.as_markup()


def appeal_actions(appeal_id: int, user_id: int, *, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="👤 Карточка", callback_data=AdminCB(action="user", target=user_id)
    )
    builder.button(
        text="♻️ Снять ограничения",
        callback_data=AdminCB(action="ap_accept", target=appeal_id, page=page),
    )
    builder.button(
        text="✍️ Ответить", callback_data=AdminCB(action="ap_reply", target=appeal_id, page=page)
    )
    builder.button(
        text="❌ Отклонить", callback_data=AdminCB(action="ap_reject", target=appeal_id, page=page)
    )
    builder.button(text="⏭ Следующая", callback_data=AdminCB(action="appeals", page=page + 1))
    builder.button(text="🏠 Меню", callback_data=AdminCB(action="menu"))
    builder.adjust(2, 2, 2)
    return builder.as_markup()


def moderation_actions(user_id: int, *, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Одобрить", callback_data=AdminCB(action="mod_ok", target=user_id, page=page)
    )
    builder.button(
        text="🙈 Скрыть", callback_data=AdminCB(action="hold", target=user_id, page=page)
    )
    builder.button(
        text="🚫 Заблокировать", callback_data=AdminCB(action="ban_menu", target=user_id, page=page)
    )
    builder.button(text="👤 Карточка", callback_data=AdminCB(action="user", target=user_id))
    builder.button(text="⏭ Следующая", callback_data=AdminCB(action="mod", page=page + 1))
    builder.button(text="🏠 Меню", callback_data=AdminCB(action="menu"))
    builder.adjust(2, 2, 2)
    return builder.as_markup()


def settings_list(values: dict[str, str], descriptions: dict[str, str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, value in values.items():
        title = descriptions.get(key, key)
        builder.button(
            text=f"{title}: {value}", callback_data=AdminCB(action="cfg_edit", value=key)
        )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(text="🏠 Меню", callback_data=AdminCB(action="menu").pack())
    )
    return builder.as_markup()


def staff_list(members: Sequence[dict[str, Any]], *, can_manage: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for member in members:
        role = ROLE_NAMES.get(int(member.get("role") or 0), "?")
        label = f"{member.get('username') and '@' + str(member['username']) or member['id']} · {role}"
        if can_manage and int(member.get("role") or 0) < 3:
            builder.button(
                text=f"❌ {label}", callback_data=AdminCB(action="staff_del", target=int(member["id"]))
            )
        else:
            builder.button(text=f"👑 {label}", callback_data=AdminCB(action="noop"))
    builder.adjust(1)
    if can_manage:
        builder.row(
            InlineKeyboardButton(
                text="➕ Добавить модератора", callback_data=AdminCB(action="staff_add").pack()
            )
        )
    builder.row(InlineKeyboardButton(text="🏠 Меню", callback_data=AdminCB(action="menu").pack()))
    return builder.as_markup()


def update_menu(*, can_update: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Проверить обновления", callback_data=AdminCB(action="upd_check"))
    if can_update:
        builder.button(text="⬇️ Обновить сейчас", callback_data=AdminCB(action="upd_start"))
    builder.button(text="🏠 Меню", callback_data=AdminCB(action="menu"))
    builder.adjust(1)
    return builder.as_markup()


def update_confirm() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, обновить и перезапустить", callback_data=AdminCB(action="upd_go"))
    builder.button(text="⬅️ Отмена", callback_data=AdminCB(action="upd"))
    builder.adjust(1)
    return builder.as_markup()


def broadcast_confirm() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📣 Отправить всем", callback_data=AdminCB(action="bc_send"))
    builder.button(text="❌ Отмена", callback_data=AdminCB(action="menu"))
    builder.adjust(1)
    return builder.as_markup()


def pager(action: str, page: int, pages: int, *, target: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    if page > 0:
        row.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=AdminCB(action=action, page=page - 1, target=target).pack(),
            )
        )
    row.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{max(1, pages)}", callback_data=AdminCB(action="noop").pack()
        )
    )
    if page < pages - 1:
        row.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=AdminCB(action=action, page=page + 1, target=target).pack(),
            )
        )
    builder.row(*row)
    builder.row(InlineKeyboardButton(text="🏠 Меню", callback_data=AdminCB(action="menu").pack()))
    return builder.as_markup()
