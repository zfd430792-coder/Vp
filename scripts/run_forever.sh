#!/usr/bin/env bash
#
# Сторож для систем без systemd: держит бота поднятым 24/7 и ведёт лог.
# Запускается через manage.sh, вручную вызывать не нужно.
#
# Логика простая и предсказуемая:
#   код 0 — бот остановлен штатно, больше не поднимаем;
#   код 2 — ошибка настройки (токен, .env), перезапуск не поможет — останавливаемся
#           и оставляем в логе причину;
#   любой другой код — сбой, который может пройти сам (сеть, Telegram): повторяем
#           бесконечно, увеличивая паузу до пяти минут, чтобы не жечь процессор.

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

PY="$PROJECT_DIR/.venv/bin/python"
LOG="$PROJECT_DIR/logs/bot.log"
CHILD_PID_FILE="$PROJECT_DIR/data/bot.child.pid"

DELAY_START=5
DELAY_MAX=300
STABLE_SECONDS=60       # столько проработал — считаем, что бот поднялся нормально
LOG_MAX_BYTES=$((20 * 1024 * 1024))

mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data"

log() {
    printf '%s [сторож] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOG"
}

rotate_log() {
    # Чтобы лог не съел диск: одна предыдущая копия, остальное отбрасываем
    [ -f "$LOG" ] || return 0
    local size
    size=$(wc -c < "$LOG" 2>/dev/null || echo 0)
    if [ "$size" -gt "$LOG_MAX_BYTES" ]; then
        mv -f "$LOG" "$LOG.1" 2>/dev/null || return 0
        log "лог превысил $((LOG_MAX_BYTES / 1024 / 1024)) МБ, предыдущий сохранён как bot.log.1"
    fi
}

shutdown() {
    log "получен сигнал остановки"
    if [ -f "$CHILD_PID_FILE" ]; then
        child="$(cat "$CHILD_PID_FILE" 2>/dev/null || true)"
        [ -n "$child" ] && kill "$child" 2>/dev/null || true
    fi
    rm -f "$CHILD_PID_FILE"
    exit 0
}
trap shutdown TERM INT

if [ ! -x "$PY" ]; then
    log "нет окружения $PY — сначала выполните bash install.sh"
    exit 2
fi

log "запуск, окружение $("$PY" --version 2>&1)"

DELAY="$DELAY_START"

while :; do
    rotate_log
    STARTED_AT="$(date +%s)"

    "$PY" "$PROJECT_DIR/bot.py" >> "$LOG" 2>&1 &
    CHILD=$!
    printf '%s\n' "$CHILD" > "$CHILD_PID_FILE"
    wait "$CHILD"
    CODE=$?
    rm -f "$CHILD_PID_FILE"

    RAN=$(( $(date +%s) - STARTED_AT ))

    if [ "$CODE" -eq 0 ]; then
        log "бот завершился штатно (код 0), больше не перезапускаю"
        exit 0
    fi

    if [ "$CODE" -eq 2 ]; then
        log "ошибка настройки (код 2) — перезапуск не поможет, причина выше в логе"
        log "исправьте и запустите снова: ./manage.sh start"
        exit 2
    fi

    if [ "$RAN" -ge "$STABLE_SECONDS" ]; then
        DELAY="$DELAY_START"   # бот работал нормально, начинаем отсчёт заново
    fi

    log "бот остановился с кодом $CODE, проработав ${RAN} с — перезапуск через ${DELAY} с"
    sleep "$DELAY"

    DELAY=$(( DELAY * 2 ))
    [ "$DELAY" -gt "$DELAY_MAX" ] && DELAY="$DELAY_MAX"
done
