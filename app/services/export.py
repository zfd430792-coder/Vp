"""Выгрузка личных данных по запросу пользователя.

В файл попадает только то, что относится к самому человеку. Сообщения собеседников
не выгружаются: это их данные, а не его.
"""
from __future__ import annotations

import json
from typing import Any

from app.constants import INTERESTS, REPORT_CATEGORIES
from app.db import Database
from app.services import insights
from app.utils.time import fmt_dt, now


async def build(db: Database, user_id: int) -> dict[str, Any] | None:
    user = await db.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
    profile = await db.fetchone("SELECT * FROM profiles WHERE user_id = ?", (user_id,))
    if user is None or profile is None:
        return None

    photos = await db.fetchall(
        "SELECT position, created_at FROM photos WHERE user_id = ? ORDER BY position", (user_id,)
    )
    matches = await db.fetchall(
        """
        SELECT m.id, m.created_at, m.last_message_at,
               p.name AS partner_name, p.city AS partner_city
          FROM matches m
          JOIN profiles p
            ON p.user_id = CASE WHEN m.user_a = ? THEN m.user_b ELSE m.user_a END
         WHERE (m.user_a = ? OR m.user_b = ?) AND m.active = 1
         ORDER BY m.created_at
        """,
        (user_id, user_id, user_id),
    )
    my_messages = await db.fetchall(
        "SELECT match_id, kind, text, created_at FROM messages WHERE from_id = ? "
        "ORDER BY created_at",
        (user_id,),
    )
    my_reports = await db.fetchall(
        "SELECT id, category, status, created_at, resolution FROM reports WHERE reporter_id = ? "
        "ORDER BY created_at",
        (user_id,),
    )
    stats = await insights.summary(db, user_id, days=30)

    interests = [
        INTERESTS[code]
        for code in str(profile.get("interests") or "").split(",")
        if code in INTERESTS
    ]

    return {
        "выгрузка_создана": fmt_dt(now()),
        "аккаунт": {
            "telegram_id": user["id"],
            "username": user.get("username"),
            "регистрация": fmt_dt(user.get("created_at")),
            "последняя_активность": fmt_dt(user.get("last_active_at")),
            "правила_приняты": fmt_dt(user.get("rules_accepted_at")),
            "уровень_доверия": user.get("trust_score"),
            "предупреждений": user.get("warns"),
            "анкета_подтверждена": bool(user.get("verified")),
        },
        "анкета": {
            "имя": profile.get("name"),
            "возраст": profile.get("age"),
            "пол": profile.get("gender"),
            "кого_ищет": profile.get("seeking"),
            "город": profile.get("city"),
            "о_себе": profile.get("bio"),
            "интересы": interests,
            "фильтр_возраста": [profile.get("age_min"), profile.get("age_max")],
            "только_мой_город": bool(profile.get("only_my_city")),
            "только_подтверждённые": bool(profile.get("only_verified")),
            "показ_в_поиске": bool(profile.get("is_visible")),
            "статус_модерации": profile.get("moderation"),
            "фотографий": len(photos),
        },
        "статистика": {
            "лайков_отправлено": profile.get("likes_sent"),
            "лайков_получено": profile.get("likes_received"),
            "взаимных_симпатий": profile.get("matches_count"),
            "жалоб_на_анкету": profile.get("reports_count"),
            "за_30_дней": {
                "показов": stats.get("shown"),
                "лайков": stats.get("likes_in"),
                "симпатий": stats.get("matches"),
            },
        },
        "ограничения": {
            "статус": user.get("status"),
            "блокировка_до": fmt_dt(user.get("ban_until")) if user.get("ban_until") else None,
            "блокировка_бессрочная": bool(user.get("ban_permanent")),
            "причина_блокировки": user.get("ban_reason"),
            "снижение_охвата_до": (
                fmt_dt(user.get("shadow_until")) if user.get("shadow_until") else None
            ),
            "причина_снижения": user.get("shadow_reason"),
        },
        "симпатии": [
            {
                "собеседник": row.get("partner_name"),
                "город": row.get("partner_city"),
                "когда": fmt_dt(row.get("created_at")),
            }
            for row in matches
        ],
        "мои_сообщения": [
            {
                "тип": row.get("kind"),
                "текст": row.get("text"),
                "когда": fmt_dt(row.get("created_at")),
            }
            for row in my_messages
        ],
        "мои_жалобы": [
            {
                "номер": row.get("id"),
                "категория": REPORT_CATEGORIES.get(str(row.get("category")), row.get("category")),
                "статус": row.get("status"),
                "когда": fmt_dt(row.get("created_at")),
                "решение": row.get("resolution"),
            }
            for row in my_reports
        ],
    }


async def to_json(db: Database, user_id: int) -> bytes | None:
    data = await build(db, user_id)
    if data is None:
        return None
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
