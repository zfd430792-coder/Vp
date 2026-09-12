#!/usr/bin/env bash
#
# Управление ботом: ./manage.sh start|stop|restart|status|logs|update
#
# Скрипт сам понимает, как бот установлен: служба systemd или собственный сторож.

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

PY="$PROJECT_DIR/.venv/bin/python"
SERVICE_NAME="dating-bot"
PID_FILE="$PROJECT_DIR/data/bot.pid"
LOG_FILE="$PROJECT_DIR/logs/bot.log"
MODE_FILE="$PROJECT_DIR/data/runmode"

if [ -t 1 ] && command -v tput >/dev/null 2>&1 && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
    B="$(tput bold)"; D="$(tput dim)"; R="$(tput sgr0)"
    RED="$(tput setaf 1)"; GREEN="$(tput setaf 2)"; YELLOW="$(tput setaf 3)"; CYAN="$(tput setaf 6)"
else
    B=""; D=""; R=""; RED=""; GREEN=""; YELLOW=""; CYAN=""
fi

QUIET=0
for arg in "$@"; do
    [ "$arg" = "--quiet" ] && QUIET=1
done

say()  { [ "$QUIET" -eq 1 ] || printf '%s\n' "$1"; }
ok()   { [ "$QUIET" -eq 1 ] || printf '%s✔%s %s\n' "$GREEN" "$R" "$1"; }
warn() { printf '%s!%s %s\n' "$YELLOW" "$R" "$1"; }
fail() { printf '%s✘%s %s\n' "$RED" "$R" "$1"; }

MODE="plain"
[ -f "$MODE_FILE" ] && MODE="$(tr -d ' \r\n' < "$MODE_FILE")"

sc() {
    # обёртка над systemctl для нужного режима
    if [ "$MODE" = "systemd-user" ]; then
        systemctl --user "$@"
    elif [ "$(id -u)" -eq 0 ]; then
        systemctl "$@"
    else
        sudo systemctl "$@"
    fi
}

CHILD_PID_FILE="$PROJECT_DIR/data/bot.child.pid"

pid_alive() {
    # pid_alive путь_к_pid_файлу
    [ -f "$1" ] || return 1
    local pid
    pid="$(cat "$1" 2>/dev/null || true)"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null
}

supervisor_alive() { pid_alive "$PID_FILE"; }
child_alive()      { pid_alive "$CHILD_PID_FILE"; }

is_running() {
    # Живым считается только сам бот: сторож может ждать следующей попытки
    case "$MODE" in
        systemd-user|systemd-system) sc is-active --quiet "$SERVICE_NAME" ;;
        *) child_alive ;;
    esac
}

check_env() {
    if [ ! -x "$PY" ]; then
        fail "Окружение не найдено. Запустите установку: bash install.sh"
        exit 1
    fi
    if [ ! -f "$PROJECT_DIR/.env" ]; then
        fail "Нет файла .env. Запустите установку: bash install.sh"
        exit 1
    fi
}

do_start() {
    check_env
    if is_running; then
        ok "бот уже работает"
        return 0
    fi
    case "$MODE" in
        systemd-user|systemd-system)
            sc start "$SERVICE_NAME"
            ;;
        *)
            if supervisor_alive; then
                ok "сторож уже работает, ждёт следующей попытки"
                return 0
            fi
            mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data"
            nohup bash "$PROJECT_DIR/scripts/run_forever.sh" >/dev/null 2>&1 &
            printf '%s\n' "$!" > "$PID_FILE"
            ;;
    esac

    # Боту нужно время на старт: ждём, пока процесс действительно поднимется
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if is_running; then
            ok "бот запущен"
            return 0
        fi
        sleep 1
    done

    fail "запустить не удалось — посмотрите ./manage.sh logs"
    return 1
}

do_stop() {
    case "$MODE" in
        systemd-user|systemd-system)
            sc stop "$SERVICE_NAME" 2>/dev/null || true
            ;;
        *)
            if [ -f "$PID_FILE" ]; then
                pid="$(cat "$PID_FILE" 2>/dev/null || true)"
                [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
                for _ in 1 2 3 4 5 6 7 8 9 10; do
                    kill -0 "$pid" 2>/dev/null || break
                    sleep 0.5
                done
                kill -9 "$pid" 2>/dev/null || true
                rm -f "$PID_FILE"
            fi
            if [ -f "$CHILD_PID_FILE" ]; then
                child="$(cat "$CHILD_PID_FILE" 2>/dev/null || true)"
                [ -n "$child" ] && kill "$child" 2>/dev/null || true
                rm -f "$CHILD_PID_FILE"
            fi
            ;;
    esac
    ok "бот остановлен"
}

db_summary() {
    [ -x "$PY" ] || return 0
    DB_PATH_VALUE="$(sed -n 's/^DB_PATH=//p' "$PROJECT_DIR/.env" 2>/dev/null | head -n 1 | tr -d '"\r')"
    DB_PATH="${DB_PATH_VALUE:-data/bot.db}" "$PY" - <<'PYCODE' 2>/dev/null || true
import os
import sqlite3
from pathlib import Path

path = Path(os.environ.get("DB_PATH", "data/bot.db"))
if not path.is_absolute():
    path = Path.cwd() / path
if not path.exists():
    raise SystemExit(0)

connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
try:
    def one(sql: str) -> int:
        try:
            return int(connection.execute(sql).fetchone()[0])
        except sqlite3.Error:
            return 0

    users = one("SELECT COUNT(*) FROM users WHERE status != 'deleted'")
    profiles = one("SELECT COUNT(*) FROM profiles WHERE is_complete = 1")
    matches = one("SELECT COUNT(*) FROM matches")
    reports = one("SELECT COUNT(*) FROM reports WHERE status = 'open'")
    active = one(
        "SELECT COUNT(*) FROM users WHERE last_active_at > strftime('%s','now') - 86400"
    )
    size = path.stat().st_size / 1024 / 1024
    print(f"  Людей: {users}   анкет: {profiles}   активны за сутки: {active}")
    print(f"  Симпатий: {matches}   открытых жалоб: {reports}")
    print(f"  База: {size:.1f} МБ ({path})")
finally:
    connection.close()
PYCODE
}

do_status() {
    printf '\n%s Бот знакомств %s\n\n' "$CYAN" "$R"
    if is_running; then
        printf '  Состояние: %s● работает%s\n' "$GREEN" "$R"
    elif [ "$MODE" = "plain" ] && supervisor_alive; then
        printf '  Состояние: %s● перезапускается%s %s(сторож ждёт следующей попытки)%s\n' \
            "$YELLOW" "$R" "$D" "$R"
    else
        printf '  Состояние: %s● остановлен%s\n' "$RED" "$R"
    fi

    case "$MODE" in
        systemd-system) printf '  Режим: служба systemd (системная)\n' ;;
        systemd-user)   printf '  Режим: служба systemd (пользовательская)\n' ;;
        *)              printf '  Режим: собственный сторож процесса\n' ;;
    esac

    if [ "$MODE" = "plain" ] && [ -f "$PID_FILE" ]; then
        printf '  PID сторожа: %s\n' "$(cat "$PID_FILE" 2>/dev/null)"
    fi
    if [ "${MODE#systemd}" != "$MODE" ]; then
        since="$(sc show "$SERVICE_NAME" --property=ActiveEnterTimestamp --value 2>/dev/null || true)"
        [ -n "$since" ] && printf '  Запущен: %s\n' "$since"
    fi

    printf '\n'
    db_summary
    printf '\n'
    printf '  %sПоследние строки лога%s\n' "$B" "$R"
    case "$MODE" in
        systemd-user|systemd-system)
            sc --no-pager -n 8 status "$SERVICE_NAME" 2>/dev/null | tail -n 8 | sed 's/^/    /' || true
            ;;
        *)
            if [ -f "$LOG_FILE" ]; then
                tail -n 8 "$LOG_FILE" | sed 's/^/    /'
            else
                printf '    %sлога пока нет%s\n' "$D" "$R"
            fi
            ;;
    esac
    printf '\n'
}

do_logs() {
    case "$MODE" in
        systemd-user)   journalctl --user -u "$SERVICE_NAME" -n 50 -f ;;
        systemd-system)
            if [ "$(id -u)" -eq 0 ]; then
                journalctl -u "$SERVICE_NAME" -n 50 -f
            else
                sudo journalctl -u "$SERVICE_NAME" -n 50 -f
            fi
            ;;
        *)
            if [ -f "$LOG_FILE" ]; then
                tail -n 50 -f "$LOG_FILE"
            else
                warn "Лога пока нет: $LOG_FILE"
            fi
            ;;
    esac
}

do_update() {
    check_env
    if command -v git >/dev/null 2>&1 && [ -d "$PROJECT_DIR/.git" ]; then
        say "Забираю обновления…"
        git -C "$PROJECT_DIR" pull --ff-only || warn "git pull не удался, обновляю только зависимости"
    fi
    "$PY" -m pip install --quiet -r "$PROJECT_DIR/requirements.txt" && ok "зависимости обновлены"
    do_stop
    do_start
}

case "${1:-status}" in
    start)      do_start ;;
    stop)       do_stop ;;
    restart)    do_stop; do_start ;;
    status)     do_status ;;
    logs)       do_logs ;;
    update)     do_update ;;
    is-running) is_running ;;
    *)
        printf '\nИспользование: ./manage.sh %s\n\n' "{start|stop|restart|status|logs|update}"
        printf '  start     запустить бота\n'
        printf '  stop      остановить\n'
        printf '  restart   перезапустить\n'
        printf '  status    состояние и статистика\n'
        printf '  logs      живой лог (Ctrl+C — выйти)\n'
        printf '  update    обновить код и зависимости, перезапустить\n\n'
        exit 1
        ;;
esac
