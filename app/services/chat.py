"""Общение внутри бота: сообщения идут через бота, номера телефонов не раскрываются.

Переписка хранится ограниченное время — это единственный способ разобрать жалобу
по существу, а не «на слово». Срок хранения настраивается в админ-панели.
"""
from __future__ import annotations

from typing import Any

from app.db import Database
from app.utils.time import now

# (получатель, match_id) -> когда последний раз показывали подпись «сообщение от ...».
# Это косметика, поэтому держать её в памяти процесса достаточно.
_headers: dict[tuple[int, int], int] = {}

HEADER_COOLDOWN = 300


async def open_chat(db: Database, user_id: int, match_id: int) -> None:
    """Запоминает открытый диалог в базе, чтобы он пережил перезапуск бота."""
    await db.execute("UPDATE users SET active_match_id = ? WHERE id = ?", (match_id, user_id))


async def close_chat(db: Database, user_id: int) -> None:
    await db.execute("UPDATE users SET active_match_id = NULL WHERE id = ?", (user_id,))


async def current_chat(db: Database, user_id: int) -> int | None:
    value = await db.fetchval("SELECT active_match_id FROM users WHERE id = ?", (user_id,))
    return int(value) if value else None


async def is_viewing(db: Database, user_id: int, match_id: int) -> bool:
    return await current_chat(db, user_id) == match_id


async def need_header(db: Database, to_id: int, match_id: int) -> bool:
    """Показывать ли подпись «сообщение от ...» перед пересылкой."""
    if await is_viewing(db, to_id, match_id):
        return False
    key = (to_id, match_id)
    moment = now()
    last = _headers.get(key, 0)
    if moment - last < HEADER_COOLDOWN:
        return False
    _headers[key] = moment
    return True


def reset_header(to_id: int, match_id: int) -> None:
    _headers.pop((to_id, match_id), None)


def prune(keep: int = 5000) -> None:
    if len(_headers) > keep:
        for key in list(_headers)[: len(_headers) - keep]:
            _headers.pop(key, None)


# --------------------------------------------------------------------------- хранение


async def save_message(
    db: Database,
    *,
    match_id: int,
    from_id: int,
    to_id: int,
    kind: str,
    text: str | None = None,
    file_id: str | None = None,
) -> None:
    moment = now()
    await db.execute(
        """
        INSERT INTO messages (match_id, from_id, to_id, kind, text, file_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (match_id, from_id, to_id, kind, text, file_id, moment),
    )
    await db.execute("UPDATE matches SET last_message_at = ? WHERE id = ?", (moment, match_id))


async def history(db: Database, match_id: int, limit: int = 20) -> list[dict[str, Any]]:
    rows = await db.fetchall(
        "SELECT * FROM messages WHERE match_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
        (match_id, limit),
    )
    return list(reversed(rows))


async def media_from_history(
    db: Database, match_id: int, limit: int = 10
) -> list[dict[str, Any]]:
    return await db.fetchall(
        "SELECT * FROM messages WHERE match_id = ? AND file_id IS NOT NULL "
        "ORDER BY created_at DESC LIMIT ?",
        (match_id, limit),
    )


async def mark_read(db: Database, match_id: int, user_id: int) -> None:
    match = await db.fetchone("SELECT user_a, user_b FROM matches WHERE id = ?", (match_id,))
    if not match:
        return
    column = "last_read_a" if int(match["user_a"]) == user_id else "last_read_b"
    await db.execute(f"UPDATE matches SET {column} = ? WHERE id = ?", (now(), match_id))


async def cleanup_old(db: Database, keep_days: int) -> int:
    """Удаляет старую переписку. Возвращает число удалённых сообщений."""
    cutoff = now() - max(1, keep_days) * 86400
    return await db.modify("DELETE FROM messages WHERE created_at < ?", (cutoff,))
