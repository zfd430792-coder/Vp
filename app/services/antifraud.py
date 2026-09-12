"""Защита от ботоферм и накруток.

Идея простая: не банить по одному признаку, а копить сигналы. Один сигнал —
повод присмотреться, несколько — повод показать капчу или снизить охват.
Автоматика здесь никогда не выдаёт вечный бан: последнее слово за человеком.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from typing import Any

from app.constants import DAY, HOUR, MOD_HOLD, MOD_OK, MOD_REVIEW
from app.db import Database
from app.services.settings import Settings
from app.utils.text import analyze_text, text_hash
from app.utils.time import now

log = logging.getLogger(__name__)

# Веса событий: вычитаются из уровня доверия
EVENT_WEIGHTS = {
    "photo_reuse": 25,
    "bio_duplicate": 20,
    "spam_text": 20,
    "fast_swipes": 15,
    "like_everyone": 15,
    "captcha_failed": 10,
    "reg_burst": 10,
    "banned_content": 40,
    "report_confirmed": 30,
}


async def log_event(
    db: Database,
    user_id: int,
    kind: str,
    *,
    weight: int | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    await db.execute(
        "INSERT INTO events (user_id, kind, weight, meta, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            user_id,
            kind,
            EVENT_WEIGHTS.get(kind, 0) if weight is None else int(weight),
            json.dumps(meta, ensure_ascii=False) if meta else None,
            now(),
        ),
    )


async def risk_score(db: Database, user_id: int, days: int = 30) -> int:
    value = await db.fetchval(
        "SELECT COALESCE(SUM(weight), 0) FROM events WHERE user_id = ? AND created_at > ?",
        (user_id, now() - days * DAY),
        0,
    )
    return int(value)


async def user_events(db: Database, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    return await db.fetchall(
        "SELECT * FROM events WHERE user_id = ? AND weight > 0 ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )


# --------------------------------------------------------------------------- поведение


class ActionTracker:
    """Считает темп действий в памяти процесса — для ловли автокликеров."""

    def __init__(self, history: int = 40) -> None:
        self._history = history
        self._actions: dict[int, deque[tuple[float, str]]] = defaultdict(
            lambda: deque(maxlen=history)
        )

    def push(self, user_id: int, action: str) -> None:
        self._actions[user_id].append((time.monotonic(), action))

    def intervals(self, user_id: int, count: int = 10) -> list[float]:
        events = list(self._actions.get(user_id, ()))[-count:]
        return [round(b[0] - a[0], 3) for a, b in zip(events, events[1:], strict=False)]

    def is_inhuman(self, user_id: int) -> bool:
        """Слишком ровный и быстрый темп — почти наверняка скрипт."""
        gaps = self.intervals(user_id, 12)
        if len(gaps) < 8:
            return False
        fast = sum(1 for gap in gaps if gap < 0.35)
        return fast >= 6

    def like_ratio(self, user_id: int, count: int = 30) -> tuple[float, int]:
        events = list(self._actions.get(user_id, ()))[-count:]
        if not events:
            return 0.0, 0
        likes = sum(1 for _, action in events if action in {"like", "superlike"})
        return likes / len(events), len(events)

    def prune(self, keep: int = 5000) -> None:
        if len(self._actions) <= keep:
            return
        for user_id in list(self._actions)[: len(self._actions) - keep]:
            self._actions.pop(user_id, None)


tracker = ActionTracker()


async def check_swipe(db: Database, user_id: int, action: str) -> str | None:
    """Проверяет темп листания. Возвращает код проблемы или None."""
    tracker.push(user_id, action)

    if tracker.is_inhuman(user_id):
        await log_event(db, user_id, "fast_swipes", meta={"intervals": tracker.intervals(user_id)})
        return "fast_swipes"

    ratio, sample = tracker.like_ratio(user_id)
    if sample >= 25 and ratio >= 0.95:
        recent = await db.fetchval(
            "SELECT COUNT(*) FROM events WHERE user_id = ? AND kind = 'like_everyone' "
            "AND created_at > ?",
            (user_id, now() - DAY),
            0,
        )
        if not int(recent):
            await log_event(db, user_id, "like_everyone", meta={"ratio": round(ratio, 2)})
            return "like_everyone"
    return None


# --------------------------------------------------------------------------- регистрация


async def registration_burst(db: Database, seconds: int = HOUR) -> int:
    return int(
        await db.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at > ?", (now() - seconds,), 0
        )
    )


async def needs_captcha(db: Database, settings: Settings, user: dict[str, Any]) -> bool:
    """Показывать ли капчу этому пользователю."""
    if not settings.get_bool("captcha_enabled", True):
        return False
    if user.get("captcha_passed"):
        return False

    burst_limit = max(1, settings.get_int("reg_burst_limit", 12))
    if await registration_burst(db) >= burst_limit:
        await log_event(db, int(user["id"]), "reg_burst", weight=0)
        return True

    # Аккаунт без username и с низким доверием — типичный расходник ботофермы
    if not user.get("username") and int(user.get("trust_score") or 50) < 50:
        return True

    return await risk_score(db, int(user["id"])) >= 20


# --------------------------------------------------------------------------- содержимое анкеты


async def screen_photo(db: Database, user_id: int, file_unique_id: str) -> list[int]:
    """Ищет это же фото у других аккаунтов."""
    rows = await db.fetchall(
        "SELECT DISTINCT user_id FROM photos WHERE file_unique_id = ? AND user_id != ?",
        (file_unique_id, user_id),
    )
    owners = [int(row["user_id"]) for row in rows]
    if owners:
        await log_event(
            db, user_id, "photo_reuse", meta={"owners": owners[:5], "count": len(owners)}
        )
    return owners


async def is_banned_content(db: Database, digest: str) -> dict[str, Any] | None:
    return await db.fetchone("SELECT * FROM banned_content WHERE hash = ?", (digest,))


async def ban_content(
    db: Database,
    digest: str,
    kind: str,
    reason: str,
    admin_id: int,
    *,
    added_for: int | None = None,
) -> None:
    """Заносит хэш контента в стоп-лист.

    ``added_for`` — чей это был контент: по нему запрет можно снять даже после того,
    как сами фотографии удалены из анкеты.
    """
    await db.execute(
        """
        INSERT INTO banned_content (hash, kind, reason, added_by, added_for, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(hash) DO UPDATE SET
            reason = excluded.reason,
            added_by = excluded.added_by,
            added_for = COALESCE(excluded.added_for, banned_content.added_for)
        """,
        (digest, kind, reason, admin_id, added_for, now()),
    )


async def ban_user_photos(db: Database, user_id: int, reason: str, admin_id: int) -> int:
    """Заносит все фото анкеты в стоп-лист. Возвращает число добавленных хэшей."""
    rows = await db.fetchall(
        "SELECT DISTINCT file_unique_id FROM photos WHERE user_id = ?", (user_id,)
    )
    for row in rows:
        await ban_content(
            db, str(row["file_unique_id"]), "photo", reason, admin_id, added_for=user_id
        )
    return len(rows)


async def unban_user_photos(db: Database, user_id: int) -> int:
    """Снимает запрет с фотографий этого аккаунта, даже если они уже удалены."""
    return await db.modify(
        "DELETE FROM banned_content WHERE kind = 'photo' AND ("
        "added_for = ? OR hash IN (SELECT file_unique_id FROM photos WHERE user_id = ?))",
        (user_id, user_id),
    )


async def has_banned_photos(db: Database, user_id: int) -> bool:
    row = await db.fetchone(
        """
        SELECT 1 FROM banned_content
         WHERE kind = 'photo' AND (
               added_for = ?
            OR hash IN (SELECT file_unique_id FROM photos WHERE user_id = ?)
         )
         LIMIT 1
        """,
        (user_id, user_id),
    )
    return row is not None


async def stoplist_size(db: Database) -> int:
    return int(await db.fetchval("SELECT COUNT(*) FROM banned_content", (), 0))


async def screen_profile(
    db: Database, user_id: int, profile: dict[str, Any]
) -> tuple[str, list[str]]:
    """Проверяет готовую анкету. Возвращает (статус модерации, причины).

    ``ok`` — всё чисто, ``review`` — показываем, но реже и ставим в очередь,
    ``hold`` — прячем до решения модератора.
    """
    reasons: list[str] = []
    severity = 0

    bio = profile.get("bio") or ""
    spam_score, spam_reasons = analyze_text(bio)
    if spam_score >= 60:
        severity = max(severity, 2)
        reasons.extend(spam_reasons)
        await log_event(db, user_id, "spam_text", meta={"score": spam_score, "where": "bio"})
    elif spam_score >= 30:
        severity = max(severity, 1)
        reasons.extend(spam_reasons)
        await log_event(db, user_id, "spam_text", weight=10, meta={"score": spam_score})

    if bio.strip():
        digest = text_hash(bio)
        # Одинаковое описание у разных аккаунтов — типичная штамповка ботофермы
        same = int(
            await db.fetchval(
                "SELECT COUNT(*) FROM profiles WHERE bio_hash = ? AND user_id != ?",
                (digest, user_id),
                0,
            )
        )
        if same:
            severity = max(severity, 1 if same == 1 else 2)
            reasons.append(f"такое же описание ещё у {same} анкет")
            await log_event(db, user_id, "bio_duplicate", meta={"count": same})

        banned = await is_banned_content(db, digest)
        if banned:
            severity = 2
            reasons.append(f"текст в стоп-листе: {banned.get('reason') or 'запрещён'}")
            await log_event(db, user_id, "banned_content", meta={"kind": "bio"})

    photo_rows = await db.fetchall(
        "SELECT file_unique_id FROM photos WHERE user_id = ?", (user_id,)
    )
    reused_total = 0
    for row in photo_rows:
        digest = str(row["file_unique_id"])
        banned = await is_banned_content(db, digest)
        if banned:
            severity = 2
            reasons.append(f"фото в стоп-листе: {banned.get('reason') or 'запрещено'}")
            await log_event(db, user_id, "banned_content", meta={"kind": "photo"})
        owners = await db.fetchall(
            "SELECT DISTINCT user_id FROM photos WHERE file_unique_id = ? AND user_id != ?",
            (digest, user_id),
        )
        reused_total += len(owners)
    if reused_total:
        severity = max(severity, 1 if reused_total < 3 else 2)
        reasons.append(f"фото встречается ещё у {reused_total} аккаунтов")

    status = {0: MOD_OK, 1: MOD_REVIEW, 2: MOD_HOLD}[severity]
    return status, reasons


async def flagged_users(
    db: Database, *, limit: int = 10, offset: int = 0
) -> list[dict[str, Any]]:
    """Аккаунты с наибольшим количеством сигналов за последние 7 дней."""
    return await db.fetchall(
        """
        SELECT e.user_id,
               SUM(e.weight) AS risk,
               COUNT(*) AS signals,
               MAX(e.created_at) AS last_at,
               GROUP_CONCAT(DISTINCT e.kind) AS kinds,
               u.status, u.trust_score, u.username,
               p.name, p.moderation
          FROM events e
          JOIN users u ON u.id = e.user_id
          LEFT JOIN profiles p ON p.user_id = e.user_id
         WHERE e.created_at > ? AND e.weight > 0
         GROUP BY e.user_id
        HAVING risk > 0
         ORDER BY risk DESC, last_at DESC
         LIMIT ? OFFSET ?
        """,
        (now() - 7 * DAY, limit, offset),
    )


async def flagged_count(db: Database) -> int:
    return int(
        await db.fetchval(
            "SELECT COUNT(*) FROM (SELECT user_id FROM events WHERE created_at > ? AND weight > 0 "
            "GROUP BY user_id HAVING SUM(weight) > 0)",
            (now() - 7 * DAY,),
            0,
        )
    )
