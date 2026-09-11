"""Настройки бота, которые можно менять из админ-панели, с кэшем в памяти."""
from __future__ import annotations

import logging

from app.constants import DEFAULT_SETTINGS
from app.db import Database

log = logging.getLogger(__name__)


class Settings:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._cache: dict[str, str] = dict(DEFAULT_SETTINGS)

    async def load(self) -> None:
        rows = await self._db.fetchall("SELECT key, value FROM settings")
        for row in rows:
            if row["key"] in DEFAULT_SETTINGS:
                self._cache[row["key"]] = row["value"]

    def get(self, key: str, default: str = "") -> str:
        return self._cache.get(key, DEFAULT_SETTINGS.get(key, default))

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get(key, str(default)))
        except (TypeError, ValueError):
            log.warning("Настройка %s не является числом, использую %s", key, default)
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        raw = self.get(key, "1" if default else "0").strip().lower()
        return raw in {"1", "true", "yes", "on", "да"}

    async def set(self, key: str, value: str) -> None:
        if key not in DEFAULT_SETTINGS:
            raise KeyError(f"Неизвестная настройка: {key}")
        value = str(value).strip()
        await self._db.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._cache[key] = value

    async def reset(self, key: str) -> None:
        if key not in DEFAULT_SETTINGS:
            raise KeyError(f"Неизвестная настройка: {key}")
        await self._db.execute("DELETE FROM settings WHERE key = ?", (key,))
        self._cache[key] = DEFAULT_SETTINGS[key]

    def all(self) -> dict[str, str]:
        return dict(self._cache)

    # --- часто используемые значения -------------------------------------------
    @property
    def pass_ttl(self) -> int:
        return max(1, self.get_int("pass_ttl_days", 14)) * 86400

    @property
    def inactive_cutoff_days(self) -> int:
        return max(1, self.get_int("inactive_days", 45))
