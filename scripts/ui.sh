#!/usr/bin/env bash
#
# Общее оформление для install.sh и manage.sh.
#
# Намеренно не рисуем рамок с правой границей: эмодзи и кириллица занимают разное
# число колонок в разных терминалах, и такие рамки почти всегда разъезжаются.
# Вместо них — горизонтальные линии и ровный отступ слева.

if [ -t 1 ] && command -v tput >/dev/null 2>&1 && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
    UI_B="$(tput bold)"; UI_D="$(tput dim)"; UI_R="$(tput sgr0)"
    UI_RED="$(tput setaf 1)"; UI_GREEN="$(tput setaf 2)"; UI_YELLOW="$(tput setaf 3)"
    UI_BLUE="$(tput setaf 4)"; UI_CYAN="$(tput setaf 6)"
else
    UI_B=""; UI_D=""; UI_R=""
    UI_RED=""; UI_GREEN=""; UI_YELLOW=""; UI_BLUE=""; UI_CYAN=""
fi

UI_WIDTH=54
if command -v tput >/dev/null 2>&1; then
    _ui_cols="$(tput cols 2>/dev/null || echo 80)"
    if [ "$_ui_cols" -lt 58 ] 2>/dev/null; then
        UI_WIDTH=$(( _ui_cols > 24 ? _ui_cols - 4 : 20 ))
    fi
fi

UI_LINE=""
_ui_i=0
while [ "$_ui_i" -lt "$UI_WIDTH" ]; do
    UI_LINE="${UI_LINE}─"
    _ui_i=$((_ui_i + 1))
done

ui_blank() { printf '\n'; }
ui_rule()  { printf '  %s%s%s\n' "$UI_D" "$UI_LINE" "$UI_R"; }

# Крупный заголовок раздела: «💛  БОТ ЗНАКОМСТВ»
ui_head() { printf '  %s%s%s\n' "$UI_B" "$1" "$UI_R"; }

# Обычный и приглушённый текст с отступом в четыре пробела
ui_text() { printf '     %s\n' "$1"; }
ui_dim()  { printf '     %s%s%s\n' "$UI_D" "$1" "$UI_R"; }

# Шаг установки: [2/6]  Окружение и зависимости
ui_step() {
    printf '\n  %s[%s]%s  %s%s%s\n' "$UI_BLUE" "$1" "$UI_R" "$UI_B" "$2" "$UI_R"
}

ui_ok()   { printf '         %s✔%s %s\n' "$UI_GREEN" "$UI_R" "$1"; }
ui_note() { printf '         %s%s%s\n' "$UI_D" "$1" "$UI_R"; }
ui_warn() { printf '         %s!%s %s\n' "$UI_YELLOW" "$UI_R" "$1"; }
ui_fail() { printf '         %s✘%s %s%s%s\n' "$UI_RED" "$UI_R" "$UI_B" "$1" "$UI_R"; }

# Пункт списка и подпункт
ui_item() { printf '     %s%s%s %s\n' "$UI_CYAN" "•" "$UI_R" "$1"; }
ui_sub()  { printf '       %s%s%s\n' "$UI_D" "$1" "$UI_R"; }

# Строка вида «команда — описание» с ровными столбцами
ui_cmd() { printf '       %s%-22s%s %s%s%s\n' "$UI_B" "$1" "$UI_R" "$UI_D" "$2" "$UI_R"; }
