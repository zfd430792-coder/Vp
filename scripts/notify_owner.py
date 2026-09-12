"""Отправляет владельцам сообщение через Bot API, читая токен из .env.

Нужно, чтобы результат обновления доходил до человека: бот в этот момент
перезапускается и сам написать не может.

Использование: python scripts/notify_owner.py "текст сообщения"
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def send(token: str, chat_id: str, text: str) -> bool:
    payload = json.dumps(
        {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def main() -> int:
    if len(sys.argv) < 2:
        return 2

    env = read_env(PROJECT_DIR / ".env")
    token = env.get("BOT_TOKEN", "")
    owners = [item.strip() for item in env.get("OWNER_IDS", "").replace(";", ",").split(",")]
    owners = [item for item in owners if item]
    if not token or not owners:
        return 1

    text = sys.argv[1][:4000]
    delivered = sum(1 for owner in owners if send(token, owner, text))
    return 0 if delivered else 1


if __name__ == "__main__":
    raise SystemExit(main())
