#!/usr/bin/env bash
#
# Управление ботом: ./manage.sh start|stop|restart|status|logs|update
#
# Скрипт сам понимает, как бот установлен: служба systemd или собственный сторож.

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [ ! -f "$PROJECT_DIR/scripts/ui.sh" ]; then
    printf 'Не найден scripts/ui.sh — похоже, репозиторий скачан не полностью.\n' >&2
    printf 'Скачайте проект заново: git clone <репозиторий>\n' >&2
    exit 1
fi
# shellcheck source=scripts/ui.sh
. "$PROJECT_DIR/scripts/ui.sh"

PY="$PROJECT_DIR/.venv/bin/python"
SERVICE_NAME="dating-bot"
PID_FILE="$PROJECT_DIR/data/bot.pid"
CHILD_PID_FILE="$PROJECT_DIR/data/bot.child.pid"
LOG_FILE="$PROJECT_DIR/logs/bot.log"
MODE_FILE="$PROJECT_DIR/data/runmode"

# Как нас позвали: через глобальную команду или напрямую из папки проекта
SELF="${BOT_CMD:-./manage.sh}"

QUIET=0
for arg in "$@"; do
    [ "$arg" = "--quiet" ] && QUIET=1
done

ok()   { [ "$QUIET" -eq 1 ] || printf '  %s✔%s %s\n' "$UI_GREEN" "$UI_R" "$1"; }
warn() { printf '  %s!%s %s\n' "$UI_YELLOW" "$UI_R" "$1"; }
# В тихом режиме о неудаче сообщает вызывающий скрипт, чтобы не было двух строк
fail() { [ "$QUIET" -eq 1 ] || printf '  %s✘%s %s\n' "$UI_RED" "$UI_R" "$1"; }
# А о поломке установки сообщаем всегда: иначе бот молча не запустится
hard_fail() { printf '  %s✘%s %s\n' "$UI_RED" "$UI_R" "$1"; }
say()  { [ "$QUIET" -eq 1 ] || printf '  %s\n' "$1"; }

MODE="plain"
[ -f "$MODE_FILE" ] && MODE="$(tr -d ' \r\n' < "$MODE_FILE")"

sc() {
    if [ "$MODE" = "systemd-user" ]; then
        systemctl --user "$@"
    elif [ "$(id -u)" -eq 0 ]; then
        systemctl "$@"
    else
        sudo systemctl "$@"
    fi
}

pid_alive() {
    [ -f "$1" ] || return 1
    local pid
    pid="$(cat "$1" 2>/dev/null || true)"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null
}

supervisor_alive() { pid_alive "$PID_FILE"; }
child_alive()      { pid_alive "$CHILD_PID_FILE"; }

is_running() {
    # Живым считаем только сам бот: сторож может ждать следующей попытки
    case "$MODE" in
        systemd-user|systemd-system) sc is-active --quiet "$SERVICE_NAME" ;;
        *) child_alive ;;
    esac
}

restart_count() {
    # Сколько раз служба перезапускалась — растёт, если бот падает по кругу
    case "$MODE" in
        systemd-user|systemd-system)
            sc show "$SERVICE_NAME" --property=NRestarts --value 2>/dev/null || printf '0'
            ;;
        *) printf '0' ;;
    esac
}

do_healthcheck() {
    # «Запустился» — это не «процесс появился», а «один и тот же процесс прожил
    # несколько секунд». Иначе падающий по кругу бот выглядел бы работающим.
    local settle="${1:-8}" waited=0 first="" second="" restarts_before="" restarts_after=""

    while [ "$waited" -lt 12 ]; do
        is_running && break
        waited=$((waited + 1))
        sleep 1
    done
    is_running || return 1

    first="$(cat "$CHILD_PID_FILE" 2>/dev/null || true)"
    restarts_before="$(restart_count)"
    sleep "$settle"
    is_running || return 1

    case "$MODE" in
        systemd-user|systemd-system)
            restarts_after="$(restart_count)"
            [ "$restarts_before" = "$restarts_after" ] || return 1
            ;;
        *)
            second="$(cat "$CHILD_PID_FILE" 2>/dev/null || true)"
            [ -n "$first" ] && [ "$first" = "$second" ] || return 1
            ;;
    esac
    return 0
}

check_env() {
    if [ ! -x "$PY" ]; then
        fail "окружение не найдено — запустите установку: bash install.sh"
        exit 1
    fi
    if [ ! -f "$PROJECT_DIR/.env" ]; then
        fail "нет файла .env — запустите установку: bash install.sh"
        exit 1
    fi
}

mode_title() {
    case "$MODE" in
        systemd-system) printf 'служба systemd, системная' ;;
        systemd-user)   printf 'служба systemd, пользовательская' ;;
        *)              printf 'собственный сторож процесса' ;;
    esac
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
                ok "сторож уже работает и ждёт следующей попытки"
                return 0
            fi
            if [ ! -f "$PROJECT_DIR/scripts/run_forever.sh" ]; then
                hard_fail "нет файла scripts/run_forever.sh — репозиторий скачан не полностью"
                return 1
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

    fail "запустить не удалось — посмотрите: $SELF logs"
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
    # Выравнивание считает Python: в bash printf padding идёт по байтам,
    # а кириллица занимает два байта на символ — столбцы бы разъехались.
    [ -x "$PY" ] || return 0
    local db_value
    db_value="$(sed -n 's/^DB_PATH=//p' "$PROJECT_DIR/.env" 2>/dev/null | head -n 1 | tr -d '"\r')"
    DB_PATH="${db_value:-data/bot.db}" "$PY" - <<'PYCODE' 2>/dev/null || true
import os
import sqlite3
from pathlib import Path

path = Path(os.environ.get("DB_PATH", "data/bot.db"))
if not path.is_absolute():
    path = Path.cwd() / path
if not path.exists():
    print(f"{'База':<12}ещё не создана")
    raise SystemExit(0)

connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
try:

    def one(sql: str) -> int:
        try:
            return int(connection.execute(sql).fetchone()[0])
        except sqlite3.Error:
            return 0

    users = one("SELECT COUNT(*) FROM users WHERE status != 'deleted'")
    active = one(
        "SELECT COUNT(*) FROM users"
        " WHERE last_active_at > strftime('%s', 'now') - 86400"
    )
    profiles = one("SELECT COUNT(*) FROM profiles WHERE is_complete = 1")
    matches = one("SELECT COUNT(*) FROM matches")
    reports = one("SELECT COUNT(*) FROM reports WHERE status = 'open'")
    verified = one("SELECT COUNT(*) FROM users WHERE verified = 1")
    size_mb = path.stat().st_size / 1024 / 1024

    rows = [
        ("Людей", str(users)),
        ("Активны", f"{active} за сутки"),
        ("Анкет", f"{profiles} заполнено, {verified} с галочкой"),
        ("Симпатий", str(matches)),
        ("Жалоб", f"{reports} открытых"),
        ("База", f"{size_mb:.1f} МБ"),
    ]
    for label, value in rows:
        print(f"{label:<12}{value}")
finally:
    connection.close()
PYCODE
}

do_status() {
    ui_blank
    ui_rule
    ui_blank
    ui_head "💛  Бот знакомств"
    ui_blank

    if is_running; then
        printf '     Состояние   %s● работает%s\n' "$UI_GREEN" "$UI_R"
    elif [ "$MODE" = "plain" ] && supervisor_alive; then
        printf '     Состояние   %s● перезапускается%s\n' "$UI_YELLOW" "$UI_R"
        ui_sub "сторож ждёт следующей попытки, причина в логе"
    else
        printf '     Состояние   %s● остановлен%s\n' "$UI_RED" "$UI_R"
    fi
    printf '     Режим       %s\n' "$(mode_title)"

    case "$MODE" in
        systemd-user|systemd-system)
            since="$(sc show "$SERVICE_NAME" --property=ActiveEnterTimestamp --value 2>/dev/null || true)"
            [ -n "$since" ] && printf '     Запущен     %s\n' "$since"
            ;;
        *)
            if supervisor_alive; then
                printf '     Сторож      PID %s\n' "$(cat "$PID_FILE" 2>/dev/null)"
            fi
            ;;
    esac

    ui_blank
    db_summary | sed 's/^/     /'
    ui_blank
    ui_text "Последние строки лога"
    case "$MODE" in
        systemd-user|systemd-system)
            sc --no-pager -n 8 status "$SERVICE_NAME" 2>/dev/null |
                tail -n 6 | ui_trim 66 | sed 's/^/       /' || true
            ;;
        *)
            if [ -f "$LOG_FILE" ]; then
                tail -n 8 "$LOG_FILE" | ui_trim 66 | tail -n 6 | sed 's/^/       /'
            else
                ui_sub "лога пока нет"
            fi
            ;;
    esac
    ui_blank
    ui_rule
    ui_blank
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
                warn "лога пока нет: $LOG_FILE"
            fi
            ;;
    esac
}

do_update() {
    # Вся логика обновления в update.sh: копия базы, откат при сбое, проверка.
    # Здесь только единая точка входа, чтобы не держать две реализации.
    check_env
    exec bash "$PROJECT_DIR/update.sh" "$@"
}

usage() {
    ui_blank
    ui_head "Управление ботом"
    ui_blank
    ui_cmd "$SELF" "состояние и статистика"
    ui_cmd "$SELF update" "обновить бота"
    ui_cmd "$SELF logs" "живой лог, Ctrl+C — выйти"
    ui_cmd "$SELF restart" "перезапустить"
    ui_cmd "$SELF start" "запустить"
    ui_cmd "$SELF stop" "остановить"
    ui_blank
}

case "${1:-status}" in
    start)      do_start ;;
    stop)       do_stop ;;
    restart)    do_stop; do_start ;;
    status)     do_status ;;
    logs)       do_logs ;;
    update)     shift || true; do_update "$@" ;;
    is-running) is_running ;;
    is-healthy) shift || true; do_healthcheck "${1:-8}" ;;
    help|-h|--help) usage ;;
    *)          usage; exit 1 ;;
esac
