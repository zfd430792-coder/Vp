"""Анкеты и фотографии."""
from __future__ import annotations

from typing import Any

from app.constants import MAX_PHOTOS, MOD_HOLD, MOD_OK, MOD_REJECTED, MOD_REVIEW
from app.db import Database
from app.utils.text import normalize_city, text_hash
from app.utils.time import now

Profile = dict[str, Any]


async def get(db: Database, user_id: int) -> Profile | None:
    return await db.fetchone("SELECT * FROM profiles WHERE user_id = ?", (user_id,))


async def ensure(db: Database, user_id: int) -> Profile:
    """Создаёт пустую анкету, если её ещё нет."""
    await db.execute(
        "INSERT INTO profiles (user_id, updated_at) VALUES (?, ?) ON CONFLICT(user_id) DO NOTHING",
        (user_id, now()),
    )
    profile = await get(db, user_id)
    if profile is None:
        raise RuntimeError(f"Не удалось создать анкету для {user_id}")
    return profile


async def update(db: Database, user_id: int, **fields: Any) -> None:
    """Обновляет произвольные поля анкеты (белый список колонок)."""
    allowed = {
        "name", "age", "gender", "seeking", "city", "city_norm", "bio", "bio_hash", "interests",
        "age_min", "age_max", "only_verified", "is_complete", "is_visible",
        "moderation", "moderation_note", "lat", "lon", "geo_source", "search_radius",
    }
    payload = {key: value for key, value in fields.items() if key in allowed}
    if not payload:
        return
    if "city" in payload and "city_norm" not in payload:
        payload["city_norm"] = normalize_city(str(payload["city"] or ""))
    if "bio" in payload:
        bio_value = str(payload["bio"] or "").strip()
        payload["bio_hash"] = text_hash(bio_value) if bio_value else None
    payload["updated_at"] = now()
    assignments = ", ".join(f"{key} = ?" for key in payload)
    await db.execute(
        f"UPDATE profiles SET {assignments} WHERE user_id = ?",
        (*payload.values(), user_id),
    )


async def is_complete(db: Database, user_id: int) -> bool:
    profile = await get(db, user_id)
    if not profile:
        return False
    return bool(profile.get("is_complete"))


async def mark_complete(db: Database, user_id: int) -> bool:
    """Помечает анкету заполненной, если есть все обязательные поля и фото."""
    profile = await get(db, user_id)
    if not profile:
        return False
    photos = await count_photos(db, user_id)
    required = all(
        profile.get(field) for field in ("name", "age", "gender", "seeking", "city")
    )
    complete = bool(required and photos > 0)
    await db.execute(
        "UPDATE profiles SET is_complete = ?, updated_at = ? WHERE user_id = ?",
        (1 if complete else 0, now(), user_id),
    )
    return complete


async def set_visible(db: Database, user_id: int, visible: bool) -> None:
    await db.execute(
        "UPDATE profiles SET is_visible = ?, updated_at = ? WHERE user_id = ?",
        (1 if visible else 0, now(), user_id),
    )


async def set_moderation(
    db: Database, user_id: int, status: str, note: str | None = None
) -> None:
    if status not in {MOD_OK, MOD_REVIEW, MOD_HOLD, MOD_REJECTED}:
        raise ValueError(f"Неизвестный статус модерации: {status}")
    await db.execute(
        "UPDATE profiles SET moderation = ?, moderation_note = ? WHERE user_id = ?",
        (status, note, user_id),
    )


# --------------------------------------------------------------------------- фотографии


async def photos(db: Database, user_id: int) -> list[dict[str, Any]]:
    return await db.fetchall(
        "SELECT * FROM photos WHERE user_id = ? ORDER BY position, id", (user_id,)
    )


async def count_photos(db: Database, user_id: int) -> int:
    return int(await db.fetchval("SELECT COUNT(*) FROM photos WHERE user_id = ?", (user_id,), 0))


async def is_photo_blocked(db: Database, file_unique_id: str) -> bool:
    """Фото из стоп-листа модерации нельзя загружать повторно."""
    row = await db.fetchone(
        "SELECT 1 FROM banned_content WHERE hash = ? AND kind = 'photo'", (file_unique_id,)
    )
    return row is not None


async def add_photo(
    db: Database, user_id: int, file_id: str, file_unique_id: str, kind: str = "photo"
) -> bool:
    """Добавляет фото. Возвращает False, если достигнут лимит."""
    current = await count_photos(db, user_id)
    if current >= MAX_PHOTOS:
        return False
    await db.execute(
        """
        INSERT INTO photos (user_id, file_id, file_unique_id, kind, position, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, file_id, file_unique_id, kind, current, now()),
    )
    return True


async def clear_photos(db: Database, user_id: int) -> None:
    await db.execute("DELETE FROM photos WHERE user_id = ?", (user_id,))


async def delete_photo(db: Database, photo_id: int, user_id: int) -> bool:
    removed = await db.modify(
        "DELETE FROM photos WHERE id = ? AND user_id = ?", (photo_id, user_id)
    )
    if not removed:
        return False
    await _renumber(db, user_id)
    return True


async def _renumber(db: Database, user_id: int) -> None:
    rows = await db.fetchall(
        "SELECT id FROM photos WHERE user_id = ? ORDER BY position, id", (user_id,)
    )
    for index, row in enumerate(rows):
        await db.execute("UPDATE photos SET position = ? WHERE id = ?", (index, row["id"]))


async def duplicate_photo_owners(db: Database, file_unique_id: str, exclude: int) -> list[int]:
    """Кто ещё использует это же фото — сильный признак ботофермы."""
    rows = await db.fetchall(
        "SELECT DISTINCT user_id FROM photos WHERE file_unique_id = ? AND user_id != ?",
        (file_unique_id, exclude),
    )
    return [int(row["user_id"]) for row in rows]


# --------------------------------------------------------------------------- карточка


async def card(db: Database, user_id: int) -> dict[str, Any] | None:
    """Полные данные для показа анкеты: профиль + аккаунт + фото."""
    row = await db.fetchone(
        """
        SELECT p.*, u.username, u.tg_name, u.last_active_at, u.created_at AS user_created_at,
               u.trust_score, u.status, u.role, u.warns, u.shadow_level, u.shadow_until,
               u.ban_permanent, u.ban_until, u.bot_blocked, u.verified, u.verify_status
          FROM profiles p
          JOIN users u ON u.id = p.user_id
         WHERE p.user_id = ?
        """,
        (user_id,),
    )
    if row is None:
        return None
    row["photos"] = await photos(db, user_id)
    return row
