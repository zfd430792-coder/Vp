"""Загрузка пользователя, обновление активности и проверка ограничений."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

from app import texts
from app.constants import ROLE_MODERATOR
from app.db import Database
from app.keyboards import inline
from app.services import users as users_service
from app.states import Appeal
from app.utils.text import esc, human_delta
from app.utils.time import fmt_dt

TOUCH_INTERVAL = 90  # как часто обновлять last_active_at, секунд
BAN_NOTICE_COOLDOWN = 60


class UserMiddleware(BaseMiddleware):
    """Кладёт в ``data['user']`` запись пользователя и отсекает заблокированных."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._touched: dict[int, float] = {}
        self._ban_notice: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None or tg_user.is_bot:
            return await handler(event, data)

        user = await users_service.ensure(
            self.db,
            tg_user.id,
            username=tg_user.username,
            tg_name=tg_user.full_name,
        )

        moment = time.monotonic()
        if moment - self._touched.get(tg_user.id, 0.0) > TOUCH_INTERVAL:
            self._touched[tg_user.id] = moment
            await users_service.touch(self.db, tg_user.id)
            user["last_active_at"] = int(time.time())
            user["bot_blocked"] = 0

        data["user"] = user
        data["is_staff"] = int(user.get("role") or 0) >= ROLE_MODERATOR

        if users_service.is_banned(user) and not await self._appeal_allowed(event, data):
            await self._notify_ban(event, user)
            return None

        if len(self._touched) > 10_000:
            self._touched = {
                key: value for key, value in self._touched.items() if moment - value < 3600
            }
        return await handler(event, data)

    async def _appeal_allowed(self, event: TelegramObject, data: dict[str, Any]) -> bool:
        """Заблокированный пользователь может только написать апелляцию."""
        if isinstance(event, CallbackQuery) and event.data and event.data.startswith("st:appeal"):
            return True
        state: FSMContext | None = data.get("state")
        if state is not None and isinstance(event, Message):
            current = await state.get_state()
            if current == Appeal.text.state:
                return True
        return False

    async def _notify_ban(self, event: TelegramObject, user: dict[str, Any]) -> None:
        moment = time.monotonic()
        user_id = int(user["id"])
        if moment - self._ban_notice.get(user_id, 0.0) < BAN_NOTICE_COOLDOWN:
            if isinstance(event, CallbackQuery):
                await event.answer("Доступ ограничен", show_alert=False)
            return
        self._ban_notice[user_id] = moment

        left = users_service.ban_left(user)
        if left < 0:
            until_line = texts.BAN_PERMANENT_LINE
        else:
            until_line = texts.BAN_UNTIL_LINE.format(
                date=fmt_dt(user.get("ban_until")), left=human_delta(left)
            )
        text = texts.BANNED.format(
            reason=esc(user.get("ban_reason") or "нарушение правил"),
            until=until_line,
        )
        markup = inline.appeal_button()
        # Уведомление не критично: если Telegram его не примет, просто идём дальше
        if isinstance(event, CallbackQuery):
            await event.answer("Доступ ограничен", show_alert=False)
            if event.message is not None:
                with suppress(Exception):
                    await event.message.answer(text, reply_markup=markup)
        elif isinstance(event, Message):
            with suppress(Exception):
                await event.answer(text, reply_markup=markup)
