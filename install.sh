#!/usr/bin/env bash
#
# Установка бота знакомств одной командой:
#
#   bash install.sh
#
# Скрипт создаст окружение, спросит токен бота и ваш Telegram ID, настроит
# автозапуск и поднимет бота в фоне. Запускать можно повторно — настройки
# сохранятся, достаточно подтвердить.
#
# Без вопросов (для автоматизации):
#   BOT_TOKEN=... OWNER_IDS=... bash install.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [ ! -f "$PROJECT_DIR/scripts/ui.sh" ]; then
    printf 'Не найден scripts/ui.sh — похоже, репозиторий скачан не полностью.\n' >&2
    printf 'Скачайте проект заново: git clone <репозиторий>\n' >&2
    exit 1
fi
# shellcheck source=scripts/ui.sh
. "$PROJECT_DIR/scripts/ui.sh"

VENV="$PROJECT_DIR/.venv"
PY="$VENV/bin/python"
ENV_FILE="$PROJECT_DIR/.env"
SERVICE_NAME="dating-bot"
MIN_PY_MAJOR=3
MIN_PY_MINOR=10
TOTAL_STEPS=6

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

# ------------------------------------------------------------------ ввод с клавиатуры

# Проверяем не права на /dev/tty, а саму возможность его открыть: в контейнерах
# файл бывает на месте, но открытие падает.
TTY_IN=""
if ( : < /dev/tty ) 2>/dev/null; then
    TTY_IN="/dev/tty"
elif [ -t 0 ]; then
    TTY_IN="/dev/stdin"
fi

ANSWER=""

ask() {
    # ask "приглашение" -> ответ в $ANSWER, код 1 если ввода нет
    ANSWER=""
    [ -z "$TTY_IN" ] && return 1
    printf '     %s%s%s' "$UI_B" "$1" "$UI_R"
    if ! IFS= read -r ANSWER < "$TTY_IN" 2>/dev/null; then
        printf '\n'
        ANSWER=""
        return 1
    fi
    ANSWER="$(printf '%s' "$ANSWER" | tr -d ' \r\n')"
    return 0
}

ask_required() {
    # ask_required "приглашение" проверка "подсказка при ошибке"
    local prompt="$1" validator="$2" hint="$3" attempt=0
    while [ "$attempt" -lt 5 ]; do
        attempt=$((attempt + 1))
        if ! ask "$prompt"; then
            die "Не получается прочитать ответ из терминала" \
                "Запустите установку напрямую: bash install.sh" \
                "Либо передайте значения заранее:" \
                "BOT_TOKEN=... OWNER_IDS=... bash install.sh"
        fi
        if [ -n "$ANSWER" ] && "$validator" "$ANSWER"; then
            return 0
        fi
        ui_warn "$hint"
    done
    die "Слишком много неудачных попыток" "Запустите снова, когда данные будут под рукой"
}

confirm() {
    [ -z "$TTY_IN" ] && return 1
    printf '     %s%s%s %s[y/N]%s ' "$UI_B" "$1" "$UI_R" "$UI_D" "$UI_R"
    local answer=""
    IFS= read -r answer < "$TTY_IN" 2>/dev/null || answer=""
    case "$answer" in
        [yYдД]*) return 0 ;;
        *) return 1 ;;
    esac
}

pause() {
    [ -z "$TTY_IN" ] && return 0
    printf '     %sНажмите Enter, когда всё будет под рукой%s' "$UI_D" "$UI_R"
    IFS= read -r _ < "$TTY_IN" 2>/dev/null || true
    ui_blank
}

# ------------------------------------------------------------------ приветствие

ui_blank
ui_rule
ui_blank
ui_head "💛  Бот знакомств"
ui_blank
ui_text "Установка займёт около минуты. Скрипт соберёт"
ui_text "окружение, спросит два значения и запустит бота"
ui_text "в фоне — дальше он работает сам, круглосуточно."
ui_blank
ui_rule

# ------------------------------------------------------------------ предупреждение

NEEDS_INPUT=1
if [ -n "${BOT_TOKEN:-}" ] && [ -n "${OWNER_IDS:-}" ]; then
    NEEDS_INPUT=0
fi

ui_blank
ui_head "⚠️  Перед началом"
ui_blank
ui_text "Понадобится два значения — возьмите их сейчас,"
ui_text "чтобы не искать во время установки."
ui_blank
ui_item "Токен бота"
ui_sub "@BotFather → /newbot → придумайте имя."
ui_sub "В ответ придёт строка вида 1234567890:AA..."
ui_blank
ui_item "Ваш Telegram ID"
ui_sub "@userinfobot → /start."
ui_sub "В ответ придёт число — это и есть ID."
ui_blank
ui_text "Токен — пароль вашего бота. Не публикуйте его"
ui_text "и никому не пересылайте: по нему бота угоняют."
ui_blank
if [ "$NEEDS_INPUT" -eq 1 ] && [ ! -f "$ENV_FILE" ]; then
    pause
fi
ui_rule

# ------------------------------------------------------------------ 1. Python

step "Проверяю Python"

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c "import sys; raise SystemExit(0 if sys.version_info >= ($MIN_PY_MAJOR, $MIN_PY_MINOR) else 1)" 2>/dev/null; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    die "Нужен Python ${MIN_PY_MAJOR}.${MIN_PY_MINOR} или новее" \
        "Ubuntu, Debian:  sudo apt update && sudo apt install -y python3 python3-venv" \
        "Fedora:          sudo dnf install -y python3" \
        "macOS:           brew install python@3.12"
fi
ui_ok "$("$PYTHON_BIN" --version 2>&1) · $(command -v "$PYTHON_BIN")"

# ------------------------------------------------------------------ 2. окружение

step "Собираю окружение"

if [ ! -x "$PY" ]; then
    if ! "$PYTHON_BIN" -m venv "$VENV" 2>/dev/null; then
        die "Не удалось создать виртуальное окружение" \
            "Обычно не хватает пакета venv:" \
            "sudo apt install -y python3-venv"
    fi
    ui_ok "окружение создано в .venv"
else
    ui_ok "окружение уже готово: .venv"
fi

"$PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
if ! "$PY" -m pip install --quiet -r "$PROJECT_DIR/requirements.txt"; then
    die "Не удалось установить зависимости" \
        "Проверьте интернет и повторите:" \
        "$PY -m pip install -r requirements.txt"
fi
ui_ok "$("$PY" - <<'PYCODE'
import aiogram, aiosqlite
print(f"aiogram {aiogram.__version__}, aiosqlite {aiosqlite.__version__}")
PYCODE
)"

mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/logs"

# ------------------------------------------------------------------ 3. настройки

step "Спрашиваю токен и владельца"

read_env_value() {
    [ -f "$ENV_FILE" ] || return 0
    sed -n "s/^$1=//p" "$ENV_FILE" | head -n 1 | tr -d '"'"'"'\r'
}

valid_token()  { printf '%s' "$1" | grep -Eq '^[0-9]{6,}:[A-Za-z0-9_-]{30,}$'; }
valid_admins() { printf '%s' "$1" | grep -Eq '^[0-9]{5,15}(,[0-9]{5,15})*$'; }

TOKEN="${BOT_TOKEN:-}"
ADMINS="${OWNER_IDS:-}"

OLD_TOKEN="$(read_env_value BOT_TOKEN || true)"
OLD_ADMINS="$(read_env_value OWNER_IDS || true)"

if [ -z "$TOKEN" ] && [ -n "$OLD_TOKEN" ]; then
    ui_dim "в .env уже есть настройки: бот ...${OLD_TOKEN: -6}, владелец ${OLD_ADMINS:-не указан}"
    if confirm "Оставить их?"; then
        TOKEN="$OLD_TOKEN"
        ADMINS="$OLD_ADMINS"
        ui_ok "прежние настройки сохранены"
    fi
fi

if [ -z "$TOKEN" ]; then
    ui_blank
    ask_required 'Токен бота: ' valid_token \
        'Непохоже на токен. Ожидается вид 1234567890:AA... без пробелов'
    TOKEN="$ANSWER"
    ui_ok "токен принят"
fi

if [ -z "$ADMINS" ]; then
    ui_blank
    ask_required 'Ваш Telegram ID: ' valid_admins \
        'Нужно число, например 123456789. Несколько владельцев — через запятую'
    ADMINS="$ANSWER"
    ui_ok "владелец: $ADMINS"
fi

MOD_CHAT="${MODERATION_CHAT_ID:-$(read_env_value MODERATION_CHAT_ID || true)}"
if [ -z "$MOD_CHAT" ] && [ -n "$TTY_IN" ]; then
    ui_blank
    ui_dim "чат для уведомлений модерации — можно пропустить, нажав Enter"
    if ask 'ID чата модерации: '; then
        MOD_CHAT="$ANSWER"
    fi
    if [ -n "$MOD_CHAT" ] && ! printf '%s' "$MOD_CHAT" | grep -Eq '^-?[0-9]{5,20}$'; then
        ui_warn "непохоже на ID чата — пропускаю"
        MOD_CHAT=""
    fi
fi

# ------------------------------------------------------------------ 4. проверка токена

step "Проверяю токен в Telegram"

BOT_USERNAME=""
CHECK_OUTPUT="$("$PY" - "$TOKEN" <<'PYCODE' 2>&1 || true
import json
import sys
import urllib.error
import urllib.request

token = sys.argv[1]
try:
    with urllib.request.urlopen(
        f"https://api.telegram.org/bot{token}/getMe", timeout=20
    ) as response:
        data = json.load(response)
    me = data.get("result") or {}
    print("OK", me.get("username") or "", (me.get("first_name") or "").replace("\n", " "))
except urllib.error.HTTPError as error:
    print("BAD", error.code)
except Exception as error:  # нет сети, прокси, DNS
    print("NET", type(error).__name__)
PYCODE
)"

case "$CHECK_OUTPUT" in
    OK*)
        BOT_USERNAME="$(printf '%s' "$CHECK_OUTPUT" | awk '{print $2}')"
        BOT_TITLE="$(printf '%s' "$CHECK_OUTPUT" | cut -d' ' -f3-)"
        ui_ok "подключился бот @${BOT_USERNAME}"
        [ -n "$BOT_TITLE" ] && ui_note "название: $BOT_TITLE"
        ;;
    BAD*)
        ui_warn "Telegram не принял токен, ошибка $(printf '%s' "$CHECK_OUTPUT" | awk '{print $2}')"
        if ! confirm "Продолжить всё равно?"; then
            die "Установка остановлена" "Возьмите новый токен у @BotFather и запустите снова"
        fi
        ;;
    *)
        ui_warn "связаться с Telegram не удалось — проверю при запуске"
        ;;
esac

# ------------------------------------------------------------------ 5. автозапуск

step "Настраиваю автозапуск"

cat > "$ENV_FILE" <<ENVFILE
# Создано install.sh $(date '+%Y-%m-%d %H:%M')
BOT_TOKEN=$TOKEN
OWNER_IDS=$ADMINS
MODERATION_CHAT_ID=$MOD_CHAT
DB_PATH=data/bot.db
LOG_LEVEL=INFO
ENVFILE
chmod 600 "$ENV_FILE"
ui_ok ".env сохранён, читать его может только владелец файла"

RUN_USER="$(id -un)"
MODE="plain"

write_unit() {
    # write_unit путь [user]
    #
    # Restart=on-failure вместе с RestartPreventExitStatus=2: сбои сети лечим
    # перезапуском, а ошибку настройки — нет, иначе бот будет биться в цикле.
    # StartLimitIntervalSec=0 снимает лимит попыток: при долгом отсутствии сети
    # systemd не должен однажды сдаться и оставить бота лежать.
    local unit="$1" kind="${2:-system}"
    {
        printf '[Unit]\n'
        printf 'Description=Telegram-бот знакомств\n'
        if [ "$kind" != "user" ]; then
            printf 'After=network-online.target\n'
            printf 'Wants=network-online.target\n'
        fi
        printf 'StartLimitIntervalSec=0\n'
        printf '\n[Service]\n'
        printf 'Type=simple\n'
        printf 'WorkingDirectory=%s\n' "$PROJECT_DIR"
        printf 'ExecStart=%s %s/bot.py\n' "$PY" "$PROJECT_DIR"
        printf 'Restart=on-failure\n'
        printf 'RestartPreventExitStatus=2\n'
        printf 'RestartSec=5\n'
        printf 'StandardOutput=journal\n'
        printf 'StandardError=journal\n'
        printf 'SyslogIdentifier=%s\n' "$SERVICE_NAME"
        if [ "$kind" != "user" ]; then
            printf 'User=%s\n' "$RUN_USER"
            printf 'Group=%s\n' "$(id -gn)"
            printf 'NoNewPrivileges=true\n'
            printf 'PrivateTmp=true\n'
        fi
        printf '\n[Install]\n'
        if [ "$kind" = "user" ]; then
            printf 'WantedBy=default.target\n'
        else
            printf 'WantedBy=multi-user.target\n'
        fi
    } > "$unit"
}

if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    if [ "$(id -u)" -eq 0 ]; then
        write_unit "/etc/systemd/system/${SERVICE_NAME}.service"
        systemctl daemon-reload
        systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
        MODE="systemd-system"
        ui_ok "служба systemd «${SERVICE_NAME}» включена"
        ui_note "после перезагрузки сервера бот поднимется сам"
    else
        mkdir -p "$HOME/.config/systemd/user"
        write_unit "$HOME/.config/systemd/user/${SERVICE_NAME}.service" user
        systemctl --user daemon-reload
        systemctl --user enable "$SERVICE_NAME" >/dev/null 2>&1 || true
        MODE="systemd-user"
        ui_ok "пользовательская служба systemd «${SERVICE_NAME}» включена"
        if loginctl enable-linger "$RUN_USER" >/dev/null 2>&1; then
            ui_ok "бот будет работать и без входа в систему"
        else
            ui_warn "не удалось включить linger: бот может останавливаться при выходе"
            ui_note "попросите администратора: sudo loginctl enable-linger $RUN_USER"
        fi
    fi
else
    MODE="plain"
    ui_ok "systemd недоступен — присмотр за процессом беру на себя"
    ui_note "сторож поднимет бота, если тот упадёт"
    if command -v crontab >/dev/null 2>&1; then
        CRON_LINE="@reboot cd $PROJECT_DIR && ./manage.sh start >/dev/null 2>&1"
        if crontab -l 2>/dev/null | grep -Fq "$PROJECT_DIR && ./manage.sh start"; then
            ui_ok "запуск после перезагрузки уже прописан в cron"
        elif { crontab -l 2>/dev/null; printf '%s\n' "$CRON_LINE"; } | crontab - 2>/dev/null; then
            ui_ok "запуск после перезагрузки добавлен в cron"
        else
            ui_warn "cron не принял задание — после перезагрузки: ./manage.sh start"
        fi
    else
        ui_warn "cron не найден — после перезагрузки: ./manage.sh start"
    fi
fi

printf '%s\n' "$MODE" > "$PROJECT_DIR/data/runmode"

# ------------------------------------------------------------------ 6. запуск

step "Запускаю бота"

chmod +x "$PROJECT_DIR/manage.sh" "$PROJECT_DIR/scripts/run_forever.sh" 2>/dev/null || true
"$PROJECT_DIR/manage.sh" restart --quiet || true

# Ждём и проверяем ещё раз: нельзя отчитываться «готово», если бот сразу упал
sleep 4
if ! "$PROJECT_DIR/manage.sh" is-running; then
    ui_blank
    ui_fail "бот не удержался после запуска"
    ui_note "последние строки лога:"
    ui_blank
    if [ -f "$PROJECT_DIR/logs/bot.log" ]; then
        tail -n 12 "$PROJECT_DIR/logs/bot.log" | sed 's/^/         /'
    else
        "$PROJECT_DIR/manage.sh" status 2>&1 | tail -n 10 | sed 's/^/         /' || true
    fi
    ui_blank
    ui_note "чаще всего дело в токене — проверьте его у @BotFather"
    ui_note "после правки .env запустите: ./manage.sh start"
    ui_blank
    exit 1
fi
ui_ok "бот работает в фоне"

# ------------------------------------------------------------------ итог

ui_blank
ui_rule
ui_blank
ui_head "✅  Готово"
ui_blank
if [ -n "$BOT_USERNAME" ]; then
    ui_text "Бот @${BOT_USERNAME} запущен и работает круглосуточно."
else
    ui_text "Бот запущен и работает круглосуточно."
fi
ui_blank
ui_text "Что дальше"
if [ -n "$BOT_USERNAME" ]; then
    ui_sub "1. Откройте https://t.me/${BOT_USERNAME}"
else
    ui_sub "1. Откройте своего бота в Telegram"
fi
ui_sub "2. Отправьте /start и заполните анкету"
ui_sub "3. Команда /admin откроет админ-панель — вы владелец"
ui_blank
ui_text "Управление"
ui_cmd "./manage.sh status" "состояние и статистика"
ui_cmd "./manage.sh logs" "живой лог"
ui_cmd "./manage.sh restart" "перезапуск"
ui_cmd "./manage.sh stop" "остановить"
ui_cmd "./manage.sh update" "обновить и перезапустить"
ui_blank
ui_rule
ui_blank
