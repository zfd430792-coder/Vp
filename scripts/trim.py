"""Обрезает строки по символам для вывода в терминал.

Нужен, потому что `cut -c` и `awk substr` в части систем считают байты и рвут
кириллицу посередине символа. Заодно выбрасываем пустые строки, чтобы хвост лога
читался компактно.

Использование: tail -n 20 bot.log | python scripts/trim.py 66
"""
from __future__ import annotations

import sys
from contextlib import suppress


def main() -> None:
    width = 66
    if len(sys.argv) > 1:
        with suppress(ValueError):
            width = max(8, int(sys.argv[1]))

    for raw in sys.stdin:
        line = raw.rstrip()
        if not line:
            continue
        print(line if len(line) <= width else line[: width - 1] + "…")


if __name__ == "__main__":
    main()
