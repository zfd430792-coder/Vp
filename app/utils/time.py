"""Время: всё внутри бота хранится в UNIX-секундах (UTC)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

MOSCOW = timezone(timedelta(hours=3))


def now() -> int:
    return int(time.time())


def fmt_dt(ts: int | None, tz: timezone = MOSCOW) -> str:
    """Дата и время в удобном виде (по умолчанию МСК)."""
    if not ts:
        return "—"
    return datetime.fromtimestamp(int(ts), tz).strftime("%d.%m.%Y %H:%M")


def fmt_date(ts: int | None, tz: timezone = MOSCOW) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(int(ts), tz).strftime("%d.%m.%Y")


def day_start(ts: int | None = None, tz: timezone = MOSCOW) -> int:
    """Начало суток для статистики (по МСК)."""
    moment = datetime.fromtimestamp(ts if ts is not None else now(), tz)
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())
