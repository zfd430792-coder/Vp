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
