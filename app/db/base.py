"""Асинхронная обёртка над SQLite."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite

from app.db.schema import MIGRATIONS

log = logging.getLogger(__name__)

Params = Sequence[Any] | dict[str, Any]


class Database:
    """Одно соединение с SQLite в режиме автокоммита.

    Запись защищена ``asyncio.Lock``: бот однопроцессный, а так мы гарантируем,
    что операции не перемешаются между собой.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle
    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None -> автокоммит, каждое выражение применяется сразу
        self._conn = await aiosqlite.connect(self.path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self.migrate()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База данных не подключена: вызовите Database.connect()")
        return self._conn

    # ------------------------------------------------------------------ migrations
    async def migrate(self) -> None:
        cursor = await self.conn.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        await cursor.close()
        current = int(row[0]) if row else 0
        target = len(MIGRATIONS)
        if current >= target:
            return
        for version in range(current, target):
            log.info("Применяю миграцию базы данных #%s", version + 1)
            for statement in MIGRATIONS[version]:
                await self.conn.execute(statement)
            await self.conn.execute(f"PRAGMA user_version={version + 1}")
        log.info("Схема базы данных обновлена до версии %s", target)

    # ------------------------------------------------------------------ helpers
    async def execute(self, sql: str, params: Params = ()) -> aiosqlite.Cursor:
        async with self._lock:
            return await self.conn.execute(sql, params)

    async def executemany(self, sql: str, params_seq: Iterable[Params]) -> None:
        async with self._lock:
            await self.conn.executemany(sql, params_seq)

    async def fetchone(self, sql: str, params: Params = ()) -> dict[str, Any] | None:
        """Возвращает одну строку как обычный словарь (или None)."""
        async with self._lock:
            cursor = await self.conn.execute(sql, params)
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
        return dict(row) if row is not None else None

    async def fetchall(self, sql: str, params: Params = ()) -> list[dict[str, Any]]:
        """Возвращает список строк в виде словарей."""
        async with self._lock:
            cursor = await self.conn.execute(sql, params)
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        return [dict(row) for row in rows]

    async def fetchval(self, sql: str, params: Params = (), default: Any = None) -> Any:
        """Возвращает первое поле первой строки."""
        async with self._lock:
            cursor = await self.conn.execute(sql, params)
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
        if row is None or row[0] is None:
            return default
        return row[0]

    async def insert(self, sql: str, params: Params = ()) -> int:
        """Выполняет INSERT и возвращает rowid новой строки (0 — ничего не вставлено)."""
        async with self._lock:
            cursor = await self.conn.execute(sql, params)
            try:
                if cursor.rowcount == 0:
                    return 0
                return int(cursor.lastrowid or 0)
            finally:
                await cursor.close()

    async def modify(self, sql: str, params: Params = ()) -> int:
        """Выполняет UPDATE/DELETE и возвращает число затронутых строк."""
        async with self._lock:
            cursor = await self.conn.execute(sql, params)
            try:
                return int(cursor.rowcount)
            finally:
                await cursor.close()
