#!/usr/bin/env bash
#
# Создание глобальной команды управления ботом.
# Подключается из install.sh и update.sh после scripts/ui.sh.
#
# Обёртка помнит путь к проекту, поэтому команду можно звать из любой папки.

LAUNCHER_PRIMARY="bot"
LAUNCHER_FALLBACK="dating-bot"
LAUNCHER_NAME=""
LAUNCHER_PATH=""
LAUNCHER_ON_PATH=1
LAUNCHER_DIR=""
LAUNCHER_PROFILES=""

launcher_dir() {
    # Под root — общесистемный каталог, иначе личный каталог пользователя
    if [ "$(id -u)" -eq 0 ] && [ -d /usr/local/bin ] && [ -w /usr/local/bin ]; then
        printf '/usr/local/bin'
    else
        printf '%s/.local/bin' "$HOME"
    fi
}

launcher_is_ours() {
    # Свою обёртку узнаём по метке внутри файла
    [ -f "$1" ] && grep -q 'DATING_BOT_LAUNCHER' "$1" 2>/dev/null
}

launcher_write() {
    local target="$1" project="$2"
    cat > "$target" <<LAUNCHER
#!/usr/bin/env bash
# DATING_BOT_LAUNCHER — создано установщиком бота знакомств
BOT_DIR="$project"

if [ ! -f "\$BOT_DIR/manage.sh" ]; then
    printf 'Каталог бота не найден: %s\n' "\$BOT_DIR" >&2
    printf 'Похоже, проект переместили или удалили.\n' >&2
    printf 'Запустите установку заново из нового места: bash install.sh\n' >&2
    exit 1
fi

# Имя команды нужно, чтобы подсказки показывали «bot …», а не «./manage.sh …»
export BOT_CMD="\$(basename "\$0")"
exec bash "\$BOT_DIR/manage.sh" "\$@"
LAUNCHER
    chmod +x "$target"
}

launcher_add_to_path() {
    # Дописываем каталог в профили оболочки, помечая свою строку
    local dir="$1" added=""
    for profile in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
        [ -f "$profile" ] || continue
        if grep -Fq "$dir" "$profile" 2>/dev/null; then
            continue
        fi
        {
            printf '\n# Добавлено установщиком бота знакомств\n'
            printf 'export PATH="%s:$PATH"\n' "$dir"
        } >> "$profile" 2>/dev/null || continue
        added="$added $(basename "$profile")"
    done
    LAUNCHER_PROFILES="$added"
}

install_launcher() {
    # install_launcher каталог_проекта [quiet]
    local project="$1" quiet="${2:-}"
    local existing=""

    LAUNCHER_DIR="$(launcher_dir)"
    if ! mkdir -p "$LAUNCHER_DIR" 2>/dev/null; then
        [ "$quiet" = quiet ] || ui_warn "не удалось создать каталог $LAUNCHER_DIR — глобальной команды не будет"
        [ "$quiet" = quiet ] || ui_note "управляйте ботом из папки проекта: ./manage.sh status"
        return 1
    fi

    LAUNCHER_NAME="$LAUNCHER_PRIMARY"
    existing="$(command -v "$LAUNCHER_PRIMARY" 2>/dev/null || true)"
    if [ -n "$existing" ] && ! launcher_is_ours "$existing"; then
        # Имя занято чужой программой — не перетираем её
        LAUNCHER_NAME="$LAUNCHER_FALLBACK"
        [ "$quiet" = quiet ] || ui_note "имя «$LAUNCHER_PRIMARY» уже занято, беру «$LAUNCHER_FALLBACK»"
    fi

    LAUNCHER_PATH="$LAUNCHER_DIR/$LAUNCHER_NAME"
    if ! launcher_write "$LAUNCHER_PATH" "$project"; then
        LAUNCHER_NAME=""
        [ "$quiet" = quiet ] || ui_warn "записать команду не удалось — управляйте из папки проекта"
        return 1
    fi

    [ "$quiet" = quiet ] || ui_ok "команда «$LAUNCHER_NAME» готова: $LAUNCHER_PATH"

    case ":$PATH:" in
        *":$LAUNCHER_DIR:"*)
            LAUNCHER_ON_PATH=1
            ;;
        *)
            LAUNCHER_ON_PATH=0
            launcher_add_to_path "$LAUNCHER_DIR"
            if [ "$quiet" != quiet ]; then
                if [ -n "$LAUNCHER_PROFILES" ]; then
                    ui_note "каталог добавлен в профиль:$LAUNCHER_PROFILES"
                    ui_note "команда заработает в новом терминале"
                else
                    ui_warn "каталог $LAUNCHER_DIR не в PATH — добавьте его сами"
                fi
            fi
            ;;
    esac
    return 0
}
