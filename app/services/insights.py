"""Дневная статистика анкеты: показы, входящие лайки, взаимности.

Нужна для двух вещей: человек видит, как работает его анкета и что улучшить,
а администратор — общую картину по боту.
"""
from __future__ import annotations

from app.db import Database
from app.utils.time import day_start, now

FIELDS = {"shown", "likes_in", "matches"}


async def bump(db: Database, user_id: int, field: str, amount: int = 1) -> None:
    if field not in FIELDS:
        raise ValueError(f"Неизвестное поле статистики: {field}")
    await db.execute(
        f"""
        INSERT INTO profile_stats (user_id, day, {field})
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, day) DO UPDATE SET {field} = {field} + excluded.{field}
        """,
        (user_id, day_start(), amount),
    )


async def summary(db: Database, user_id: int, days: int = 7) -> dict[str, int]:
    since = day_start() - max(0, days - 1) * 86400
    row = await db.fetchone(
        """
        SELECT COALESCE(SUM(shown), 0) AS shown,
               COALESCE(SUM(likes_in), 0) AS likes_in,
               COALESCE(SUM(matches), 0) AS matches
          FROM profile_stats
         WHERE user_id = ? AND day >= ?
        """,
        (user_id, since),
    )
    data = {key: int(value) for key, value in (row or {}).items()}
    data.setdefault("shown", 0)
    data.setdefault("likes_in", 0)
    data.setdefault("matches", 0)
    return data


async def totals(db: Database, days: int = 7) -> dict[str, int]:
    since = day_start() - max(0, days - 1) * 86400
    row = await db.fetchone(
        """
        SELECT COALESCE(SUM(shown), 0) AS shown,
               COALESCE(SUM(likes_in), 0) AS likes_in,
               COALESCE(SUM(matches), 0) AS matches
          FROM profile_stats
         WHERE day >= ?
        """,
        (since,),
    )
    return {key: int(value) for key, value in (row or {}).items()}


async def cleanup(db: Database, keep_days: int = 120) -> int:
    cutoff = day_start(now()) - max(1, keep_days) * 86400
    return await db.modify("DELETE FROM profile_stats WHERE day < ?", (cutoff,))
