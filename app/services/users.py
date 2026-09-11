"""Пользователи: создание, роли, ограничения, доверие."""
from __future__ import annotations

import logging
from typing import Any

from app.constants import (
    DAY,
    ROLE_MODERATOR,
    ROLE_OWNER,
    ROLE_USER,
    SHADOW_REACH,
    STATUS_ACTIVE,
    STATUS_BANNED,
    STATUS_DELETED,
    STATUS_NEW,
)
from app.db import Database
from app.utils.time import now

log = logging.getLogger(__name__)

User = dict[str, Any]


async def get(db: Database, user_id: int) -> User | None:
    return await db.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))


async def get_by_username(db: Database, username: str) -> User | None:
    clean = username.strip().lstrip("@").lower()
    if not clean:
        return None
    return await db.fetchone(
        "SELECT * FROM users WHERE LOWER(username) = ? ORDER BY last_active_at DESC LIMIT 1",
        (clean,),
    )


async def ensure(
    db: Database,
    user_id: int,
    *,
    username: str | None = None,
    tg_name: str | None = None,
    referrer_id: int | None = None,
    source: str | None = None,
) -> User:
    """Возвращает пользователя, создавая запись при первом обращении."""
    moment = now()
    existing = await get(db, user_id)
    if existing is None:
        await db.execute(
            """
            INSERT INTO users (id, username, tg_name, created_at, last_active_at, referrer_id, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (user_id, username, tg_name, moment, moment, referrer_id, source),
        )
        created = await get(db, user_id)
        if created is None:  # практически невозможно, но пусть будет явно
            raise RuntimeError(f"Не удалось создать пользователя {user_id}")
        return created

    changed_username = (existing.get("username") or None) != (username or None)
    changed_name = (existing.get("tg_name") or None) != (tg_name or None)
    if changed_username or changed_name:
        await db.execute(
            "UPDATE users SET username = ?, tg_name = ? WHERE id = ?",
            (username, tg_name, user_id),
        )
        existing["username"] = username
        existing["tg_name"] = tg_name
    return existing


async def touch(db: Database, user_id: int) -> None:
    """Отмечает активность и снимает флаг «бот заблокирован»."""
    await db.execute(
        "UPDATE users SET last_active_at = ?, bot_blocked = 0 WHERE id = ?",
        (now(), user_id),
    )


async def mark_bot_blocked(db: Database, user_id: int) -> None:
    await db.execute("UPDATE users SET bot_blocked = 1 WHERE id = ?", (user_id,))


async def accept_rules(db: Database, user_id: int) -> None:
    await db.execute(
        "UPDATE users SET rules_accepted_at = ?, status = ? WHERE id = ?",
        (now(), STATUS_ACTIVE, user_id),
    )


async def set_captcha_passed(db: Database, user_id: int, passed: bool = True) -> None:
    await db.execute(
        "UPDATE users SET captcha_passed = ? WHERE id = ?", (1 if passed else 0, user_id)
    )


# --------------------------------------------------------------------------- ограничения


def is_banned(user: User | None) -> bool:
    if not user:
        return False
    if user.get("ban_permanent"):
        return True
    ban_until = user.get("ban_until") or 0
    return bool(ban_until and ban_until > now())


def ban_left(user: User) -> int:
    if user.get("ban_permanent"):
        return -1
    return max(0, int(user.get("ban_until") or 0) - now())


def is_shadowed(user: User | None) -> bool:
    if not user:
        return False
    level = int(user.get("shadow_level") or 0)
    until = int(user.get("shadow_until") or 0)
    return level > 0 and until > now()


def shadow_reach(user: User | None) -> int:
    """Какой процент обычного охвата получает анкета."""
    if not is_shadowed(user):
        return 100
    return SHADOW_REACH.get(int(user.get("shadow_level") or 0), 100)


async def ban(
    db: Database,
    user_id: int,
    *,
    reason: str,
    seconds: int | None,
    by: int,
) -> None:
    """Ограничивает доступ. ``seconds=None`` — бессрочно."""
    permanent = 1 if seconds is None else 0
    until = None if seconds is None else now() + max(60, seconds)
    await db.execute(
        """
        UPDATE users
           SET status = ?, ban_until = ?, ban_permanent = ?, ban_reason = ?, banned_by = ?
         WHERE id = ?
        """,
        (STATUS_BANNED, until, permanent, reason, by, user_id),
    )
    # Анкету специально не трогаем: заблокированных и так нет в поиске,
    # зато после разблокировки сохранится выбор самого человека (пауза или показ).


async def unban(db: Database, user_id: int) -> None:
    await db.execute(
        """
        UPDATE users
           SET status = ?, ban_until = NULL, ban_permanent = 0, ban_reason = NULL, banned_by = NULL
         WHERE id = ?
        """,
        (STATUS_ACTIVE, user_id),
    )


async def set_shadow(
    db: Database,
    user_id: int,
    *,
    level: int,
    hours: int,
    reason: str,
) -> None:
    """Мягкое ограничение охвата: анкета видна, но реже попадает в ленту."""
    level = max(1, min(3, int(level)))
    await db.execute(
        """
        UPDATE users
           SET shadow_level = ?, shadow_until = ?, shadow_reason = ?, shadow_notified = 0
         WHERE id = ?
        """,
        (level, now() + max(1, hours) * 3600, reason, user_id),
    )


async def clear_shadow(db: Database, user_id: int) -> None:
    await db.execute(
        """
        UPDATE users
           SET shadow_level = 0, shadow_until = NULL, shadow_reason = NULL, shadow_notified = 0
         WHERE id = ?
        """,
        (user_id,),
    )


async def add_warn(db: Database, user_id: int) -> int:
    await db.execute("UPDATE users SET warns = warns + 1 WHERE id = ?", (user_id,))
    return int(await db.fetchval("SELECT warns FROM users WHERE id = ?", (user_id,), 0))


async def reset_warns(db: Database, user_id: int) -> None:
    await db.execute("UPDATE users SET warns = 0 WHERE id = ?", (user_id,))


# --------------------------------------------------------------------------- роли


async def set_role(db: Database, user_id: int, role: int) -> None:
    await db.execute("UPDATE users SET role = ? WHERE id = ?", (int(role), user_id))


async def staff(db: Database) -> list[User]:
    return await db.fetchall(
        "SELECT * FROM users WHERE role >= ? ORDER BY role DESC, id", (ROLE_MODERATOR,)
    )


async def sync_owners(db: Database, owner_ids: tuple[int, ...]) -> None:
    """Владельцы из .env всегда имеют высшую роль."""
    for owner_id in owner_ids:
        existing = await get(db, owner_id)
        moment = now()
        if existing is None:
            await db.execute(
                """
                INSERT INTO users (id, created_at, last_active_at, role, status, rules_accepted_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET role = excluded.role
                """,
                (owner_id, moment, moment, ROLE_OWNER, STATUS_NEW, moment),
            )
        elif int(existing.get("role") or 0) != ROLE_OWNER:
            await set_role(db, owner_id, ROLE_OWNER)


# --------------------------------------------------------------------------- доверие


async def recompute_trust(db: Database, user_id: int) -> int:
    """Пересчитывает уровень доверия 0..100.

    Доверие влияет на лимиты и на то, насколько строго бот смотрит на аккаунт.
    """
    user = await get(db, user_id)
    if not user:
        return 0

    moment = now()
    score = 50
    age_days = max(0, (moment - int(user.get("created_at") or moment))) // DAY
    if age_days >= 1:
        score += 5
    if age_days >= 3:
        score += 5
    if age_days >= 14:
        score += 10
    if user.get("username"):
        score += 5
    if user.get("captcha_passed"):
        score += 5

    profile = await db.fetchone(
        "SELECT is_complete, matches_count FROM profiles WHERE user_id = ?", (user_id,)
    )
    if profile and profile.get("is_complete"):
        score += 5
        photos = await db.fetchval(
            "SELECT COUNT(*) FROM photos WHERE user_id = ?", (user_id,), 0
        )
        if int(photos) >= 2:
            score += 5
        if int(profile.get("matches_count") or 0) >= 1:
            score += 10

    confirmed = await db.fetchval(
        "SELECT COUNT(*) FROM reports WHERE target_id = ? AND status = 'resolved'",
        (user_id,),
        0,
    )
    score -= 15 * int(confirmed)

    open_reporters = await db.fetchval(
        "SELECT COUNT(DISTINCT reporter_id) FROM reports "
        "WHERE target_id = ? AND status IN ('open', 'in_review')",
        (user_id,),
        0,
    )
    score -= 7 * int(open_reporters)

    risk = await db.fetchval(
        "SELECT COALESCE(SUM(weight), 0) FROM events "
        "WHERE user_id = ? AND weight > 0 AND created_at > ?",
        (user_id, moment - 30 * DAY),
        0,
    )
    score -= min(40, int(risk))

    score -= 5 * int(user.get("warns") or 0)
    score = max(0, min(100, score))

    await db.execute("UPDATE users SET trust_score = ? WHERE id = ?", (score, user_id))
    return score


async def delete_account(db: Database, user_id: int) -> None:
    """Полностью удаляет данные пользователя, оставляя техническую запись.

    Запись в ``users`` остаётся со статусом ``deleted``, чтобы сохранить историю
    модерации (жалобы других людей на этот аккаунт) и не дать обойти блокировку
    удалением анкеты.
    """
    await db.execute("DELETE FROM photos WHERE user_id = ?", (user_id,))
    await db.execute("DELETE FROM profiles WHERE user_id = ?", (user_id,))
    await db.execute("DELETE FROM likes WHERE from_id = ? OR to_id = ?", (user_id, user_id))
    await db.execute(
        "DELETE FROM messages WHERE match_id IN "
        "(SELECT id FROM matches WHERE user_a = ? OR user_b = ?)",
        (user_id, user_id),
    )
    await db.execute("DELETE FROM matches WHERE user_a = ? OR user_b = ?", (user_id, user_id))
    await db.execute("DELETE FROM blocks WHERE user_id = ?", (user_id,))
    await db.execute(
        """
        UPDATE users
           SET status = ?, username = NULL, tg_name = NULL, rules_accepted_at = NULL,
               role = CASE WHEN role >= ? THEN role ELSE ? END,
               shadow_level = 0, shadow_until = NULL, shadow_reason = NULL
         WHERE id = ?
        """,
        (STATUS_DELETED, ROLE_OWNER, ROLE_USER, user_id),
    )
