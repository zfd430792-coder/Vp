"""Лайки, дизлайки, взаимные симпатии."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.constants import ACT_LIKE, ACT_PASS, ACT_SUPERLIKE, STATUS_ACTIVE
from app.db import Database
from app.utils.time import now


@dataclass(frozen=True)
class LikeResult:
    recorded: bool
    matched: bool = False
    match_id: int | None = None
    created_match: bool = False  # True только у того, кто замкнул пару


async def existing(db: Database, from_id: int, to_id: int) -> dict[str, Any] | None:
    return await db.fetchone(
        "SELECT * FROM likes WHERE from_id = ? AND to_id = ?", (from_id, to_id)
    )


def pair(user_a: int, user_b: int) -> tuple[int, int]:
    return (user_a, user_b) if user_a < user_b else (user_b, user_a)


async def act(
    db: Database,
    from_id: int,
    to_id: int,
    action: str,
    *,
    message: str | None = None,
    source: str = "feed",
) -> LikeResult:
    """Записывает действие и при взаимности создаёт пару."""
    if from_id == to_id:
        return LikeResult(recorded=False)
    if action not in {ACT_LIKE, ACT_SUPERLIKE, ACT_PASS}:
        raise ValueError(f"Неизвестное действие: {action}")

    moment = now()
    previous = await existing(db, from_id, to_id)
    await db.execute(
        """
        INSERT INTO likes (from_id, to_id, action, source, message, created_at, seen, responded)
        VALUES (?, ?, ?, ?, ?, ?, 0, 0)
        ON CONFLICT(from_id, to_id) DO UPDATE SET
            action = excluded.action,
            source = excluded.source,
            message = excluded.message,
            created_at = excluded.created_at,
            seen = 0,
            responded = 0
        """,
        (from_id, to_id, action, source, message, moment),
    )

    was_positive = bool(previous and previous["action"] in (ACT_LIKE, ACT_SUPERLIKE))
    is_positive = action in (ACT_LIKE, ACT_SUPERLIKE)

    if is_positive and not was_positive:
        await db.execute(
            "UPDATE profiles SET likes_sent = likes_sent + 1 WHERE user_id = ?", (from_id,)
        )
        await db.execute(
            "UPDATE profiles SET likes_received = likes_received + 1 WHERE user_id = ?", (to_id,)
        )

    if not is_positive:
        # Дизлайк закрывает входящий лайк, чтобы он не висел в списке
        await db.execute(
            "UPDATE likes SET responded = 1 WHERE from_id = ? AND to_id = ?", (to_id, from_id)
        )
        return LikeResult(recorded=True)

    reciprocal = await db.fetchone(
        "SELECT action FROM likes WHERE from_id = ? AND to_id = ? AND action IN (?, ?)",
        (to_id, from_id, ACT_LIKE, ACT_SUPERLIKE),
    )
    if not reciprocal:
        return LikeResult(recorded=True)

    user_a, user_b = pair(from_id, to_id)
    inserted = await db.insert(
        "INSERT OR IGNORE INTO matches (user_a, user_b, created_at) VALUES (?, ?, ?)",
        (user_a, user_b, moment),
    )
    match_row = await db.fetchone(
        "SELECT id FROM matches WHERE user_a = ? AND user_b = ?", (user_a, user_b)
    )
    match_id = int(match_row["id"]) if match_row else None

    await db.execute(
        "UPDATE likes SET responded = 1, seen = 1 "
        "WHERE (from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?)",
        (from_id, to_id, to_id, from_id),
    )

    created = bool(inserted)
    if created:
        await db.execute(
            "UPDATE profiles SET matches_count = matches_count + 1 WHERE user_id IN (?, ?)",
            (from_id, to_id),
        )
    else:
        # Пара уже была (например, её закрывали и лайкнули снова) — оживляем её
        await db.execute(
            "UPDATE matches SET active = 1, closed_by = NULL WHERE id = ?", (match_id,)
        )

    return LikeResult(recorded=True, matched=True, match_id=match_id, created_match=created)


# --------------------------------------------------------------------------- входящие лайки


_INCOMING_FILTER = """
      FROM likes l
      JOIN users u ON u.id = l.from_id
      JOIN profiles p ON p.user_id = l.from_id
     WHERE l.to_id = :me
       AND l.action IN ('like', 'superlike')
       AND l.responded = 0
       AND p.is_complete = 1
       AND u.status = :status_active
       AND u.ban_permanent = 0
       AND (u.ban_until IS NULL OR u.ban_until <= :now)
       AND NOT EXISTS (
              SELECT 1 FROM likes mine
               WHERE mine.from_id = :me AND mine.to_id = l.from_id
           )
       AND NOT EXISTS (
              SELECT 1 FROM blocks b
               WHERE (b.user_id = :me AND b.target_id = l.from_id)
                  OR (b.user_id = l.from_id AND b.target_id = :me)
           )
"""


async def incoming_count(db: Database, user_id: int) -> int:
    params = {"me": user_id, "status_active": STATUS_ACTIVE, "now": now()}
    return int(await db.fetchval(f"SELECT COUNT(*) {_INCOMING_FILTER}", params, 0))


async def next_incoming(db: Database, user_id: int) -> dict[str, Any] | None:
    """Следующий входящий лайк: суперлайки вперёд, потом свежие."""
    params = {"me": user_id, "status_active": STATUS_ACTIVE, "now": now()}
    return await db.fetchone(
        f"""
        SELECT p.*, u.username, u.last_active_at, l.action AS like_action,
               l.message AS like_message, l.created_at AS liked_at
        {_INCOMING_FILTER}
        ORDER BY CASE l.action WHEN 'superlike' THEN 0 ELSE 1 END, l.created_at DESC
        LIMIT 1
        """,
        params,
    )


async def mark_seen(db: Database, from_id: int, to_id: int) -> None:
    await db.execute(
        "UPDATE likes SET seen = 1 WHERE from_id = ? AND to_id = ?", (from_id, to_id)
    )


async def unseen_likes_count(db: Database, user_id: int) -> int:
    return int(
        await db.fetchval(
            "SELECT COUNT(*) FROM likes WHERE to_id = ? AND seen = 0 AND responded = 0 "
            "AND action IN (?, ?)",
            (user_id, ACT_LIKE, ACT_SUPERLIKE),
            0,
        )
    )


# --------------------------------------------------------------------------- пары


async def match_by_id(db: Database, match_id: int) -> dict[str, Any] | None:
    return await db.fetchone("SELECT * FROM matches WHERE id = ?", (match_id,))


async def match_between(db: Database, user_a: int, user_b: int) -> dict[str, Any] | None:
    first, second = pair(user_a, user_b)
    return await db.fetchone(
        "SELECT * FROM matches WHERE user_a = ? AND user_b = ?", (first, second)
    )


def partner_id(match: dict[str, Any], user_id: int) -> int:
    return int(match["user_b"]) if int(match["user_a"]) == user_id else int(match["user_a"])


async def list_matches(
    db: Database, user_id: int, *, limit: int = 8, offset: int = 0
) -> list[dict[str, Any]]:
    return await db.fetchall(
        """
        SELECT m.id AS match_id,
               m.created_at,
               m.last_message_at,
               CASE WHEN m.user_a = :me THEN m.user_b ELSE m.user_a END AS partner_id,
               CASE WHEN m.user_a = :me THEN m.last_read_a ELSE m.last_read_b END AS last_read,
               p.name, p.age, p.city, p.gender,
               u.status AS partner_status,
               u.ban_permanent, u.ban_until,
               (SELECT COUNT(*) FROM messages msg
                 WHERE msg.match_id = m.id AND msg.to_id = :me
                   AND msg.created_at > CASE WHEN m.user_a = :me THEN m.last_read_a ELSE m.last_read_b END
               ) AS unread
          FROM matches m
          JOIN profiles p ON p.user_id = CASE WHEN m.user_a = :me THEN m.user_b ELSE m.user_a END
          JOIN users u ON u.id = p.user_id
         WHERE (m.user_a = :me OR m.user_b = :me)
           AND m.active = 1
         ORDER BY CASE WHEN m.last_message_at > 0 THEN m.last_message_at ELSE m.created_at END DESC
         LIMIT :limit OFFSET :offset
        """,
        {"me": user_id, "limit": limit, "offset": offset},
    )


async def count_matches(db: Database, user_id: int) -> int:
    return int(
        await db.fetchval(
            "SELECT COUNT(*) FROM matches WHERE (user_a = ? OR user_b = ?) AND active = 1",
            (user_id, user_id),
            0,
        )
    )


async def total_unread(db: Database, user_id: int) -> int:
    return int(
        await db.fetchval(
            """
            SELECT COUNT(*)
              FROM messages msg
              JOIN matches m ON m.id = msg.match_id
             WHERE msg.to_id = ?
               AND m.active = 1
               AND msg.created_at > CASE WHEN m.user_a = ? THEN m.last_read_a ELSE m.last_read_b END
            """,
            (user_id, user_id),
            0,
        )
    )


async def close_match(db: Database, match_id: int, by_user: int) -> None:
    await db.execute(
        "UPDATE matches SET active = 0, closed_by = ? WHERE id = ?", (by_user, match_id)
    )


async def block_user(db: Database, user_id: int, target_id: int) -> None:
    """Блокировка: пара закрывается, анкеты больше не показываются друг другу."""
    await db.execute(
        "INSERT INTO blocks (user_id, target_id, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, target_id) DO NOTHING",
        (user_id, target_id, now()),
    )
    match = await match_between(db, user_id, target_id)
    if match:
        await close_match(db, int(match["id"]), user_id)


async def is_blocked(db: Database, user_id: int, target_id: int) -> bool:
    row = await db.fetchone(
        "SELECT 1 FROM blocks WHERE (user_id = ? AND target_id = ?) OR (user_id = ? AND target_id = ?)",
        (user_id, target_id, target_id, user_id),
    )
    return row is not None
