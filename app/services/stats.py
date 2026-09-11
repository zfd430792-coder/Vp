"""Сводная статистика для админ-панели."""
from __future__ import annotations

from typing import Any

from app.constants import DAY, MOD_HOLD, MOD_REVIEW
from app.db import Database
from app.utils.time import day_start, now


async def overview(db: Database) -> dict[str, Any]:
    moment = now()
    today = day_start()
    day_ago = moment - DAY
    week_ago = moment - 7 * DAY

    async def count(sql: str, params: tuple = ()) -> int:
        return int(await db.fetchval(sql, params, 0))

    return {
        "users_total": await count("SELECT COUNT(*) FROM users WHERE status != 'deleted'"),
        "users_new_today": await count("SELECT COUNT(*) FROM users WHERE created_at >= ?", (today,)),
        "users_new_week": await count("SELECT COUNT(*) FROM users WHERE created_at >= ?", (week_ago,)),
        "users_active_day": await count(
            "SELECT COUNT(*) FROM users WHERE last_active_at >= ?", (day_ago,)
        ),
        "users_active_week": await count(
            "SELECT COUNT(*) FROM users WHERE last_active_at >= ?", (week_ago,)
        ),
        "profiles_complete": await count("SELECT COUNT(*) FROM profiles WHERE is_complete = 1"),
        "profiles_hidden": await count(
            "SELECT COUNT(*) FROM profiles WHERE is_complete = 1 AND is_visible = 0"
        ),
        "profiles_moderation": await count(
            "SELECT COUNT(*) FROM profiles WHERE moderation IN (?, ?)", (MOD_HOLD, MOD_REVIEW)
        ),
        "likes_day": await count(
            "SELECT COUNT(*) FROM likes WHERE action IN ('like','superlike') AND created_at >= ?",
            (day_ago,),
        ),
        "passes_day": await count(
            "SELECT COUNT(*) FROM likes WHERE action = 'pass' AND created_at >= ?", (day_ago,)
        ),
        "matches_total": await count("SELECT COUNT(*) FROM matches"),
        "matches_day": await count("SELECT COUNT(*) FROM matches WHERE created_at >= ?", (day_ago,)),
        "messages_day": await count(
            "SELECT COUNT(*) FROM messages WHERE created_at >= ?", (day_ago,)
        ),
        "reports_open": await count("SELECT COUNT(*) FROM reports WHERE status = 'open'"),
        "reports_day": await count("SELECT COUNT(*) FROM reports WHERE created_at >= ?", (day_ago,)),
        "reports_resolved_week": await count(
            "SELECT COUNT(*) FROM reports WHERE status = 'resolved' AND handled_at >= ?",
            (week_ago,),
        ),
        "appeals_open": await count("SELECT COUNT(*) FROM appeals WHERE status = 'open'"),
        "bans_active": await count(
            "SELECT COUNT(*) FROM users WHERE ban_permanent = 1 OR ban_until > ?", (moment,)
        ),
        "shadow_active": await count(
            "SELECT COUNT(*) FROM users WHERE shadow_level > 0 AND shadow_until > ?", (moment,)
        ),
        "bot_blocked": await count("SELECT COUNT(*) FROM users WHERE bot_blocked = 1"),
        "registrations_hour": await count(
            "SELECT COUNT(*) FROM users WHERE created_at >= ?", (moment - 3600,)
        ),
    }


async def top_sources(db: Database, limit: int = 5) -> list[dict[str, Any]]:
    return await db.fetchall(
        """
        SELECT COALESCE(source, 'прямой вход') AS source, COUNT(*) AS total
          FROM users
         WHERE created_at >= ?
         GROUP BY source
         ORDER BY total DESC
         LIMIT ?
        """,
        (now() - 7 * DAY, limit),
    )
