"""Мелкие помощники для клавиатур."""
from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def chunked(items: Sequence[T], size: int) -> list[list[T]]:
    """Разбивает список на строки кнопок."""
    size = max(1, size)
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def page_bounds(total: int, page: int, per_page: int) -> tuple[int, int, int]:
    """Возвращает (номер страницы, всего страниц, смещение)."""
    per_page = max(1, per_page)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    return page, pages, page * per_page
