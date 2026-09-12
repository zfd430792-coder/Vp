"""Верификация анкеты живым селфи.

Человека просят сделать селфи со случайным жестом. Бот не может выполнить такую
просьбу, поэтому это самый надёжный барьер против ботоферм и чужих фотографий.
Селфи не попадает в анкету: его видит только модератор, а после решения фото
удаляется из базы.
"""
from __future__ import annotations

import random
from typing import Any

from app.constants import (
    DAY,
    VERIFY_ATTEMPTS_PER_DAY,
    VERIFY_GESTURES,
    VERIFY_NONE,
    VERIFY_OK,
    VERIFY_PENDING,
    VERIFY_REJECTED,
    VERIFY_RETRY_COOLDOWN,
)
from app.db import Database
from app.utils.text import shorten
from app.utils.time import now


def pick_gesture() -> str:
    return random.choice(VERIFY_GESTURES)


def status_of(user: dict[str, Any]) -> str:
    return str(user.get("verify_status") or VERIFY_NONE)


def is_verified(user: dict[str, Any] | None) -> bool:
    return bool(user and user.get("verified"))


async def can_request(db: Database, user: dict[str, Any]) -> tuple[bool, str]:
    """Можно ли начать проверку. Возвращает (можно, причина отказа)."""
    if is_verified(user):
        return False, "already"
    if status_of(user) == VERIFY_PENDING:
        return False, "pending"

    user_id = int(user["id"])
    attempts = int(
        await db.fetchval(
            "SELECT COUNT(*) FROM events WHERE user_id = ? AND kind = 'verify_sent' "
            "AND created_at > ?",
            (user_id, now() - DAY),
            0,
        )
    )
    if attempts >= VERIFY_ATTEMPTS_PER_DAY:
        return False, "attempts"

    if status_of(user) == VERIFY_REJECTED:
        last = int(user.get("verify_at") or 0)
        if last and now() - last < VERIFY_RETRY_COOLDOWN:
            return False, "cooldown"
    return True, ""


async def start(db: Database, user_id: int, gesture: str) -> None:
    """Запоминает, какой жест мы попросили показать."""
    await db.execute(
        "UPDATE users SET verify_gesture = ?, verify_note = NULL WHERE id = ?",
        (gesture, user_id),
    )


async def submit(db: Database, user_id: int, file_id: str) -> None:
    await db.execute(
        """
        UPDATE users
           SET verify_status = ?, verify_file_id = ?, verify_at = ?, verify_note = NULL
         WHERE id = ?
        """,
        (VERIFY_PENDING, file_id, now(), user_id),
    )


async def approve(db: Database, user_id: int) -> None:
    """Подтверждает анкету и сразу удаляет селфи — хранить его больше не нужно."""
    await db.execute(
        """
        UPDATE users
           SET verified = 1, verify_status = ?, verify_file_id = NULL,
               verify_at = ?, verify_note = NULL
         WHERE id = ?
        """,
        (VERIFY_OK, now(), user_id),
    )


async def reject(db: Database, user_id: int, note: str) -> None:
    await db.execute(
        """
        UPDATE users
           SET verified = 0, verify_status = ?, verify_file_id = NULL,
               verify_at = ?, verify_note = ?
         WHERE id = ?
        """,
        (VERIFY_REJECTED, now(), shorten(note, 300), user_id),
    )


async def revoke(db: Database, user_id: int, note: str = "проверка отменена модератором") -> None:
    await db.execute(
        """
        UPDATE users
           SET verified = 0, verify_status = ?, verify_file_id = NULL,
               verify_at = ?, verify_note = ?
         WHERE id = ?
        """,
        (VERIFY_NONE, now(), shorten(note, 300), user_id),
    )


async def queue(db: Database, *, limit: int = 1, offset: int = 0) -> list[dict[str, Any]]:
    return await db.fetchall(
        """
        SELECT u.id AS user_id, u.username, u.verify_file_id, u.verify_gesture, u.verify_at,
               u.trust_score, u.created_at AS user_created_at, u.last_active_at, u.warns,
               p.name, p.age, p.gender, p.city, p.bio, p.interests, p.moderation
          FROM users u
          LEFT JOIN profiles p ON p.user_id = u.id
         WHERE u.verify_status = ?
         ORDER BY u.verify_at ASC
         LIMIT ? OFFSET ?
        """,
        (VERIFY_PENDING, limit, offset),
    )


async def queue_count(db: Database) -> int:
    return int(
        await db.fetchval(
            "SELECT COUNT(*) FROM users WHERE verify_status = ?", (VERIFY_PENDING,), 0
        )
    )
