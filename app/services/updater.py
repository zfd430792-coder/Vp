"""Версия бота и запуск обновления из админ-панели.

Сам процесс обновления делает scripts/../update.sh: он останавливает бота,
поэтому запускается отдельным процессом, а результат присылает владельцу
сообщением. Здесь только чтение версии и запуск.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

GIT_TIMEOUT = 25


async def _git(project_dir: Path, *args: str, timeout: int = 10) -> str | None:
    """Запускает git и возвращает вывод, либо None при любой заминке."""
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(project_dir),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError) as error:
        log.debug("git недоступен: %s", error)
        return None

    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return None

    if process.returncode != 0:
        return None
    return stdout.decode("utf-8", "replace").strip()


def read_commit(project_dir: Path) -> str:
    """Короткий хэш текущего коммита, прочитанный прямо из файлов .git.

    Без вызова git: нужно на старте бота, где лишний процесс ни к чему, да и
    git может быть не установлен.
    """
    head = project_dir / ".git" / "HEAD"
    try:
        text = head.read_text(encoding="utf-8").strip()
    except OSError:
        return ""

    if not text.startswith("ref: "):
        return text[:7]

    name = text[5:].strip()
    ref = project_dir / ".git" / name
    try:
        return ref.read_text(encoding="utf-8").strip()[:7]
    except OSError:
        pass

    packed = project_dir / ".git" / "packed-refs"
    try:
        for line in packed.read_text(encoding="utf-8").splitlines():
            if line.endswith(f" {name}"):
                return line.split()[0][:7]
    except OSError:
        pass
    return ""


def mark_running(project_dir: Path, state_dir: Path) -> None:
    """Записывает версию, на которой бот сейчас запущен.

    По этой отметке update.sh понимает, что код обновили, а процесс остался
    прежним: после ручного git pull перезапуск нужен, даже если тянуть нечего.
    """
    commit = read_commit(project_dir) or "unknown"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "running_version").write_text(commit + "\n", encoding="utf-8")
    except OSError as error:
        log.debug("не удалось записать отметку версии: %s", error)


def available(project_dir: Path) -> bool:
    """Можно ли обновляться автоматически: нужен git и сам скрипт."""
    return (project_dir / ".git").exists() and (project_dir / "update.sh").exists()


async def version(project_dir: Path) -> dict[str, Any]:
    commit = await _git(project_dir, "rev-parse", "--short", "HEAD")
    subject = await _git(project_dir, "log", "-1", "--format=%s")
    when = await _git(project_dir, "log", "-1", "--format=%cd", "--date=format:%d.%m.%Y %H:%M")
    branch = await _git(project_dir, "rev-parse", "--abbrev-ref", "HEAD")
    return {
        "commit": commit or "неизвестно",
        "subject": subject or "",
        "date": when or "",
        "branch": branch or "",
    }


async def check(project_dir: Path) -> dict[str, Any]:
    """Смотрит, есть ли новые коммиты в origin. Требует сети."""
    branch = await _git(project_dir, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch:
        return {"ok": False, "reason": "git недоступен"}

    if await _git(project_dir, "fetch", "--quiet", "origin", branch, timeout=GIT_TIMEOUT) is None:
        return {"ok": False, "reason": "не удалось связаться с репозиторием"}

    behind = await _git(project_dir, "rev-list", "--count", f"HEAD..origin/{branch}")
    latest = await _git(project_dir, "log", "-1", "--format=%s", f"origin/{branch}")
    try:
        count = int(behind or "0")
    except ValueError:
        count = 0
    return {"ok": True, "behind": count, "latest": latest or "", "branch": branch}


def start(project_dir: Path) -> bool:
    """Запускает update.sh отдельным процессом и сразу отпускает его.

    Отдельная сессия нужна, потому что скрипт остановит бота: иначе он
    погибнет вместе с ним, не доведя обновление до конца.
    """
    script = project_dir / "update.sh"
    if not script.exists():
        return False

    log_dir = project_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "update.log"

    try:
        with log_file.open("ab") as stream:
            subprocess.Popen(  # noqa: S603 - запускаем свой же скрипт из каталога проекта
                ["bash", str(script)],
                cwd=str(project_dir),
                stdout=stream,
                stderr=stream,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env={**os.environ, "TERM": "dumb"},
            )
    except OSError as error:
        log.warning("не удалось запустить обновление: %s", error)
        return False
    return True
