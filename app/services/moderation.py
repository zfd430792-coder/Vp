"""Жалобы, апелляции, действия модераторов.

Принципы:
• жалобу всегда видит человек — автоматика только расставляет приоритеты;
• автоматические меры обратимы и ограничены по времени;
• массовые ложные жалобы теряют вес, чтобы травлей нельзя было выдавить человека.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.constants import (
    DAY,
    MOD_HOLD,
    MOD_OK,
    MOD_REVIEW,
    REPORT_CATEGORIES,
    REPORT_PRIORITY,
)
from app.db import Database
from app.services import profiles as profiles_service
from app.services import users as users_service
from app.services.settings import Settings
from app.utils.text import shorten
from app.utils.time import now

log = logging.getLogger(__name__)

HIGH_SEVERITY = {"minor", "blackmail", "scam", "nsfw"}
REPORT_COOLDOWN = 120  # секунд между жалобами от одного человека
REPORTS_PER_DAY = 10


@dataclass(frozen=True)
class AutoAction:
    kind: str  # review | shadow | hold
    reason: str
    shadow_level: int = 0
    shadow_hours: int = 0


# --------------------------------------------------------------------------- жалобы


async def can_report(db: Database, reporter_id: int) -> tuple[bool, str]:
    last = await db.fetchval(
        "SELECT created_at FROM reports WHERE reporter_id = ? ORDER BY created_at DESC LIMIT 1",
        (reporter_id,),
    )
    if last and now() - int(last) < REPORT_COOLDOWN:
        return False, "cooldown"
    today = int(
        await db.fetchval(
            "SELECT COUNT(*) FROM reports WHERE reporter_id = ? AND created_at > ?",
            (reporter_id, now() - DAY),
            0,
        )
    )
    if today >= REPORTS_PER_DAY:
        return False, "daily"
    return True, ""


async def has_open_report(db: Database, reporter_id: int, target_id: int) -> bool:
    row = await db.fetchone(
        "SELECT 1 FROM reports WHERE reporter_id = ? AND target_id = ? "
        "AND status IN ('open', 'in_review')",
        (reporter_id, target_id),
    )
    return row is not None


async def create_report(
    db: Database,
    *,
    reporter_id: int,
    target_id: int,
    category: str,
    comment: str | None = None,
    context: str = "feed",
    match_id: int | None = None,
) -> int:
    if category not in REPORT_CATEGORIES:
        category = "other"
    priority = REPORT_PRIORITY.get(category, 10)
    report_id = await db.insert(
        """
        INSERT INTO reports (reporter_id, target_id, category, comment, context, match_id,
                             priority, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (reporter_id, target_id, category, comment, context, match_id, priority, now()),
    )
    await db.execute(
        "UPDATE profiles SET reports_count = reports_count + 1 WHERE user_id = ?", (target_id,)
    )
    return int(report_id)


async def reporter_credibility(db: Database, reporter_id: int) -> float:
    """Вес жалобщика: у тех, чьи жалобы регулярно отклоняют, он ниже."""
    rejected = int(
        await db.fetchval(
            "SELECT COUNT(*) FROM reports WHERE reporter_id = ? AND status = 'rejected'",
            (reporter_id,),
            0,
        )
    )
    confirmed = int(
        await db.fetchval(
            "SELECT COUNT(*) FROM reports WHERE reporter_id = ? AND status = 'resolved'",
            (reporter_id,),
            0,
        )
    )
    if rejected >= 5 and rejected > confirmed * 2:
        return 0.25
    if rejected >= 2 and rejected > confirmed:
        return 0.5
    if confirmed >= 3:
        return 1.5
    return 1.0


async def report_pressure(db: Database, target_id: int, days: int = 30) -> tuple[int, float]:
    """Сколько разных людей пожаловались и какой у этого суммарный вес."""
    rows = await db.fetchall(
        """
        SELECT reporter_id, category
          FROM reports
         WHERE target_id = ? AND created_at > ? AND status IN ('open', 'in_review', 'resolved')
         GROUP BY reporter_id, category
        """,
        (target_id, now() - days * DAY),
    )
    seen: dict[int, str] = {}
    for row in rows:
        reporter = int(row["reporter_id"])
        category = str(row["category"])
        # За каждым жалобщиком считаем самую серьёзную категорию
        if reporter not in seen or category in HIGH_SEVERITY:
            seen[reporter] = category

    weight = 0.0
    for reporter, category in seen.items():
        credibility = await reporter_credibility(db, reporter)
        weight += credibility * (2.0 if category in HIGH_SEVERITY else 1.0)
    return len(seen), round(weight, 2)


async def auto_moderate(db: Database, settings: Settings, target_id: int) -> AutoAction | None:
    """Автоматическая, полностью обратимая реакция на поток жалоб."""
    unique, weight = await report_pressure(db, target_id)
    if unique == 0:
        return None

    hold_at = max(1, settings.get_int("reports_for_hold", 5))
    shadow_at = max(1, settings.get_int("reports_for_shadow", 3))
    review_at = max(1, settings.get_int("reports_for_review", 2))
    shadow_hours = max(1, settings.get_int("shadow_hours", 24))

    user = await users_service.get(db, target_id)
    if not user:
        return None

    reason = f"жалобы от {unique} разных людей"

    if weight >= hold_at:
        await profiles_service.set_moderation(db, target_id, MOD_HOLD, reason)
        await users_service.set_shadow(db, target_id, level=2, hours=shadow_hours, reason=reason)
        return AutoAction("hold", reason, shadow_level=2, shadow_hours=shadow_hours)

    if weight >= shadow_at:
        await profiles_service.set_moderation(db, target_id, MOD_REVIEW, reason)
        if not users_service.is_shadowed(user):
            await users_service.set_shadow(
                db, target_id, level=1, hours=shadow_hours, reason=reason
            )
            return AutoAction("shadow", reason, shadow_level=1, shadow_hours=shadow_hours)
        return AutoAction("review", reason)

    if weight >= review_at:
        profile = await profiles_service.get(db, target_id)
        if profile and profile.get("moderation") == MOD_OK:
            await profiles_service.set_moderation(db, target_id, MOD_REVIEW, reason)
            return AutoAction("review", reason)
    return None


# --------------------------------------------------------------------------- очередь модерации


async def queue(
    db: Database, *, status: str = "open", limit: int = 1, offset: int = 0
) -> list[dict[str, Any]]:
    return await db.fetchall(
        """
        SELECT r.*,
               p.name AS target_name, p.age AS target_age, p.city AS target_city,
               u.username AS target_username, u.trust_score, u.warns,
               u.ban_permanent, u.ban_until, u.shadow_level, u.shadow_until,
               (SELECT COUNT(*) FROM reports r2
                 WHERE r2.target_id = r.target_id AND r2.status IN ('open', 'in_review')
               ) AS open_on_target
          FROM reports r
          LEFT JOIN profiles p ON p.user_id = r.target_id
          LEFT JOIN users u ON u.id = r.target_id
         WHERE r.status = ?
         ORDER BY r.priority DESC, r.created_at ASC
         LIMIT ? OFFSET ?
        """,
        (status, limit, offset),
    )


async def queue_count(db: Database, status: str = "open") -> int:
    return int(
        await db.fetchval("SELECT COUNT(*) FROM reports WHERE status = ?", (status,), 0)
    )


async def get_report(db: Database, report_id: int) -> dict[str, Any] | None:
    return await db.fetchone(
        """
        SELECT r.*,
               p.name AS target_name, p.age AS target_age, p.city AS target_city,
               u.username AS target_username, u.trust_score, u.warns,
               u.ban_permanent, u.ban_until, u.shadow_level, u.shadow_until
          FROM reports r
          LEFT JOIN profiles p ON p.user_id = r.target_id
          LEFT JOIN users u ON u.id = r.target_id
         WHERE r.id = ?
        """,
        (report_id,),
    )


async def reports_about(db: Database, target_id: int, limit: int = 10) -> list[dict[str, Any]]:
    return await db.fetchall(
        "SELECT * FROM reports WHERE target_id = ? ORDER BY created_at DESC LIMIT ?",
        (target_id, limit),
    )


async def close_reports(
    db: Database,
    target_id: int,
    *,
    admin_id: int,
    status: str,
    resolution: str,
) -> list[dict[str, Any]]:
    """Закрывает все открытые жалобы на пользователя.

    Возвращает закрытые жалобы (id и автора), чтобы каждому можно было ответить.
    """
    rows = await db.fetchall(
        "SELECT id, reporter_id FROM reports WHERE target_id = ? AND status IN ('open', 'in_review')",
        (target_id,),
    )
    if not rows:
        return []
    await db.execute(
        """
        UPDATE reports
           SET status = ?, handled_by = ?, handled_at = ?, resolution = ?
         WHERE target_id = ? AND status IN ('open', 'in_review')
        """,
        (status, admin_id, now(), shorten(resolution, 300), target_id),
    )
    return [{"id": int(row["id"]), "reporter_id": int(row["reporter_id"])} for row in rows]


async def resolve_report(
    db: Database,
    report_id: int,
    *,
    admin_id: int,
    status: str,
    resolution: str,
) -> None:
    await db.execute(
        """
        UPDATE reports
           SET status = ?, handled_by = ?, handled_at = ?, resolution = ?
         WHERE id = ?
        """,
        (status, admin_id, now(), shorten(resolution, 300), report_id),
    )


async def overdue_reports(db: Database, settings: Settings) -> int:
    """Жалобы, которые висят дольше обещанного срока разбора."""
    hours = max(1, settings.get_int("report_sla_hours", 6))
    return int(
        await db.fetchval(
            "SELECT COUNT(*) FROM reports WHERE status = 'open' AND created_at < ?",
            (now() - hours * 3600,),
            0,
        )
    )


# --------------------------------------------------------------------------- апелляции


async def has_open_appeal(db: Database, user_id: int) -> bool:
    row = await db.fetchone(
        "SELECT 1 FROM appeals WHERE user_id = ? AND status = 'open'", (user_id,)
    )
    return row is not None


async def create_appeal(db: Database, user_id: int, text: str) -> int:
    return int(
        await db.insert(
            "INSERT INTO appeals (user_id, text, created_at) VALUES (?, ?, ?)",
            (user_id, shorten(text, 1000), now()),
        )
    )


async def appeals_queue(db: Database, limit: int = 1, offset: int = 0) -> list[dict[str, Any]]:
    return await db.fetchall(
        """
        SELECT a.*, u.username, u.status, u.ban_reason, u.ban_until, u.ban_permanent,
               u.shadow_level, u.shadow_until, u.shadow_reason, p.name
          FROM appeals a
          JOIN users u ON u.id = a.user_id
          LEFT JOIN profiles p ON p.user_id = a.user_id
         WHERE a.status = 'open'
         ORDER BY a.created_at ASC
         LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )


async def appeals_count(db: Database) -> int:
    return int(await db.fetchval("SELECT COUNT(*) FROM appeals WHERE status = 'open'", (), 0))


async def get_appeal(db: Database, appeal_id: int) -> dict[str, Any] | None:
    return await db.fetchone("SELECT * FROM appeals WHERE id = ?", (appeal_id,))


async def answer_appeal(
    db: Database, appeal_id: int, *, admin_id: int, answer: str, status: str = "closed"
) -> None:
    await db.execute(
        "UPDATE appeals SET status = ?, handled_by = ?, handled_at = ?, answer = ? WHERE id = ?",
        (status, admin_id, now(), shorten(answer, 1000), appeal_id),
    )


# --------------------------------------------------------------------------- журнал


async def log_action(
    db: Database,
    admin_id: int,
    action: str,
    *,
    target_id: int | None = None,
    details: Any = None,
) -> None:
    if details is not None and not isinstance(details, str):
        details = json.dumps(details, ensure_ascii=False)
    await db.execute(
        "INSERT INTO admin_log (admin_id, action, target_id, details, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (admin_id, action, target_id, details, now()),
    )
    log.info("admin=%s action=%s target=%s %s", admin_id, action, target_id, details or "")


async def recent_log(db: Database, limit: int = 15, offset: int = 0) -> list[dict[str, Any]]:
    return await db.fetchall(
        """
        SELECT l.*, u.username AS admin_username
          FROM admin_log l
          LEFT JOIN users u ON u.id = l.admin_id
         ORDER BY l.created_at DESC
         LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )


async def log_count(db: Database) -> int:
    return int(await db.fetchval("SELECT COUNT(*) FROM admin_log", (), 0))


# --------------------------------------------------------------------------- очередь анкет


async def profile_queue(db: Database, *, limit: int = 1, offset: int = 0) -> list[dict[str, Any]]:
    """Анкеты, которые ждут глаз модератора."""
    return await db.fetchall(
        """
        SELECT p.*, u.username, u.trust_score, u.status, u.created_at AS user_created_at,
               u.last_active_at, u.warns, u.shadow_level, u.shadow_until,
               u.ban_permanent, u.ban_until, u.role, u.bot_blocked
          FROM profiles p
          JOIN users u ON u.id = p.user_id
         WHERE p.moderation IN (?, ?)
         ORDER BY CASE p.moderation WHEN ? THEN 0 ELSE 1 END, p.updated_at ASC
         LIMIT ? OFFSET ?
        """,
        (MOD_HOLD, MOD_REVIEW, MOD_HOLD, limit, offset),
    )


async def profile_queue_count(db: Database) -> int:
    return int(
        await db.fetchval(
            "SELECT COUNT(*) FROM profiles WHERE moderation IN (?, ?)",
            (MOD_HOLD, MOD_REVIEW),
            0,
        )
    )
