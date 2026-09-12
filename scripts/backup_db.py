"""Целостная копия базы перед обновлением.

Простое копирование файла может застать базу в момент записи и захватить
половину журнала WAL. Встроенный механизм sqlite делает согласованный снимок
даже во время работы бота.

Использование: python scripts/backup_db.py data/bot.db data/backups/bot-2026.db
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("нужны путь к базе и путь к копии", file=sys.stderr)
        return 2

    source_path = Path(sys.argv[1])
    target_path = Path(sys.argv[2])
    if not source_path.exists():
        print("базы ещё нет — копировать нечего")
        return 0

    target_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    target = sqlite3.connect(target_path)
    try:
        with target:
            source.backup(target)
    finally:
        target.close()
        source.close()

    size_mb = target_path.stat().st_size / 1024 / 1024
    print(f"{size_mb:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
