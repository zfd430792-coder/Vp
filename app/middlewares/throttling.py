"""Ограничение частоты обращений — защита от флуда и автокликеров."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.texts import TOO_FAST


class ThrottlingMiddleware(BaseMiddleware):
    """Не больше ``limit`` событий за ``window`` секунд от одного человека."""

    def __init__(self, limit: int = 8, window: float = 3.0, notice_cooldown: float = 5.0) -> None:
        self.limit = limit
        self.window = window
        self.notice_cooldown = notice_cooldown
        self._events: dict[int, deque[float]] = defaultdict(deque)
        self._notified: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        moment = time.monotonic()
        bucket = self._events[user.id]
        bucket.append(moment)
        while bucket and moment - bucket[0] > self.window:
            bucket.popleft()

        if len(bucket) > self.limit:
            last_notice = self._notified.get(user.id, 0.0)
            if moment - last_notice > self.notice_cooldown:
                self._notified[user.id] = moment
                if isinstance(event, CallbackQuery):
                    await event.answer(TOO_FAST, show_alert=False)
                elif isinstance(event, Message):
                    await event.answer(TOO_FAST)
            return None

        if len(self._events) > 10_000:
            self._prune(moment)
        return await handler(event, data)

    def _prune(self, moment: float) -> None:
        stale = [
            user_id
            for user_id, bucket in self._events.items()
            if not bucket or moment - bucket[-1] > 60
        ]
        for user_id in stale:
            self._events.pop(user_id, None)
            self._notified.pop(user_id, None)
