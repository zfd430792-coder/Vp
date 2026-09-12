#!/usr/bin/env bash
#
# Обновление бота одной командой:
#
#   ./update.sh
#
# Ничего вводить не нужно: токен и владелец берутся из существующего .env.
# Перед обновлением делается копия базы, а если новая версия не поднимется —
# скрипт сам вернёт предыдущую и перезапустит бота.
#
#   ./update.sh --check   только посмотреть, есть ли обновления

set -uo pipefail

# Этот скрипт обновляет в том числе сам себя. bash дочитывает файл по ходу
# выполнения, поэтому подмена на середине может оборвать работу на любом месте.
# Чтобы этого не случилось, выполняемся из временной копии, а каталог проекта
# передаём через переменную окружения.
if [ -n "${UPDATE_PROJECT_DIR:-}" ]; then
    PROJECT_DIR="$UPDATE_PROJECT_DIR"
    trap 'rm -f "${UPDATE_COPY:-}"' EXIT
else
    PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    SELF_COPY="$(mktemp 2>/dev/null || printf '/tmp/update-bot.%s.sh' "$$")"
    if cat "${BASH_SOURCE[0]}" > "$SELF_COPY" 2>/dev/null; then
        export UPDATE_PROJECT_DIR="$PROJECT_DIR" UPDATE_COPY="$SELF_COPY"
        exec bash "$SELF_COPY" "$@"
    fi
    rm -f "$SELF_COPY"
fi

cd "$PROJECT_DIR"

if [ ! -f "$PROJECT_DIR/scripts/ui.sh" ]; then
    printf 'Не найден scripts/ui.sh — похоже, репозиторий скачан не полностью.\n' >&2
    exit 1
fi
# shellcheck source=scripts/ui.sh
. "$PROJECT_DIR/scripts/ui.sh"

SELF="${BOT_CMD:-./manage.sh}"
PY="$PROJECT_DIR/.venv/bin/python"
ENV_FILE="$PROJECT_DIR/.env"
BACKUP_DIR="$PROJECT_DIR/data/backups"
BACKUPS_KEEP=5
TOTAL_STEPS=5

CHECK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --check|-c) CHECK_ONLY=1 ;;
    esac
done

notify_owner() {
    # Бот в момент обновления перезапускается и написать сам не может,
    # поэтому результат отправляет скрипт — токен берёт из того же .env
    [ -x "$PY" ] || return 0
    [ -f "$PROJECT_DIR/scripts/notify_owner.py" ] || return 0
    "$PY" "$PROJECT_DIR/scripts/notify_owner.py" "$1" >/dev/null 2>&1 || true
}

step_no=0
step() {
    step_no=$((step_no + 1))
    ui_step "$step_no/$TOTAL_STEPS" "$1"
}

die() {
    ui_blank
    ui_fail "$1"
    shift || true
    while [ "$#" -gt 0 ]; do
        ui_note "$1"
        shift
    done
    ui_blank
    exit 1
}

ui_blank
ui_rule
ui_blank
ui_head "🔄  Обновление бота"
ui_blank
ui_text "Токен и владельца вводить не нужно — они уже сохранены."
ui_blank
ui_rule

# ------------------------------------------------------------------ 1. что есть сейчас

step "Проверяю, что всё на месте"

[ -x "$PY" ] || die "Окружение не найдено" "Сначала установка: bash install.sh"
[ -f "$ENV_FILE" ] || die "Нет файла .env" "Сначала установка: bash install.sh"
ui_ok ".env на месте, вводить ничего не придётся"

# Команду управления делаем сразу, а не после обновления: иначе при «обновлять
# нечего» скрипт выходил раньше, и команда так и не появлялась.
ensure_launcher() {
    [ -f "$PROJECT_DIR/scripts/launcher.sh" ] || return 1
    # shellcheck source=scripts/launcher.sh
    . "$PROJECT_DIR/scripts/launcher.sh"
    install_launcher "$PROJECT_DIR" quiet || return 1
    SELF="$LAUNCHER_NAME"
    return 0
}

if ensure_launcher; then
    ui_ok "команда «$LAUNCHER_NAME» на месте: $LAUNCHER_PATH"
    if [ "$LAUNCHER_ON_PATH" -eq 0 ]; then
        ui_note "заработает в новом терминале, либо: export PATH=\"$LAUNCHER_DIR:\$PATH\""
    fi
fi


if ! command -v git >/dev/null 2>&1 || [ ! -d "$PROJECT_DIR/.git" ]; then
    die "Обновление через git недоступно" \
        "Проект скачан без истории git, автоматически обновить нечем." \
        "Скачайте новую версию заново и перенесите файл .env и папку data."
fi

DB_PATH_VALUE="$(sed -n 's/^DB_PATH=//p' "$ENV_FILE" 2>/dev/null | head -n 1 | tr -d '"\r')"
DB_PATH="${DB_PATH_VALUE:-data/bot.db}"
case "$DB_PATH" in
    /*) DB_FULL="$DB_PATH" ;;
    *)  DB_FULL="$PROJECT_DIR/$DB_PATH" ;;
esac
if [ -f "$DB_FULL" ]; then
    ui_ok "база на месте: $DB_PATH"
else
    ui_note "базы пока нет — обновление ничего не потеряет"
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
OLD_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo '')"
ui_ok "ветка $BRANCH, текущая версия ${OLD_COMMIT:0:7}"

# ------------------------------------------------------------------ 2. есть ли обновления

step "Смотрю, что нового"

if ! git fetch --quiet origin "$BRANCH" 2>/dev/null; then
    die "Не удалось связаться с репозиторием" \
        "Проверьте интернет и доступ к origin, затем повторите."
fi

REMOTE_COMMIT="$(git rev-parse "origin/$BRANCH" 2>/dev/null || echo '')"
if [ -z "$REMOTE_COMMIT" ]; then
    die "В origin нет ветки $BRANCH" "Проверьте, туда ли вы смотрите."
fi

if [ "$OLD_COMMIT" = "$REMOTE_COMMIT" ]; then
    ui_ok "у вас уже последняя версия"

    # Код мог обновить руками через git pull — тогда тянуть нечего, но запущенный
    # процесс всё ещё старый. Сверяем отметку, которую бот пишет при старте.
    RUNNING_FILE="$(dirname "$DB_FULL")/running_version"
    RUNNING="$(cat "$RUNNING_FILE" 2>/dev/null | tr -d ' \r\n')"
    CURRENT="${OLD_COMMIT:0:7}"
    NEED_RESTART=0
    if ! "$PROJECT_DIR/manage.sh" is-running; then
        ui_warn "бот сейчас не работает — запускаю"
        NEED_RESTART=1
    elif [ -z "$RUNNING" ]; then
        ui_note "не знаю, на какой версии запущен бот — перезапускаю на всякий случай"
        NEED_RESTART=1
    elif [ "$RUNNING" != "$CURRENT" ]; then
        ui_warn "запущен код версии $RUNNING, а на диске уже $CURRENT"
        ui_note "похоже, код обновляли вручную — перезапускаю"
        NEED_RESTART=1
    else
        ui_ok "бот работает на этой же версии, перезапуск не нужен"
    fi

    if [ "$NEED_RESTART" -eq 1 ]; then
        "$PROJECT_DIR/manage.sh" restart --quiet || true
        if "$PROJECT_DIR/manage.sh" is-healthy 8; then
            ui_ok "бот перезапущен на версии $CURRENT"
            notify_owner "🔄 <b>Бот перезапущен</b>
Код был обновлён раньше, процесс работал на старой версии.
Сейчас работает <code>$CURRENT</code>."
        else
            ui_fail "бот не поднялся после перезапуска"
            ui_note "посмотрите: $SELF logs"
        fi
    fi
    ui_blank
    ui_rule
    ui_blank
    ui_head "✅  Всё на свежей версии"
    ui_blank
    if [ "$NEED_RESTART" -eq 1 ]; then
        ui_text "Код был актуальный, но процесс работал на старом — перезапустил."
    else
        ui_text "Бот уже на свежей версии, обновлять и перезапускать нечего."
    fi
    ui_blank
    ui_text "Управление"
    ui_cmd "$SELF" "состояние и версия"
    ui_cmd "$SELF version" "что менялось последним"
    ui_cmd "$SELF logs" "живой лог"
    if [ "${LAUNCHER_ON_PATH:-1}" -eq 0 ]; then
        ui_blank
        ui_text "Команда заработает в новом терминале. Прямо сейчас:"
        ui_sub "export PATH=\"$LAUNCHER_DIR:\$PATH\""
    fi
    ui_blank
    ui_rule
    ui_blank
    exit 0
fi

CHANGED="$(git diff --name-only "$OLD_COMMIT" "$REMOTE_COMMIT" 2>/dev/null | wc -l | tr -d ' ')"
COMMITS="$(git rev-list --count "$OLD_COMMIT..$REMOTE_COMMIT" 2>/dev/null || echo '?')"
ui_ok "новых коммитов: $COMMITS, изменённых файлов: $CHANGED"
git log --oneline --no-decorate "$OLD_COMMIT..$REMOTE_COMMIT" 2>/dev/null |
    head -n 5 | ui_trim | sed 's/^/         /'

if [ "$CHECK_ONLY" -eq 1 ]; then
    ui_blank
    ui_rule
    ui_blank
    ui_head "ℹ️  Обновления есть"
    ui_blank
    ui_text "Установить: ./update.sh"
    ui_blank
    ui_rule
    ui_blank
    exit 0
fi

# ------------------------------------------------------------------ 3. копия базы

step "Делаю копию базы"

if [ -f "$DB_FULL" ]; then
    STAMP="$(date '+%Y%m%d-%H%M%S')"
    BACKUP_FILE="$BACKUP_DIR/bot-$STAMP.db"
    if SIZE="$("$PY" "$PROJECT_DIR/scripts/backup_db.py" "$DB_FULL" "$BACKUP_FILE" 2>&1)"; then
        ui_ok "копия сохранена: data/backups/bot-$STAMP.db (${SIZE} МБ)"
    else
        die "Не удалось сделать копию базы" "$SIZE" "Обновление остановлено — данные важнее."
    fi
    # Оставляем только последние копии, чтобы не забить диск
    OLD_BACKUPS="$(ls -1t "$BACKUP_DIR"/bot-*.db 2>/dev/null | tail -n +$((BACKUPS_KEEP + 1)))"
    if [ -n "$OLD_BACKUPS" ]; then
        printf '%s\n' "$OLD_BACKUPS" | while IFS= read -r old; do rm -f "$old"; done
        ui_note "старые копии удалены, оставлено последних: $BACKUPS_KEEP"
    fi
else
    ui_note "базы нет — копировать нечего"
fi

# ------------------------------------------------------------------ 4. обновление кода

step "Ставлю новую версию"

STASHED=0
if [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
    if git stash push --quiet --message "update.sh $(date '+%Y-%m-%d %H:%M')" 2>/dev/null; then
        STASHED=1
        ui_warn "ваши правки в файлах проекта отложены в git stash"
        ui_note "вернуть их потом: git stash pop"
    else
        die "В проекте есть изменённые файлы" \
            "Сохраните или отмените их и повторите:" \
            "git status"
    fi
fi

if ! git merge --ff-only --quiet "origin/$BRANCH" 2>/dev/null; then
    [ "$STASHED" -eq 1 ] && git stash pop --quiet 2>/dev/null || true
    die "Обновление не применилось без конфликтов" \
        "Скорее всего история разошлась. Посмотрите: git status" \
        "Ничего не изменено, бот продолжает работать."
fi

NEW_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo '')"
ui_ok "код обновлён: ${OLD_COMMIT:0:7} → ${NEW_COMMIT:0:7}"

if ! "$PY" -m pip install --quiet -r "$PROJECT_DIR/requirements.txt"; then
    ui_warn "зависимости обновить не удалось — продолжаю на текущих"
else
    ui_ok "$("$PY" - <<'PYCODE'
import aiogram, aiosqlite
print(f"aiogram {aiogram.__version__}, aiosqlite {aiosqlite.__version__}")
PYCODE
)"
fi

chmod +x "$PROJECT_DIR"/*.sh "$PROJECT_DIR"/scripts/*.sh 2>/dev/null || true

# Обновление могло принести новую версию обёртки — перезаписываем её свежей
ensure_launcher >/dev/null 2>&1 || true

# ------------------------------------------------------------------ 5. перезапуск и проверка

step "Перезапускаю и проверяю"

"$PROJECT_DIR/manage.sh" restart --quiet || true

# Не «процесс появился», а «прожил несколько секунд»: падающий по кругу бот
# не должен считаться успешным обновлением
if "$PROJECT_DIR/manage.sh" is-healthy 8; then
    ui_ok "бот работает на новой версии"
    SCHEMA="$("$PY" - "$DB_FULL" <<'PYCODE' 2>/dev/null || true
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1])
if path.exists():
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        print(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()
PYCODE
)"
    [ -n "$SCHEMA" ] && ui_note "схема базы: версия $SCHEMA"

    ui_blank
    ui_rule
    ui_blank
    ui_head "✅  Обновлено"
    ui_blank
    ui_text "Бот работает на версии ${NEW_COMMIT:0:7}. Вводить ничего не потребовалось."
    if [ "$STASHED" -eq 1 ]; then
        ui_blank
        ui_text "Ваши правки лежат в git stash — вернуть: git stash pop"
    fi
    notify_owner "✅ <b>Бот обновлён</b>
Версия: <code>${NEW_COMMIT:0:7}</code>
Коммитов применено: ${COMMITS}
Копия базы сохранена перед обновлением."

    ui_blank
    ui_text "Полезное"
    ui_cmd "$SELF" "состояние и статистика"
    ui_cmd "$SELF logs" "живой лог"
    ui_cmd "$SELF update --check" "посмотреть, есть ли обновления"
    ui_blank
    ui_rule
    ui_blank
    exit 0
fi

# --- не поднялся: возвращаем предыдущую версию
ui_fail "новая версия не запустилась — возвращаю предыдущую"
ui_note "последние строки лога:"
ui_blank
if [ -f "$PROJECT_DIR/logs/bot.log" ]; then
    tail -n 12 "$PROJECT_DIR/logs/bot.log" | ui_trim | sed 's/^/         /'
else
    "$PROJECT_DIR/manage.sh" status 2>&1 | tail -n 10 | sed 's/^/         /'
fi
ui_blank

if git reset --hard --quiet "$OLD_COMMIT" 2>/dev/null; then
    ui_ok "код возвращён на ${OLD_COMMIT:0:7}"
    "$PY" -m pip install --quiet -r "$PROJECT_DIR/requirements.txt" >/dev/null 2>&1 || true
    "$PROJECT_DIR/manage.sh" restart --quiet || true
    if "$PROJECT_DIR/manage.sh" is-healthy 6; then
        ui_ok "бот снова работает на предыдущей версии"
        notify_owner "⚠️ <b>Обновление откатилось</b>
Новая версия <code>${NEW_COMMIT:0:7}</code> не запустилась, вернул <code>${OLD_COMMIT:0:7}</code>.
Бот работает на прежней версии, причина в логе."
    else
        ui_warn "поднять бота не удалось даже на старой версии"
        ui_note "запустите вручную и посмотрите лог: $SELF start, затем $SELF logs"
        notify_owner "🚨 <b>Бот не поднялся</b>
Обновление откатилось на <code>${OLD_COMMIT:0:7}</code>, но запустить бота не удалось.
Нужны руки: ./manage.sh start и ./manage.sh logs"
    fi
else
    ui_warn "вернуть код автоматически не получилось"
    ui_note "сделайте вручную: git reset --hard ${OLD_COMMIT:0:7} && ./manage.sh start"
fi

ui_blank
ui_text "База осталась обновлённой, её копия лежит в data/backups."
ui_text "Причина сбоя — в логе выше."
ui_blank
exit 1
