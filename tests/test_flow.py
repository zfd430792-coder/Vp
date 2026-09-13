"""Интеграционная проверка логики бота без обращения к Telegram.

Запуск: python tests/test_flow.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.constants import (
    ACT_LIKE,
    ACT_PASS,
    ACT_SUPERLIKE,
    MOD_HOLD,
    MOD_OK,
    MOD_REVIEW,
)
from app.db import Database
from app.services import (
    antifraud,
    chat,
    feed,
    geo,
    insights,
    likes,
    limits,
    moderation,
    profiles,
    stats,
    users,
    verification,
)
from app.services.scheduler import Maintenance
from app.services.settings import Settings
from app.utils.time import now

PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(label)
        print(f"  ✅ {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  ❌ {label} {detail}")


class FakeBot:
    """Заглушка Telegram: собирает отправленные сообщения."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.id = 1

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.sent.append((chat_id, text))
        return type("Msg", (), {"message_id": len(self.sent)})()

    async def copy_message(self, chat_id: int, from_chat_id: int, message_id: int, **kwargs):
        self.sent.append((chat_id, f"copy:{message_id}"))
        return type("Msg", (), {"message_id": len(self.sent)})()

    async def send_photo(self, chat_id: int, photo: str, **kwargs):
        self.sent.append((chat_id, f"photo:{photo}"))
        return type("Msg", (), {"message_id": len(self.sent)})()

    async def send_media_group(self, chat_id: int, media, **kwargs):
        self.sent.append((chat_id, f"album:{len(media)}"))
        return [type("Msg", (), {"message_id": len(self.sent)})()]

    def texts_for(self, chat_id: int) -> list[str]:
        return [text for target, text in self.sent if target == chat_id]


async def make_user(
    db: Database,
    user_id: int,
    *,
    name: str,
    age: int,
    gender: str,
    seeking: str = "any",
    city: str = "Москва",
    bio: str = "Обычное описание для теста: люблю горы, кофе и настольные игры.",
    photos: int = 2,
    search_radius: int = 0,
) -> None:
    await users.ensure(db, user_id, username=f"user{user_id}", tg_name=name)
    await users.accept_rules(db, user_id)
    await profiles.ensure(db, user_id)
    await profiles.update(
        db,
        user_id,
        name=name,
        age=age,
        gender=gender,
        seeking=seeking,
        city=city,
        bio=bio,
        search_radius=search_radius,
        age_min=18,
        age_max=99,
    )
    for index in range(photos):
        await profiles.add_photo(db, user_id, f"file_{user_id}_{index}", f"uniq_{user_id}_{index}")
    await profiles.mark_complete(db, user_id)


async def main() -> None:
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    db = Database(tmp)
    await db.connect()
    settings = Settings(db)
    await settings.load()
    bot = FakeBot()

    print("\n▶ Схема и настройки")
    version = await db.fetchval("PRAGMA user_version")
    check("миграции применились", int(version) >= 1, f"version={version}")
    await settings.set("likes_per_day", "3")
    await settings.set("likes_per_day_new", "3")
    check("настройка сохраняется", settings.get_int("likes_per_day") == 3)
    settings2 = Settings(db)
    await settings2.load()
    check("настройка читается из базы", settings2.get_int("likes_per_day") == 3)

    print("\n▶ Регистрация профилей")
    await make_user(db, 1001, name="Артём", age=27, gender="m", seeking="f")
    await make_user(db, 1002, name="Аня", age=25, gender="f", seeking="m")
    await make_user(db, 1003, name="Лена", age=24, gender="f", seeking="m")
    await make_user(db, 1004, name="Оля", age=31, gender="f", seeking="m")
    await make_user(db, 1005, name="Ника", age=23, gender="f", seeking="m", city="Казань")
    await make_user(db, 1006, name="Марк", age=29, gender="m", seeking="f")
    for uid in (1001, 1002, 1003, 1004, 1005, 1006):
        await db.execute("UPDATE users SET last_active_at = ? WHERE id = ?", (now(), uid))

    profile1 = await profiles.get(db, 1001)
    check("анкета заполнена", bool(profile1 and profile1["is_complete"]))
    check("город нормализован", profile1["city_norm"] == "москва", profile1["city_norm"])
    check("фото сохранены", len(await profiles.photos(db, 1001)) == 2)

    print("\n▶ Лента: фильтры")
    user1 = await users.get(db, 1001)
    available = await feed.count_available(db, settings, user1, profile1)
    check("в ленте только девушки из Москвы", available == 3, f"available={available}")

    await profiles.update(db, 1001, age_min=18, age_max=26)
    profile1 = await profiles.get(db, 1001)
    available = await feed.count_available(db, settings, user1, profile1)
    check("фильтр возраста работает", available == 2, f"available={available}")

    await profiles.update(db, 1001, search_radius=999)
    profile1 = await profiles.get(db, 1001)
    available = await feed.count_available(db, settings, user1, profile1)
    check("без ограничения города Ника не появляется (её фильтр города)", available == 2, f"{available}")

    await profiles.update(db, 1005, search_radius=999)
    profile1 = await profiles.get(db, 1001)
    available = await feed.count_available(db, settings, user1, profile1)
    check("взаимный фильтр города учтён", available == 3, f"available={available}")

    await profiles.update(db, 1001, search_radius=0, age_min=18, age_max=99)
    profile1 = await profiles.get(db, 1001)
    candidate = await feed.next_candidate(db, settings, user1, profile1)
    check("кандидат найден", candidate is not None and candidate["gender"] == "f")

    print("\n▶ Лайки и взаимные симпатии")
    result = await likes.act(db, 1001, 1002, ACT_LIKE)
    check("лайк записан", result.recorded and not result.matched)
    back = await likes.act(db, 1002, 1001, ACT_LIKE)
    check("взаимность найдена", back.matched and back.created_match and back.match_id)
    match_id = back.match_id

    again = await likes.act(db, 1001, 1002, ACT_LIKE)
    check("повторная пара не создаётся", again.matched and not again.created_match)
    total_matches = await db.fetchval("SELECT COUNT(*) FROM matches")
    check("в базе ровно одна пара", int(total_matches) == 1, f"{total_matches}")

    counters = await profiles.get(db, 1002)
    check("счётчик симпатий обновлён", int(counters["matches_count"]) == 1, str(counters["matches_count"]))
    check("счётчик входящих лайков", int(counters["likes_received"]) == 1, str(counters["likes_received"]))

    profile1 = await profiles.get(db, 1001)
    check(
        "оценённая анкета выпадает из ленты",
        await feed.count_available(db, settings, user1, profile1) == 2,
    )

    print("\n▶ Лимиты")
    user1 = await users.get(db, 1001)
    quota = await limits.quota(db, user1, settings)
    check("лимит новичка применён", quota.limit == 3, f"limit={quota.limit}")
    check("израсходован один лайк", quota.used == 1, f"used={quota.used}")

    await likes.act(db, 1001, 1003, ACT_LIKE)
    await likes.act(db, 1001, 1004, ACT_SUPERLIKE)
    quota = await limits.quota(db, user1, settings)
    check("лимит исчерпан", quota.exhausted and quota.left == 0, f"used={quota.used}")
    check("время до сброса посчитано", 0 < quota.reset_in <= 86400, f"reset={quota.reset_in}")

    await likes.act(db, 1001, 1005, ACT_LIKE, source="inbox")
    quota = await limits.quota(db, user1, settings)
    check("ответный лайк не тратит лимит", quota.used == 3, f"used={quota.used}")

    await likes.act(db, 1006, 1002, ACT_PASS)
    quota6 = await limits.quota(db, await users.get(db, 1006), settings)
    check("дизлайк не тратит лимит", quota6.used == 0, f"used={quota6.used}")

    print("\n▶ Входящие лайки")
    await likes.act(db, 1006, 1003, ACT_SUPERLIKE)
    # Лену (1003) ранее лайкнул Артём (1001), теперь добавился суперлайк от Марка (1006)
    incoming = await likes.incoming_count(db, 1003)
    check("входящие лайки видны", incoming == 2, f"incoming={incoming}")
    next_like = await likes.next_incoming(db, 1003)
    check(
        "суперлайк показывается первым",
        next_like is not None
        and next_like["like_action"] == "superlike"
        and int(next_like["user_id"]) == 1006,
    )
    await likes.act(db, 1003, 1006, ACT_PASS, source="inbox")
    left = await likes.incoming_count(db, 1003)
    check("отвеченный лайк уходит из списка", left == 1, f"left={left}")
    next_like = await likes.next_incoming(db, 1003)
    check("следующим показывается обычный лайк", next_like is not None and int(next_like["user_id"]) == 1001)
    await likes.act(db, 1003, 1001, ACT_PASS, source="inbox")
    check("список входящих опустел", await likes.incoming_count(db, 1003) == 0)

    print("\n▶ Диалог и хранение переписки")
    await chat.save_message(db, match_id=match_id, from_id=1001, to_id=1002, kind="text", text="Привет!")
    await chat.save_message(db, match_id=match_id, from_id=1002, to_id=1001, kind="photo", text=None, file_id="ph1")
    history = await chat.history(db, match_id)
    check("история сохранена по порядку", len(history) == 2 and history[0]["text"] == "Привет!")
    unread = await likes.total_unread(db, 1002)
    check("непрочитанное считается", unread == 1, f"unread={unread}")
    await chat.mark_read(db, match_id, 1002)
    check("после прочтения счётчик обнулился", await likes.total_unread(db, 1002) == 0)
    rows = await likes.list_matches(db, 1001)
    check("пара в списке", len(rows) == 1 and rows[0]["partner_id"] == 1002)

    print("\n▶ Жалобы и автомодерация")
    await settings.set("reports_for_review", "2")
    await settings.set("reports_for_shadow", "3")
    await settings.set("reports_for_hold", "5")

    report_id = await moderation.create_report(
        db, reporter_id=1002, target_id=1006, category="ads", comment="Реклама канала"
    )
    check("жалоба создана", report_id > 0)
    check("дубликат определяется", await moderation.has_open_report(db, 1002, 1006))

    action = await moderation.auto_moderate(db, settings, 1006)
    check("одной жалобы мало для мер", action is None, str(action))

    await moderation.create_report(db, reporter_id=1003, target_id=1006, category="ads")
    action = await moderation.auto_moderate(db, settings, 1006)
    check("две жалобы → проверка", action is not None and action.kind == "review", str(action))

    await moderation.create_report(db, reporter_id=1004, target_id=1006, category="ads")
    action = await moderation.auto_moderate(db, settings, 1006)
    check("три жалобы → снижение охвата", action is not None and action.kind == "shadow", str(action))
    user6 = await users.get(db, 1006)
    check("мягкое ограничение активно", users.is_shadowed(user6) and users.shadow_reach(user6) == 70)
    check("анкета остаётся видимой", (await profiles.get(db, 1006))["moderation"] == MOD_REVIEW)

    await moderation.create_report(db, reporter_id=1005, target_id=1006, category="scam")
    action = await moderation.auto_moderate(db, settings, 1006)
    check("серьёзные жалобы → скрытие анкеты", action is not None and action.kind == "hold", str(action))
    check("анкета скрыта", (await profiles.get(db, 1006))["moderation"] == MOD_HOLD)

    user6 = await users.get(db, 1006)
    check("автоматика не выдаёт бан", not users.is_banned(user6))

    unique, weight = await moderation.report_pressure(db, 1006)
    check("давление жалоб посчитано", unique == 4 and weight > 4, f"{unique}/{weight}")

    print("\n▶ Скрытая анкета не попадает в ленту")
    user4 = await users.get(db, 1004)
    await profiles.update(db, 1004, seeking="m")
    visible_for_olya = await feed.count_available(db, settings, user4, await profiles.get(db, 1004))
    ids = await db.fetchall(
        "SELECT user_id FROM profiles WHERE moderation = ? AND is_complete = 1", (MOD_HOLD,)
    )
    check("в скрытых ровно один", len(ids) == 1 and ids[0]["user_id"] == 1006)
    check("скрытая анкета исключена из выдачи", visible_for_olya == 1, f"{visible_for_olya}")

    print("\n▶ Репутация жалобщиков")
    for _ in range(5):
        rid = await moderation.create_report(db, reporter_id=1005, target_id=1001, category="other")
        await moderation.resolve_report(
            db, rid, admin_id=1, status="rejected", resolution="нарушений нет"
        )
    credibility = await moderation.reporter_credibility(db, 1005)
    check("ложные жалобы теряют вес", credibility <= 0.5, f"credibility={credibility}")

    print("\n▶ Блокировки и разблокировки")
    await users.ban(db, 1006, reason="мошенничество", seconds=3600, by=1)
    user6 = await users.get(db, 1006)
    check("бан действует", users.is_banned(user6))
    banned_visible = await db.fetchval(
        "SELECT COUNT(*) FROM profiles p JOIN users u ON u.id = p.user_id "
        "WHERE p.user_id = 1006 AND u.status = 'active'",
        (),
        0,
    )
    check("заблокированный выпадает из поиска", int(banned_visible) == 0)
    check("пауза анкеты не тронута баном", (await profiles.get(db, 1006))["is_visible"] == 1)
    await users.unban(db, 1006)
    check("разбан снимает ограничение", not users.is_banned(await users.get(db, 1006)))

    await users.ban(db, 1006, reason="тест", seconds=None, by=1)
    check("бессрочный бан", users.ban_left(await users.get(db, 1006)) == -1)
    await users.unban(db, 1006)

    print("\n▶ Фоновые задачи")
    await users.ban(db, 1002, reason="тест срока", seconds=60, by=1)
    await db.execute("UPDATE users SET ban_until = ? WHERE id = ?", (now() - 10, 1002))
    await users.set_shadow(db, 1003, level=2, hours=1, reason="тест")
    await db.execute(
        "UPDATE users SET shadow_until = ?, shadow_notified = 1 WHERE id = ?", (now() - 10, 1003)
    )

    class Cfg:
        moderation_chat_id = None

    maintenance = Maintenance(bot, db, settings, Cfg())
    await maintenance.run_once()
    check("истёкший бан снят", not users.is_banned(await users.get(db, 1002)))
    check("истёкшее ограничение снято", not users.is_shadowed(await users.get(db, 1003)))
    check("пользователь получил уведомления", len(bot.texts_for(1002)) > 0 and len(bot.texts_for(1003)) > 0)

    print("\n▶ Антифрод")
    spam_status, reasons = await antifraud.screen_profile(
        db, 1001, {"bio": "Заработок на крипте! Пиши @scam_bot, ставки и бонусы"}
    )
    check("спам в описании ловится", spam_status == MOD_HOLD, f"{spam_status} {reasons}")

    await profiles.add_photo(db, 1002, "file_1001_0", "uniq_1001_0")
    owners = await antifraud.screen_photo(db, 1002, "uniq_1001_0")
    check("повторное фото найдено", owners == [1001], str(owners))

    await profiles.update(db, 1004, bio="Одинаковый текст анкеты для проверки")
    await profiles.update(db, 1005, bio="Одинаковый текст анкеты для проверки")
    status, reasons = await antifraud.screen_profile(db, 1005, await profiles.get(db, 1005))
    check("одинаковые описания замечены", status != MOD_OK and any("описание" in r for r in reasons), str(reasons))

    risk = await antifraud.risk_score(db, 1001)
    check("риск накапливается", risk > 0, f"risk={risk}")
    flagged = await antifraud.flagged_users(db)
    check("список подозрительных строится", len(flagged) > 0)

    for _ in range(12):
        antifraud.tracker.push(999, "like")
    check("трекер темпа работает", antifraud.tracker.like_ratio(999)[0] == 1.0)

    print("\n▶ Доверие и лимиты")
    trust_before = await users.recompute_trust(db, 1001)
    await db.execute("UPDATE users SET created_at = ? WHERE id = ?", (now() - 20 * 86400, 1001))
    trust_after = await users.recompute_trust(db, 1001)
    check("возраст аккаунта повышает доверие", trust_after > trust_before, f"{trust_before}→{trust_after}")
    await settings.set("likes_per_day", "60")
    await settings.set("likes_per_day_new", "30")
    user1 = await users.get(db, 1001)
    parts = await limits.breakdown(db, user1, settings)
    check("база лимита — 30", parts.base == 30, str(parts.base))
    check(
        "за заполненную анкету есть надбавка",
        any("анкета" in label for label, _ in parts.parts),
        str(parts.parts),
    )
    check(
        "верификация предложена как способ поднять лимит",
        any("подтвердить" in label for label, _ in parts.available),
        str(parts.available),
    )
    await db.execute("UPDATE users SET verified = 1 WHERE id = ?", (1001,))
    verified_parts = await limits.breakdown(db, await users.get(db, 1001), settings)
    check(
        "верификация поднимает лимит",
        verified_parts.total > parts.total,
        f"{parts.total} → {verified_parts.total}",
    )
    check("лимит не выше потолка", verified_parts.total <= 60, str(verified_parts.total))
    await db.execute("UPDATE users SET verified = 0 WHERE id = ?", (1001,))

    print("\n▶ Верификация")
    fresh = await users.get(db, 1002)
    allowed, reason = await verification.can_request(db, fresh)
    check("проверку можно запросить", allowed, reason)

    await verification.start(db, 1002, "покажи два пальца ✌️")
    await verification.submit(db, 1002, "selfie_file_1002")
    pending = await users.get(db, 1002)
    check("заявка в статусе ожидания", pending["verify_status"] == "pending")
    allowed, reason = await verification.can_request(db, pending)
    check("повторно отправить нельзя", not allowed and reason == "pending", reason)
    check("заявка в очереди", await verification.queue_count(db) == 1)
    queue_rows = await verification.queue(db)
    check("в очереди видно жест", queue_rows and "два пальца" in str(queue_rows[0]["verify_gesture"]))

    await verification.approve(db, 1002)
    approved = await users.get(db, 1002)
    check("анкета подтверждена", bool(approved["verified"]))
    check("селфи удалено сразу после решения", approved["verify_file_id"] is None)
    check("очередь опустела", await verification.queue_count(db) == 0)
    allowed, reason = await verification.can_request(db, approved)
    check("подтверждённому повтор не нужен", not allowed and reason == "already", reason)

    await verification.revoke(db, 1002)
    check("верификацию можно отозвать", not (await users.get(db, 1002))["verified"])

    await verification.start(db, 1003, "покажи кулак 👊")
    await verification.submit(db, 1003, "selfie_file_1003")
    await verification.reject(db, 1003, "не видно лица")
    rejected = await users.get(db, 1003)
    check("отклонение сохраняет причину", rejected["verify_note"] == "не видно лица")
    check("после отказа фото не хранится", rejected["verify_file_id"] is None)
    allowed, reason = await verification.can_request(db, rejected)
    check("после отказа действует пауза", not allowed and reason == "cooldown", reason)

    print("\n▶ Релевантность ленты")
    await make_user(db, 2001, name="Ищущий", age=28, gender="m", seeking="f")
    await profiles.update(db, 2001, search_radius=0)
    await make_user(db, 2002, name="Обычная", age=26, gender="f", seeking="m")
    await make_user(db, 2003, name="Подтверждённая", age=26, gender="f", seeking="m")
    for uid in (2001, 2002, 2003):
        await db.execute("UPDATE users SET last_active_at = ? WHERE id = ?", (now(), uid))

    seeker = await users.get(db, 2001)
    seeker_profile = await profiles.get(db, 2001)
    await db.execute("UPDATE users SET verified = 1 WHERE id = ?", (2003,))
    picks = []
    for _ in range(5):
        candidate = await feed.next_candidate(db, settings, seeker, seeker_profile)
        picks.append(int(candidate["user_id"]) if candidate else 0)
    check("подтверждённая анкета показывается первой", set(picks) == {2003}, str(set(picks)))

    await profiles.update(db, 2001, only_verified=1)
    seeker_profile = await profiles.get(db, 2001)
    verified_only = await feed.count_available(db, settings, seeker, seeker_profile)
    check("фильтр по верификации работает", verified_only == 1, str(verified_only))
    candidate = await feed.next_candidate(db, settings, seeker, seeker_profile)
    check("показывается только подтверждённая анкета", candidate and int(candidate["user_id"]) == 2003)
    await profiles.update(db, 2001, only_verified=0)

    print("\n▶ Города и расстояния")
    check("точное название", geo.find("Москва")[0].name == "Москва")
    check("сокращение", geo.find("мск")[0].name == "Москва")
    check("приставка «г.» не мешает", geo.find("г. Сочи")[0].name == "Сочи")
    check("пробел вместо дефиса", geo.find("Санкт петербург")[0].name == "Санкт-Петербург")
    check("по началу названия", geo.find("новоси")[0].name == "Новосибирск")
    several = geo.find("нов")
    check("несколько совпадений", len(several) >= 3, str([c.name for c in several]))
    check("поиск по региону", any(c.region.startswith("Московская") for c in geo.find("Московская область")))
    check("поиск по стране", all(c.country == "Беларусь" for c in geo.find("Беларусь")))
    check("выдуманный город не находится", geo.find("Вымышленовка") == [])
    check("Горно-Алтайск не съеден приставкой", geo.find("Горно-Алтайск")[0].name == "Горно-Алтайск")

    resolved = geo.resolve_input("мск")
    check("разбор ввода: город", resolved.kind == "city" and resolved.city.name == "Москва")
    check("разбор ввода: выбор", geo.resolve_input("нов").kind == "choice")
    free = geo.resolve_input("Вымышленовка")
    check("разбор ввода: свободный текст", free.kind == "free" and free.name == "Вымышленовка")
    check("разбор ввода: мусор", geo.resolve_input("12345").kind == "invalid")

    spb_km = geo.distance_km(55.75, 37.62, 59.94, 30.31)
    check("Москва–Петербург около 630 км", 600 <= spb_km <= 680, f"{spb_km:.0f}")
    himki_km = geo.distance_km(55.75, 37.62, 55.90, 37.43)
    check("Москва–Химки около 20 км", 10 <= himki_km <= 30, f"{himki_km:.0f}")
    check("ближе пяти километров — «рядом»", geo.format_distance(3) == "рядом")
    check("десятки километров округляются", geo.format_distance(23) == "~25 км")
    check("сотни километров округляются", geo.format_distance(631) == "~650 км")
    check("ближайший город найден", geo.nearest(55.8, 37.5).name == "Москва")
    check("в океане города нет", geo.nearest(0.0, 0.0) is None)
    check("координаты округляются", geo.round_coords(55.7558123, 37.6172999) == (55.76, 37.62))
    check("коэффициент долготы посчитан", 0.5 < geo.cos_lat(55.75) < 0.6, str(geo.cos_lat(55.75)))

    print("\n▶ Поиск по радиусу")
    await make_user(db, 3001, name="Радиус", age=30, gender="m", seeking="f", city="Москва")
    await profiles.update(db, 3001, lat=55.75, lon=37.62, geo_source="city", search_radius=50)
    await make_user(db, 3002, name="Рядом", age=27, gender="f", seeking="m", city="Химки")
    await profiles.update(db, 3002, lat=55.90, lon=37.43, geo_source="city", search_radius=999)
    await make_user(db, 3003, name="Далеко", age=27, gender="f", seeking="m", city="Тула")
    await profiles.update(db, 3003, lat=54.19, lon=37.62, geo_source="city", search_radius=999)
    for uid in (3001, 3002, 3003):
        await db.execute("UPDATE users SET last_active_at = ? WHERE id = ?", (now(), uid))

    seeker = await users.get(db, 3001)
    check(
        "радиус 50 км оставляет только ближнюю",
        await feed.count_available(db, settings, seeker, await profiles.get(db, 3001)) == 1,
    )
    await profiles.update(db, 3001, search_radius=300)
    check(
        "радиус 300 км добавляет дальнюю",
        await feed.count_available(db, settings, seeker, await profiles.get(db, 3001)) == 2,
    )
    await profiles.update(db, 3002, search_radius=10)
    check(
        "чужой узкий радиус тоже уважается",
        await feed.count_available(db, settings, seeker, await profiles.get(db, 3001)) == 1,
    )
    await profiles.update(db, 3001, search_radius=0)
    mine = await profiles.get(db, 3001)
    # В Москве есть анкеты из более ранних сценариев, поэтому проверяем не число,
    # а именно исключение: Химки и Тула при «только мой город» появиться не могут
    seen_ids: set[int] = set()
    for _ in range(15):
        candidate = await feed.next_candidate(db, settings, seeker, mine)
        if candidate:
            seen_ids.add(int(candidate["user_id"]))
    check(
        "при «только мой город» другие города исключены",
        3002 not in seen_ids and 3003 not in seen_ids,
        str(sorted(seen_ids)),
    )
    check("но кто-то из своего города находится", bool(seen_ids), "выборка пуста")

    print("\n▶ Статистика анкеты")
    await insights.bump(db, 2002, "shown", 5)
    await insights.bump(db, 2002, "likes_in", 2)
    await insights.bump(db, 2002, "matches")
    summary = await insights.summary(db, 2002)
    check("показы суммируются", summary["shown"] == 5, str(summary))
    check("лайки суммируются", summary["likes_in"] == 2, str(summary))
    check("взаимности суммируются", summary["matches"] == 1, str(summary))
    totals = await insights.totals(db)
    check("общая сводка считается", totals["shown"] >= 5, str(totals))

    print("\n▶ Апелляции")
    appeal_id = await moderation.create_appeal(db, 1006, "Считаю блокировку ошибкой")
    check("апелляция создана", appeal_id > 0 and await moderation.has_open_appeal(db, 1006))
    queue = await moderation.appeals_queue(db)
    check("апелляция в очереди", len(queue) == 1 and queue[0]["user_id"] == 1006)
    await moderation.answer_appeal(db, appeal_id, admin_id=1, answer="Разобрались, сняли")
    check("апелляция закрыта", not await moderation.has_open_appeal(db, 1006))

    print("\n▶ Очереди модерации и журнал")
    check("очередь жалоб не пуста", await moderation.queue_count(db) > 0)
    queue_rows = await moderation.queue(db, limit=1)
    check("жалоба с приоритетом сверху", queue_rows and queue_rows[0]["category"] == "scam", str(queue_rows[0]["category"]) if queue_rows else "пусто")
    check("очередь анкет работает", await moderation.profile_queue_count(db) > 0)
    await moderation.log_action(db, 1, "test_action", target_id=1006, details={"a": 1})
    check("журнал пишется", await moderation.log_count(db) == 1)

    print("\n▶ Статистика")
    data = await stats.overview(db)
    expected_users = int(
        await db.fetchval("SELECT COUNT(*) FROM users WHERE status != 'deleted'", (), 0)
    )
    check(
        "статистика собирается",
        data["users_total"] == expected_users,
        f'{data["users_total"]} вместо {expected_users}',
    )
    check("лайки посчитаны", data["likes_day"] >= 5, str(data["likes_day"]))
    check("симпатии посчитаны", data["matches_total"] == 1)

    print("\n▶ Блокировка пользователем")
    await likes.block_user(db, 1002, 1001)
    check("блок записан", await likes.is_blocked(db, 1001, 1002))
    profile1 = await profiles.get(db, 1001)
    candidates = await feed.count_available(db, settings, await users.get(db, 1001), profile1)
    check("заблокировавший не появляется", candidates >= 0)
    match_row = await likes.match_between(db, 1001, 1002)
    check("пара закрыта после блокировки", match_row and match_row["active"] == 0)

    print("\n▶ Удаление аккаунта")
    await users.delete_account(db, 1004)
    check("анкета удалена", await profiles.get(db, 1004) is None)
    check("фото удалены", await profiles.count_photos(db, 1004) == 0)
    check("лайки удалены", await db.fetchval("SELECT COUNT(*) FROM likes WHERE from_id = 1004 OR to_id = 1004", (), 0) == 0)
    deleted_user = await users.get(db, 1004)
    check("запись для истории осталась", deleted_user and deleted_user["status"] == "deleted")
    check("жалобы на аккаунт сохранились", await db.fetchval("SELECT COUNT(*) FROM reports WHERE reporter_id = 1004", (), 0) >= 0)

    print("\n▶ Анкета видна другим, а не только владельцу")
    # Ровно тот баг: статус ставился только экраном с правилами, и человек
    # со статусом new сам ленту видел, а его анкету не видел никто.
    # Возраст берём такой, какого нет у остальных участников теста: тогда эта
    # пара видит только друг друга и проверки говорят именно о ней
    await make_user(db, 1010, name="Лена", age=55, gender="f", seeking="m", search_radius=999)
    await make_user(db, 1011, name="Пётр", age=56, gender="m", seeking="f", search_radius=999)
    await profiles.update(db, 1010, age_min=50, age_max=60)
    await profiles.update(db, 1011, age_min=50, age_max=60)
    await db.execute("UPDATE users SET status = 'new' WHERE id = 1010")

    stuck = await users.get(db, 1010)
    stuck_profile = await profiles.get(db, 1010)
    other = await users.get(db, 1011)
    other_profile = await profiles.get(db, 1011)

    check(
        "застрявший сам ленту видит",
        bool(await feed.next_candidate(db, settings, stuck, stuck_profile)),
    )
    check(
        "но другим он не показывается",
        await feed.next_candidate(db, settings, other, other_profile) is None,
    )

    await users.activate(db, 1010)
    stuck = await users.get(db, 1010)
    check("активация чинит видимость", stuck["status"] == "active")
    check(
        "теперь анкету видно",
        bool(await feed.next_candidate(db, settings, other, other_profile)),
    )
    await users.activate(db, 1011)
    check("повторная активация ничего не ломает", (await users.get(db, 1011))["status"] == "active")

    print("\n▶ Пустая лента называет причину")
    reason, found = await feed.diagnose(db, settings, other, other_profile)
    check("когда анкеты есть — причина не в фильтрах", reason in {"ok", "seen"}, reason)

    await profiles.update(db, 1011, age_min=70, age_max=80)
    narrow = await profiles.get(db, 1011)
    reason, found = await feed.diagnose(db, settings, other, narrow)
    check("узкий возраст назван причиной", reason == "age", reason)
    check("и сказано, сколько отсеяно", found >= 1, str(found))

    await profiles.update(db, 1011, age_min=50, age_max=60, only_verified=1)
    picky = await profiles.get(db, 1011)
    reason, _ = await feed.diagnose(db, settings, other, picky)
    check("фильтр «только проверенные» назван причиной", reason == "verified", reason)

    await profiles.update(db, 1011, only_verified=0, seeking="m")
    wrong = await profiles.get(db, 1011)
    reason, _ = await feed.diagnose(db, settings, other, wrong)
    check("несовпадение по полу названо причиной", reason == "gender", reason)
    await profiles.update(db, 1011, seeking="f")

    await db.close()

    print("\n" + "=" * 60)
    print(f"Пройдено: {len(PASSED)} · Провалено: {len(FAILED)}")
    if FAILED:
        for item in FAILED:
            print(f"  ❌ {item}")
        raise SystemExit(1)
    print("Все проверки пройдены ✅")


if __name__ == "__main__":
    asyncio.run(main())
