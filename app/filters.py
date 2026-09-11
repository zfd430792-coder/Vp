"""Свои фильтры."""
from __future__ import annotations

from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from app.constants import ROLE_MODERATOR


class IsStaff(BaseFilter):
    """Пропускает только модераторов и выше."""

    def __init__(self, min_role: int = ROLE_MODERATOR) -> None:
        self.min_role = min_role

    async def __call__(self, event: TelegramObject, user: dict[str, Any] | None = None) -> bool:
        if not user:
            return False
        return int(user.get("role") or 0) >= self.min_role
