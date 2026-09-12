"""Суточные лимиты лайков.

Окно скользящее: считаем лайки за последние 24 часа, поэтому слоты освобождаются
постепенно, а не все разом в полночь.

Лимит не фиксированный и не продаётся. Он складывается из базы и надбавок за то,
что человек сделал сам: заполнил анкету, подтвердил её селфи, накопил доверие.
Каждую надбавку бот показывает открыто — вместе с тем, что ещё можно получить.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.constants import (
    ACT_LIKE,
    ACT_SUPERLIKE,
    BONUS_FULL_PROFILE,
    BONUS_TRUSTED,
    BONUS_VERIFIED,
    DAY,
    MIN_LIKES_LIMIT,
    PENALTY_FRESH,
    PENALTY_LOW_TRUST,
)
from app.db import Database
from app.services.settings import Settings
from app.utils.time import now

FULL_PROFILE_PHOTOS = 2
FULL_PROFILE_BIO = 30


@dataclass(frozen=True)
class LimitBreakdown:
    """Из чего собран суточный лимит."""

    base: int
    total: int
    cap: int
    parts: list[tuple[str, int]] = field(default_factory=list)
    available: list[tuple[str, int]] = field(default_factory=list)
    capped: bool = False


@dataclass(frozen=True)
class LikeQuota:
    breakdown: LimitBreakdown
    used: int
    superlike_limit: int
    superlikes_used: int
    reset_in: int  # секунд до освобождения ближайшего слота (0 — слоты есть)

    @property
    def limit(self) -> int:
        return self.breakdown.total

    @property
    def left(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def superlikes_left(self) -> int:
        return max(0, self.superlike_limit - self.superlikes_used)

    @property
    def exhausted(self) -> bool:
        return self.left <= 0


def compute(
    user: dict[str, Any],
    settings: Settings,
    *,
    profile: dict[str, Any] | None = None,
    photos: int = 0,
) -> LimitBreakdown:
    base = max(1, settings.get_int("likes_per_day_new", 30))
    cap = max(base, settings.get_int("likes_per_day", 60))
    trust = int(user.get("trust_score") or 0)
    account_age = now() - int(user.get("created_at") or now())

    earned: list[tuple[str, int]] = []
    available: list[tuple[str, int]] = []

    bio = str((profile or {}).get("bio") or "").strip()
    if photos >= FULL_PROFILE_PHOTOS and len(bio) >= FULL_PROFILE_BIO:
        earned.append(("заполненная анкета", BONUS_FULL_PROFILE))
    else:
        available.append(
            (f"{FULL_PROFILE_PHOTOS} фото и описание от {FULL_PROFILE_BIO} символов", BONUS_FULL_PROFILE)
        )

    if user.get("verified"):
        earned.append(("подтверждённая анкета", BONUS_VERIFIED))
    else:
        available.append(("подтвердить анкету селфи", BONUS_VERIFIED))

    trust_bonus_at = max(1, settings.get_int("trust_for_bonus", 70))
    if trust >= trust_bonus_at and account_age >= 3 * DAY:
        earned.append(("высокий уровень доверия", BONUS_TRUSTED))
    else:
        available.append((f"доверие от {trust_bonus_at} без нарушений", BONUS_TRUSTED))

    penalties: list[tuple[str, int]] = []
    if account_age < DAY:
        penalties.append(("аккаунт младше суток", -PENALTY_FRESH))
    if trust < settings.get_int("min_trust_for_full_limits", 40):
        penalties.append(("низкий уровень доверия", -PENALTY_LOW_TRUST))

    raw = base + sum(value for _, value in earned) + sum(value for _, value in penalties)
    floor = min(MIN_LIKES_LIMIT, cap)  # если админ выставил лимит ниже — его слово главнее
    total = max(floor, min(cap, raw))
    return LimitBreakdown(
        base=base,
        total=total,
        cap=cap,
        parts=earned + penalties,
        available=available,
        capped=raw > cap,
    )


async def breakdown(db: Database, user: dict[str, Any], settings: Settings) -> LimitBreakdown:
    user_id = int(user["id"])
    profile = await db.fetchone("SELECT bio FROM profiles WHERE user_id = ?", (user_id,))
    photos = int(
        await db.fetchval("SELECT COUNT(*) FROM photos WHERE user_id = ?", (user_id,), 0)
    )
    return compute(user, settings, profile=profile, photos=photos)


async def quota(db: Database, user: dict[str, Any], settings: Settings) -> LikeQuota:
    user_id = int(user["id"])
    window_start = now() - DAY
    limits = await breakdown(db, user, settings)
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
    if used >= limits.total:
        oldest = await db.fetchval(
            "SELECT created_at FROM likes "
            "WHERE from_id = ? AND action IN (?, ?) AND source = 'feed' AND created_at > ? "
            "ORDER BY created_at ASC LIMIT 1",
            (user_id, ACT_LIKE, ACT_SUPERLIKE, window_start),
        )
        if oldest:
            reset_in = max(0, int(oldest) + DAY - now())

    return LikeQuota(
        breakdown=limits,
        used=used,
        superlike_limit=superlike_limit,
        superlikes_used=supers,
        reset_in=reset_in,
    )
