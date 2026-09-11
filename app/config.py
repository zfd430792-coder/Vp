"""Загрузка конфигурации из окружения и .env файла."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    """Простой парсер .env без внешних зависимостей.

    Значения из окружения имеют приоритет над файлом.
    """
    path = path or (BASE_DIR / ".env")
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def _parse_ids(raw: str) -> tuple[int, ...]:
    result: list[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            result.append(int(chunk))
        except ValueError:
            continue
    return tuple(dict.fromkeys(result))


class ConfigError(RuntimeError):
    """Некорректная конфигурация."""


@dataclass(frozen=True)
class Config:
    token: str
    owner_ids: tuple[int, ...]
    db_path: Path
    moderation_chat_id: int | None = None
    support_contact: str = "@support"
    log_level: str = "INFO"
    tasks_interval: int = 300


def load_config() -> Config:
    load_dotenv()

    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError(
            "Не задан BOT_TOKEN. Скопируйте .env.example в .env и укажите токен от @BotFather."
        )
    if ":" not in token:
        raise ConfigError("BOT_TOKEN выглядит некорректно: ожидается формат 123456:AA...")

    owner_ids = _parse_ids(os.environ.get("OWNER_IDS", ""))
    if not owner_ids:
        raise ConfigError(
            "Не задан OWNER_IDS. Укажите хотя бы один Telegram ID владельца (узнать: @userinfobot)."
        )

    db_raw = os.environ.get("DB_PATH", "data/bot.db").strip() or "data/bot.db"
    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    mod_chat_raw = os.environ.get("MODERATION_CHAT_ID", "").strip()
    moderation_chat_id: int | None = None
    if mod_chat_raw:
        try:
            moderation_chat_id = int(mod_chat_raw)
        except ValueError as exc:
            raise ConfigError("MODERATION_CHAT_ID должен быть числом (например -1001234567890).") from exc

    return Config(
        token=token,
        owner_ids=owner_ids,
        db_path=db_path,
        moderation_chat_id=moderation_chat_id,
        support_contact=os.environ.get("SUPPORT_CONTACT", "@support").strip() or "@support",
        log_level=os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )
