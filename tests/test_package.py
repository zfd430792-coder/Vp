"""Проверка целостности пакета: бот должен запускаться из чистой копии репозитория.

Ловит два класса поломок, которые не видны на машине разработчика, потому что
там файлы уже лежат на диске:

1. Файл есть локально, но не попал в git (например, его случайно съел .gitignore).
   На сервере после git clone/pull такого файла не будет — бот упадёт с
   ModuleNotFoundError по кругу.
2. Модуль не импортируется (опечатка, битый импорт, циклическая зависимость).

Запуск: python tests/test_package.py
"""
from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SKIP_DIRS = {".venv", "venv", "env", "__pycache__", ".git", ".ruff_cache"}

passed = 0
failed = 0


def check(title: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✅ {title}")
    else:
        failed += 1
        print(f"  ❌ {title}" + (f" — {detail}" if detail else ""))


def local_files() -> list[Path]:
    found: list[Path] = []
    stack = [ROOT]
    while stack:
        current = stack.pop()
        for item in current.iterdir():
            if item.name in SKIP_DIRS:
                continue
            if item.is_dir():
                stack.append(item)
            elif item.suffix == ".py" or item.suffix == ".sh":
                found.append(item)
    return sorted(found)


def tracked_files() -> set[Path] | None:
    """Файлы, которые git реально отдаст при клонировании."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return {ROOT / name for name in out.stdout.split("\0") if name}


def app_modules() -> list[str]:
    names: list[str] = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(ROOT).with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == "__init__":
            parts.pop()
        if parts:
            names.append(".".join(parts))
    return names


# Telegram принимает только этот набор тегов. Любой другой (или незакрытый)
# ломает отправку целиком: бот получит «can't parse entities» и промолчит.
ALLOWED_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "a", "code", "pre", "span", "tg-spoiler", "tg-emoji", "blockquote",
}
TAG_RE = re.compile(r"<\s*(/?)\s*([a-zA-Z0-9-]+)([^>]*)>")


def html_problem(value: str) -> str:
    """Возвращает описание проблемы с разметкой или пустую строку."""
    stack: list[str] = []
    for closing, tag, _attrs in TAG_RE.findall(value):
        name = tag.lower()
        if name not in ALLOWED_TAGS:
            return f"тег <{name}> Telegram не поддерживает"
        if closing:
            if not stack:
                return f"</{name}> без открывающего тега"
            if stack[-1] != name:
                return f"</{name}> закрывает <{stack[-1]}>"
            stack.pop()
        else:
            stack.append(name)
    if stack:
        return f"не закрыт тег <{stack[-1]}>"
    return ""


def text_constants() -> list[tuple[str, str]]:
    module = importlib.import_module("app.texts")
    found: list[tuple[str, str]] = []
    for name in dir(module):
        if name.startswith("_") or not name.isupper():
            continue
        value = getattr(module, name)
        if isinstance(value, str):
            found.append((name, value))
    return sorted(found)


def main() -> int:
    print("\n▶ Все файлы кода лежат в git")
    tracked = tracked_files()
    if tracked is None:
        print("  ⏭ git недоступен — проверку пропускаю")
    else:
        missing = [p.relative_to(ROOT).as_posix() for p in local_files() if p not in tracked]
        check(
            "каждый .py и .sh отслеживается git",
            not missing,
            "не попали в репозиторий: " + ", ".join(missing) if missing else "",
        )

    print("\n▶ Каждый модуль импортируется")
    for name in app_modules():
        try:
            importlib.import_module(name)
        except Exception as error:  # noqa: BLE001 — нужен любой сбой импорта
            check(name, False, f"{type(error).__name__}: {error}")
        else:
            check(name, True)

    print("\n▶ Разметка текстов понятна Telegram")
    broken = [(name, html_problem(value)) for name, value in text_constants()]
    broken = [(name, problem) for name, problem in broken if problem]
    check(
        "во всех текстах теги закрыты и поддерживаются",
        not broken,
        "; ".join(f"{name}: {problem}" for name, problem in broken),
    )

    print("\n▶ Точка входа импортируется")
    try:
        importlib.import_module("bot")
    except Exception as error:  # noqa: BLE001
        check("bot.py", False, f"{type(error).__name__}: {error}")
    else:
        check("bot.py", True)

    print("\n" + "=" * 60)
    print(f"Пройдено: {passed} · Провалено: {failed}")
    if failed:
        print("Пакет собран неправильно ❌")
        return 1
    print("Пакет целый ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
