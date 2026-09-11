"""Суточные лимиты лайков.

Окно скользящее: считаем лайки за последние 24 часа, поэтому слоты освобождаются
постепенно, а не все разом в полночь.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.constants import ACT_LIKE, ACT_SUPERLIKE, DAY
from app.db import Database
from app.services.settings import Settings
from app.utils.time import now


@dataclass(frozen=True)
class LikeQuota:
    limit: int
    used: int
    superlike_limit: int
    superlikes_used: int
    reset_in: int  # секунд до освобождения ближайшего слота (0 — слоты есть)

    @property
    def left(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def superlikes_left(self) -> int:
        return max(0, self.superlike_limit - self.superlikes_used)

    @property
    def exhausted(self) -> bool:
        return self.left <= 0


def limit_for(user: dict, settings: Settings) -> int:
    """Новичкам и аккаунтам с низким доверием лимит меньше — это бьёт по ботофермам."""
    full = max(1, settings.get_int("likes_per_day", 60))
    reduced = max(1, settings.get_int("likes_per_day_new", 30))
    min_trust = settings.get_int("min_trust_for_full_limits", 40)
    account_age = now() - int(user.get("created_at") or now())
    if account_age < DAY or int(user.get("trust_score") or 0) < min_trust:
        return min(full, reduced)
    return full


async def quota(db: Database, user: dict, settings: Settings) -> LikeQuota:
    user_id = int(user["id"])
    window_start = now() - DAY
    limit = limit_for(user, settings)
    superlike_limit = max(0, settings.get_int("superlikes_per_day", 3))

    # Ответные лайки из «Мои лайки» лимит не тратят: отвечать взаимностью должно быть легко,
    # а массовую рассылку лайков так не устроишь — только тем, кто лайкнул тебя первым.
    used = int(
        await db.fetchval(
            "SELECT COUNT(*) FROM likes "
            "WHERE from_id = ? AND action IN (?, ?) AND source = 'feed' AND created_at > ?",
            (user_id, ACT_LIKE, ACT_SUPERLIKE, window_start),
            0,
        )
    )
    supers = int(
        await db.fetchval(
            "SELECT COUNT(*) FROM likes "
            "WHERE from_id = ? AND action = ? AND source = 'feed' AND created_at > ?",
            (user_id, ACT_SUPERLIKE, window_start),
            0,
        )
    )

    reset_in = 0
    if used >= limit:
        oldest = await db.fetchval(
            "SELECT created_at FROM likes "
            "WHERE from_id = ? AND action IN (?, ?) AND source = 'feed' AND created_at > ? "
            "ORDER BY created_at ASC LIMIT 1",
            (user_id, ACT_LIKE, ACT_SUPERLIKE, window_start),
        )
        if oldest:
            reset_in = max(0, int(oldest) + DAY - now())

    return LikeQuota(
        limit=limit,
        used=used,
        superlike_limit=superlike_limit,
        superlikes_used=supers,
        reset_in=reset_in,
    )
