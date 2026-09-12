#!/usr/bin/env bash
#
# Установка бота знакомств одной командой:
#   bash install.sh
#
# Скрипт создаст окружение, спросит токен и ваш Telegram ID, настроит автозапуск
# и сразу поднимет бота в фоне. Запускать можно повторно — настройки сохранятся.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

VENV="$PROJECT_DIR/.venv"
PY="$VENV/bin/python"
ENV_FILE="$PROJECT_DIR/.env"
SERVICE_NAME="dating-bot"
MIN_PY_MAJOR=3
MIN_PY_MINOR=10
STEPS=6

# ------------------------------------------------------------------ оформление

if [ -t 1 ] && command -v tput >/dev/null 2>&1 && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
    B="$(tput bold)"; D="$(tput dim)"; R="$(tput sgr0)"
    RED="$(tput setaf 1)"; GREEN="$(tput setaf 2)"; YELLOW="$(tput setaf 3)"
    BLUE="$(tput setaf 4)"; CYAN="$(tput setaf 6)"
else
    B=""; D=""; R=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; CYAN=""
fi

step_no=0

banner() {
    printf '\n'
    printf '%s╭──────────────────────────────────────────────────╮%s\n' "$CYAN" "$R"
    printf '%s│%s   💛  %sБот знакомств%s — установка                  %s│%s\n' "$CYAN" "$R" "$B" "$R" "$CYAN" "$R"
    printf '%s╰──────────────────────────────────────────────────╯%s\n' "$CYAN" "$R"
    printf '\n'
}

step() {
    step_no=$((step_no + 1))
    printf '%s[%d/%d]%s %s%s%s\n' "$BLUE" "$step_no" "$STEPS" "$R" "$B" "$1" "$R"
}

ok()   { printf '      %s✔%s %s\n' "$GREEN" "$R" "$1"; }
info() { printf '      %s%s%s\n' "$D" "$1" "$R"; }
warn() { printf '      %s!%s %s\n' "$YELLOW" "$R" "$1"; }

die() {
    printf '\n      %s✘%s %s%s%s\n\n' "$RED" "$R" "$B" "$1" "$R"
    shift || true
    while [ "$#" -gt 0 ]; do
        printf '        %s%s%s\n' "$D" "$1" "$R"
        shift
    done
    printf '\n'
    exit 1
}

# ------------------------------------------------------------------ ввод с клавиатуры

# Ввод читаем из терминала, если он есть. Значение кладём в ANSWER, а не в stdout:
# так приглашение никогда не попадёт в подставляемую переменную.
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
    # ask "приглашение" -> результат в $ANSWER, 1 если ввода нет
    ANSWER=""
    [ -z "$TTY_IN" ] && return 1
    printf '      %s%s%s' "$B" "$1" "$R"
    if ! IFS= read -r ANSWER < "$TTY_IN" 2>/dev/null; then
        printf '\n'
        ANSWER=""
        return 1
    fi
    ANSWER="$(printf '%s' "$ANSWER" | tr -d ' \r\n')"
    return 0
}

ask_required() {
    # ask_required "приглашение" "проверка" "подсказка при ошибке"
    local prompt="$1" validator="$2" hint="$3" attempt=0
    while [ "$attempt" -lt 5 ]; do
        attempt=$((attempt + 1))
        if ! ask "$prompt"; then
            die "Не получается прочитать ответ из терминала" \
                "Запустите установку напрямую: bash install.sh" \
                "Или задайте значения заранее:" \
                "BOT_TOKEN=... OWNER_IDS=... bash install.sh"
        fi
        if [ -n "$ANSWER" ] && "$validator" "$ANSWER"; then
            return 0
        fi
        warn "$hint"
    done
    die "Слишком много неудачных попыток" "Запустите установку снова, когда данные будут под рукой"
}

confirm() {
    [ -z "$TTY_IN" ] && return 1
    printf '      %s%s%s [y/N] ' "$B" "$1" "$R"
    local answer=""
    IFS= read -r answer < "$TTY_IN" 2>/dev/null || answer=""
    case "$answer" in
        [yYдД]*) return 0 ;;
        *) return 1 ;;
    esac
}

banner

# ------------------------------------------------------------------ 1. Python

step "Проверяю Python"

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c "import sys; raise SystemExit(0 if sys.version_info >= ($MIN_PY_MAJOR, $MIN_PY_MINOR) else 1)" 2>/dev/null; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    die "Нужен Python ${MIN_PY_MAJOR}.${MIN_PY_MINOR} или новее" \
        "Ubuntu/Debian:  sudo apt update && sudo apt install -y python3 python3-venv" \
        "Fedora:         sudo dnf install -y python3" \
        "macOS:          brew install python@3.12"
fi
ok "$("$PYTHON_BIN" --version 2>&1) — $(command -v "$PYTHON_BIN")"

# ------------------------------------------------------------------ 2. окружение

step "Создаю окружение и ставлю зависимости"

if [ ! -x "$PY" ]; then
    if ! "$PYTHON_BIN" -m venv "$VENV" 2>/dev/null; then
        die "Не удалось создать виртуальное окружение" \
            "Скорее всего не хватает пакета venv:" \
            "sudo apt install -y python3-venv"
    fi
    ok "окружение создано: .venv"
else
    ok "окружение уже есть: .venv"
fi

"$PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
if ! "$PY" -m pip install --quiet -r "$PROJECT_DIR/requirements.txt"; then
    die "Не удалось установить зависимости" \
        "Проверьте интернет и попробуйте снова:" \
        "$PY -m pip install -r requirements.txt"
fi
ok "$("$PY" - <<'PYCODE'
import aiogram, aiosqlite
print(f"aiogram {aiogram.__version__}, aiosqlite {aiosqlite.__version__}")
PYCODE
)"

mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/logs"

# ------------------------------------------------------------------ 3. настройки

step "Настройка бота"

read_env_value() {
    # read_env_value КЛЮЧ — достаёт значение из существующего .env
    [ -f "$ENV_FILE" ] || return 0
    sed -n "s/^$1=//p" "$ENV_FILE" | head -n 1 | tr -d '"'"'"'\r'
}

TOKEN="${BOT_TOKEN:-}"
ADMINS="${OWNER_IDS:-}"

OLD_TOKEN="$(read_env_value BOT_TOKEN || true)"
OLD_ADMINS="$(read_env_value OWNER_IDS || true)"

if [ -z "$TOKEN" ] && [ -n "$OLD_TOKEN" ]; then
    info "В .env уже есть настройки (бот ...${OLD_TOKEN: -6})"
    if confirm "Оставить их?"; then
        TOKEN="$OLD_TOKEN"
        ADMINS="$OLD_ADMINS"
    fi
fi

valid_token() {
    printf '%s' "$1" | grep -Eq '^[0-9]{6,}:[A-Za-z0-9_-]{30,}$'
}

valid_admins() {
    printf '%s' "$1" | grep -Eq '^[0-9]{5,15}(,[0-9]{5,15})*$'
}

if [ -z "$TOKEN" ]; then
    printf '\n'
    info "Токен бота берётся у @BotFather: /newbot → название → @имя_бота"
    ask_required 'Токен бота: ' valid_token \
        'Непохоже на токен. Ожидается вид 1234567890:AA... (без пробелов)'
    TOKEN="$ANSWER"
    ok "токен принят"
fi

if [ -z "$ADMINS" ]; then
    printf '\n'
    info "Ваш Telegram ID узнаёт @userinfobot — он пришлёт число в ответ на /start"
    info "Владелец получает полный доступ к админ-панели бота"
    ask_required 'Ваш Telegram ID: ' valid_admins \
        'Нужно число, например 123456789. Несколько владельцев — через запятую'
    ADMINS="$ANSWER"
    ok "владелец: $ADMINS"
fi

printf '\n'
MOD_CHAT="${MODERATION_CHAT_ID:-$(read_env_value MODERATION_CHAT_ID || true)}"
if [ -z "$MOD_CHAT" ] && [ -n "$TTY_IN" ]; then
    info "Можно указать чат для уведомлений модерации (необязательно, Enter — пропустить)"
    if ask 'ID чата модерации: '; then
        MOD_CHAT="$ANSWER"
    fi
    if [ -n "$MOD_CHAT" ] && ! printf '%s' "$MOD_CHAT" | grep -Eq '^-?[0-9]{5,20}$'; then
        warn "Непохоже на ID чата — пропускаю"
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
except Exception as error:  # сеть недоступна, прокси, DNS
    print("NET", type(error).__name__)
PYCODE
)"

case "$CHECK_OUTPUT" in
    OK*)
        BOT_USERNAME="$(printf '%s' "$CHECK_OUTPUT" | awk '{print $2}')"
        BOT_TITLE="$(printf '%s' "$CHECK_OUTPUT" | cut -d' ' -f3-)"
        ok "бот найден: @${BOT_USERNAME} (${BOT_TITLE})"
        ;;
    BAD*)
        warn "Telegram не принял токен (ошибка $(printf '%s' "$CHECK_OUTPUT" | awk '{print $2}'))"
        if ! confirm "Продолжить всё равно?"; then
            die "Установка остановлена" "Возьмите новый токен у @BotFather и запустите снова"
        fi
        ;;
    *)
        warn "Не удалось связаться с Telegram — проверю позже при запуске"
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
ok ".env сохранён (доступ только владельцу файла)"

RUN_USER="$(id -un)"
MODE="plain"

systemd_available() {
    command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]
}

write_unit() {
    # write_unit путь [user]
    #
    # Restart=on-failure + RestartPreventExitStatus=2: сбои сети лечим перезапуском,
    # а ошибку настройки — нет, иначе бот будет биться в цикле каждые пять секунд.
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

if systemd_available; then
    if [ "$(id -u)" -eq 0 ]; then
        write_unit "/etc/systemd/system/${SERVICE_NAME}.service"
        systemctl daemon-reload
        systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
        MODE="systemd-system"
        ok "systemd: служба ${SERVICE_NAME} включена, поднимется после перезагрузки"
    else
        mkdir -p "$HOME/.config/systemd/user"
        write_unit "$HOME/.config/systemd/user/${SERVICE_NAME}.service" user
        systemctl --user daemon-reload
        systemctl --user enable "$SERVICE_NAME" >/dev/null 2>&1 || true
        MODE="systemd-user"
        ok "systemd: пользовательская служба ${SERVICE_NAME} включена"
        if loginctl enable-linger "$RUN_USER" >/dev/null 2>&1; then
            ok "бот будет работать даже без входа в систему"
        else
            warn "Не удалось включить linger: бот может останавливаться при выходе из системы"
            info "Попросите администратора выполнить: sudo loginctl enable-linger $RUN_USER"
        fi
    fi
else
    MODE="plain"
    ok "systemd недоступен — беру присмотр за процессом на себя"
    info "Отдельный сторож перезапустит бота, если он упадёт"
    if command -v crontab >/dev/null 2>&1; then
        CRON_LINE="@reboot cd $PROJECT_DIR && ./manage.sh start >/dev/null 2>&1"
        if crontab -l 2>/dev/null | grep -Fq "$PROJECT_DIR && ./manage.sh start"; then
            ok "автозапуск после перезагрузки уже прописан в cron"
        elif { crontab -l 2>/dev/null; printf '%s\n' "$CRON_LINE"; } | crontab - 2>/dev/null; then
            ok "автозапуск после перезагрузки добавлен в cron"
        else
            warn "Не удалось добавить задание в cron — после перезагрузки запустите ./manage.sh start"
        fi
    else
        warn "cron не найден — после перезагрузки сервера запустите ./manage.sh start"
    fi
fi

printf '%s\n' "$MODE" > "$PROJECT_DIR/data/runmode"

# ------------------------------------------------------------------ 6. запуск

step "Запускаю бота"

chmod +x "$PROJECT_DIR/manage.sh" "$PROJECT_DIR/scripts/run_forever.sh" 2>/dev/null || true

"$PROJECT_DIR/manage.sh" restart --quiet || true

# Ждём и проверяем ещё раз: важно не отчитаться «работает», если бот сразу упал
sleep 4
if "$PROJECT_DIR/manage.sh" is-running; then
    ok "бот работает"
else
    printf '\n'
    fail_note="Бот не удержался после запуска"
    printf '      %s✘%s %s%s%s\n' "$RED" "$R" "$B" "$fail_note" "$R"
    printf '      %sПоследние строки лога:%s\n' "$D" "$R"
    if [ -f "$PROJECT_DIR/logs/bot.log" ]; then
        tail -n 12 "$PROJECT_DIR/logs/bot.log" | sed 's/^/        /'
    else
        ( "$PROJECT_DIR/manage.sh" status 2>&1 | tail -n 10 | sed 's/^/        /' ) || true
    fi
    printf '\n      %sЧаще всего дело в токене: проверьте его у @BotFather%s\n' "$D" "$R"
    printf '      %sПосле исправления .env запустите: ./manage.sh start%s\n\n' "$D" "$R"
    exit 1
fi

# ------------------------------------------------------------------ итог

printf '\n'
printf '%s╭─ Готово ─────────────────────────────────────────╮%s\n' "$GREEN" "$R"
if [ -n "$BOT_USERNAME" ]; then
    printf '%s│%s  Бот %s@%s%s запущен и работает 24/7%*s%s│%s\n' \
        "$GREEN" "$R" "$B" "$BOT_USERNAME" "$R" \
        $(( 16 - ${#BOT_USERNAME} > 0 ? 16 - ${#BOT_USERNAME} : 1 )) "" "$GREEN" "$R"
else
    printf '%s│%s  Бот запущен и работает 24/7                     %s│%s\n' "$GREEN" "$R" "$GREEN" "$R"
fi
printf '%s╰──────────────────────────────────────────────────╯%s\n' "$GREEN" "$R"
printf '\n'
printf '  %sУправление%s\n' "$B" "$R"
printf '    ./manage.sh status    %sсостояние и статистика%s\n' "$D" "$R"
printf '    ./manage.sh logs      %sживой лог%s\n' "$D" "$R"
printf '    ./manage.sh restart   %sперезапуск%s\n' "$D" "$R"
printf '    ./manage.sh stop      %sостановить%s\n' "$D" "$R"
printf '    ./manage.sh update    %sобновить код и перезапустить%s\n' "$D" "$R"
printf '\n'
printf '  %sЧто дальше%s\n' "$B" "$R"
if [ -n "$BOT_USERNAME" ]; then
    printf '    1. Откройте бота: %shttps://t.me/%s%s\n' "$CYAN" "$BOT_USERNAME" "$R"
else
    printf '    1. Откройте своего бота в Telegram\n'
fi
printf '    2. Отправьте /start и заполните анкету\n'
printf '    3. Команда /admin откроет админ-панель — вы владелец\n'
printf '\n'
