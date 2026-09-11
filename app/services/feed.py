"""Подбор анкет для ленты.

Порядок показа:
1. Те, кто уже лайкнул тебя — их видно первыми, так рождается больше взаимностей.
2. Анкеты без ограничений охвата.
3. Кто заходил недавно.
4. Случайный порядок внутри группы, чтобы лента не была одинаковой.

Мягкий теневой бан работает не через «скрыть навсегда», а через вероятность показа:
анкета с ограничением попадает в ленту реже, но остаётся доступной — и всегда видна
тем, кто уже поставил ей лайк.
"""
from __future__ import annotations

from typing import Any

from app.constants import ACT_PASS, MOD_OK, MOD_REVIEW, STATUS_ACTIVE
from app.db import Database
from app.services.settings import Settings
from app.utils.time import now

_BASE_FILTERS = """
        FROM profiles p
        JOIN users u ON u.id = p.user_id
       WHERE p.user_id != :me
         AND p.is_complete = 1
         AND p.is_visible = 1
         AND p.moderation IN (:mod_ok, :mod_review)
         AND u.status = :status_active
         AND u.ban_permanent = 0
         AND (u.ban_until IS NULL OR u.ban_until <= :now)
         AND u.bot_blocked = 0
         AND u.last_active_at > :inactive_cutoff
         AND p.age BETWEEN :age_min AND :age_max
         AND (:seeking = 'any' OR p.gender = :seeking)
         AND (p.seeking = 'any' OR p.seeking = :my_gender)
         AND p.age_min <= :my_age
         AND p.age_max >= :my_age
         AND (:only_my_city = 0 OR p.city_norm = :my_city)
         AND (p.only_my_city = 0 OR :my_city = '' OR p.city_norm = :my_city)
         AND NOT EXISTS (
                SELECT 1 FROM likes l
                 WHERE l.from_id = :me AND l.to_id = p.user_id
                   AND (l.action != :act_pass OR l.created_at > :pass_cutoff)
             )
         AND NOT EXISTS (
                SELECT 1 FROM blocks b
                 WHERE (b.user_id = :me AND b.target_id = p.user_id)
                    OR (b.user_id = p.user_id AND b.target_id = :me)
             )
"""


def _params(user: dict[str, Any], profile: dict[str, Any], settings: Settings) -> dict[str, Any]:
    moment = now()
    my_city = (profile.get("city_norm") or "").strip()
    only_my_city = 1 if (profile.get("only_my_city") and my_city) else 0
    return {
        "me": int(user["id"]),
        "now": moment,
        "mod_ok": MOD_OK,
        "mod_review": MOD_REVIEW,
        "status_active": STATUS_ACTIVE,
        "act_pass": ACT_PASS,
        "inactive_cutoff": moment - settings.inactive_cutoff_days * 86400,
        "pass_cutoff": moment - settings.pass_ttl,
        "age_min": int(profile.get("age_min") or 18),
        "age_max": int(profile.get("age_max") or 99),
        "seeking": profile.get("seeking") or "any",
        "my_gender": profile.get("gender") or "",
        "my_age": int(profile.get("age") or 18),
        "my_city": my_city,
        "only_my_city": only_my_city,
    }


async def next_candidate(
    db: Database, settings: Settings, user: dict[str, Any], profile: dict[str, Any]
) -> dict[str, Any] | None:
    """Возвращает следующую анкету для показа или None."""
    params = _params(user, profile, settings)
    sql = f"""
        SELECT * FROM (
            SELECT p.*, u.username, u.last_active_at, u.trust_score,
                   CASE WHEN u.shadow_level > 0 AND COALESCE(u.shadow_until, 0) > :now
                        THEN u.shadow_level ELSE 0 END AS shadow,
                   EXISTS (
                       SELECT 1 FROM likes l2
                        WHERE l2.from_id = p.user_id AND l2.to_id = :me
                          AND l2.action IN ('like', 'superlike')
                   ) AS liked_me,
                   EXISTS (
                       SELECT 1 FROM likes l3
                        WHERE l3.from_id = p.user_id AND l3.to_id = :me
                          AND l3.action = 'superlike'
                   ) AS superliked_me
            {_BASE_FILTERS}
        ) AS c
        WHERE c.shadow = 0
           OR c.liked_me = 1
           OR (ABS(RANDOM()) % 100) < (CASE c.shadow WHEN 1 THEN 70 WHEN 2 THEN 40 ELSE 15 END)
        ORDER BY c.superliked_me DESC,
                 c.liked_me DESC,
                 c.shadow ASC,
                 CASE
                     WHEN c.last_active_at > :now - 86400 THEN 0
                     WHEN c.last_active_at > :now - 604800 THEN 1
                     ELSE 2
                 END ASC,
                 RANDOM()
        LIMIT 1
    """
    return await db.fetchone(sql, params)


async def count_available(
    db: Database, settings: Settings, user: dict[str, Any], profile: dict[str, Any]
) -> int:
    """Сколько анкет доступно по текущим фильтрам (без учёта вероятности показа)."""
    params = _params(user, profile, settings)
    value = await db.fetchval(f"SELECT COUNT(*) {_BASE_FILTERS}", params, 0)
    return int(value)
