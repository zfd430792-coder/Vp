"""Мини-эмулятор Telegram: гоняем настоящие хендлеры без сети."""
from __future__ import annotations

import itertools
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import TelegramMethod
from aiogram.types import (
    CallbackQuery,
    Chat,
    Location,
    Message,
    MessageId,
    PhotoSize,
    Update,
    User,
)

from app.config import Config
from app.db import Database
from app.handlers import build_router
from app.middlewares import ThrottlingMiddleware, UserMiddleware
from app.services.settings import Settings

BOT_ID = 777000
BOT_USER = User(id=BOT_ID, is_bot=True, first_name="DatingBot", username="dating_bot")


class MockSession(BaseSession):
    """Запоминает вызовы Bot API и возвращает правдоподобные ответы."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.documents: list[tuple[str, str]] = []
        self.sent_texts: dict[int, str] = {}
        self._ids = itertools.count(1000)

    async def close(self) -> None:  # pragma: no cover - заглушка
        return None

    async def stream_content(  # pragma: no cover - заглушка
        self, url: str, headers: Any = None, timeout: int = 30, chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        yield b""

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None):
        name = type(method).__name__
        payload = method.model_dump(exclude_none=True)
        self.calls.append((name, payload))

        chat_id = payload.get("chat_id", 0)
        if isinstance(chat_id, str):
            chat_id = 0

        if name == "SendDocument":
            document = payload.get("document")
            data = getattr(document, "data", None)
            self.documents.append(
                (
                    getattr(document, "filename", "file"),
                    data.decode("utf-8", "replace") if isinstance(data, bytes) else "",
                )
            )
            return self._message(int(chat_id), payload.get("caption"))
        if name in {"SendMessage", "SendPhoto", "SendVideo", "SendVoice", "SendVideoNote",
                    "SendAnimation", "EditMessageText", "EditMessageCaption"}:
            return self._message(int(chat_id), payload.get("text") or payload.get("caption"))
        if name == "SendMediaGroup":
            return [self._message(int(chat_id), None) for _ in payload.get("media", [1])]
        if name == "CopyMessage":
            return MessageId(message_id=next(self._ids))
        if name == "GetMe":
            return BOT_USER
        if name == "EditMessageReplyMarkup":
            return self._message(int(chat_id), None)
        return True

    def _message(self, chat_id: int, text: str | None) -> Message:
        message_id = next(self._ids)
        # Запоминаем текст: по нему потом видно, какое именно сообщение бот удалил
        self.sent_texts[message_id] = text or ""
        return Message(
            message_id=message_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id, type="private"),
            from_user=BOT_USER,
            text=text,
        )

    def deleted_texts(self, chat_id: int | None = None) -> list[str]:
        """Тексты сообщений, которые бот удалил с момента clear()."""
        found: list[str] = []
        for name, payload in self.calls:
            if name not in {"DeleteMessage", "DeleteMessages"}:
                continue
            if chat_id is not None and payload.get("chat_id") != chat_id:
                continue
            ids = payload.get("message_ids") or [payload.get("message_id")]
            found.extend(self.sent_texts.get(int(mid), "") for mid in ids if mid)
        return found

    # --- удобные выборки для проверок -----------------------------------------
    def texts(self, chat_id: int | None = None) -> list[str]:
        result = []
        for name, payload in self.calls:
            if name not in {"SendMessage", "EditMessageText", "SendPhoto", "EditMessageCaption"}:
                continue
            if chat_id is not None and payload.get("chat_id") != chat_id:
                continue
            body = payload.get("text") or payload.get("caption") or ""
            result.append(body)
        return result

    def last_text(self, chat_id: int | None = None) -> str:
        found = self.texts(chat_id)
        return found[-1] if found else ""

    def buttons(self, chat_id: int | None = None) -> list[str]:
        """Все callback_data последней клавиатуры."""
        for _name, payload in reversed(self.calls):
            if chat_id is not None and payload.get("chat_id") != chat_id:
                continue
            markup = payload.get("reply_markup")
            if not markup or "inline_keyboard" not in markup:
                continue
            return [
                button.get("callback_data")
                for row in markup["inline_keyboard"]
                for button in row
                if button.get("callback_data")
            ]
        return []

    def all_buttons(self, chat_id: int | None = None) -> list[str]:
        """Все callback_data из всех клавиатур, отправленных с момента clear()."""
        found: list[str] = []
        for _name, payload in self.calls:
            if chat_id is not None and payload.get("chat_id") != chat_id:
                continue
            markup = payload.get("reply_markup")
            if not markup or "inline_keyboard" not in markup:
                continue
            found.extend(
                button["callback_data"]
                for row in markup["inline_keyboard"]
                for button in row
                if button.get("callback_data")
            )
        return found

    def button_labels(self, chat_id: int | None = None) -> list[str]:
        """Подписи всех inline-кнопок с момента clear()."""
        found: list[str] = []
        for _name, payload in self.calls:
            if chat_id is not None and payload.get("chat_id") != chat_id:
                continue
            markup = payload.get("reply_markup")
            if not markup or "inline_keyboard" not in markup:
                continue
            found.extend(
                button.get("text", "")
                for row in markup["inline_keyboard"]
                for button in row
            )
        return found

    def alerts(self) -> list[str]:
        """Тексты всплывающих ответов на нажатия кнопок."""
        return [
            payload.get("text", "")
            for name, payload in self.calls
            if name == "AnswerCallbackQuery" and payload.get("text")
        ]

    def methods(self) -> list[str]:
        return [name for name, _ in self.calls]

    def clear(self) -> None:
        self.calls.clear()
        self.documents.clear()


class Harness:
    def __init__(self, db: Database, settings: Settings, config: Config) -> None:
        self.session = MockSession()
        self.bot = Bot(
            token="123456789:AABBCCDDEEFFgghhiijjkkllmmnnooppqq",
            session=self.session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp["db"] = db
        self.dp["settings"] = settings
        self.dp["config"] = config
        throttling = ThrottlingMiddleware(limit=1000)
        user_mw = UserMiddleware(db)
        for observer in (self.dp.message, self.dp.callback_query):
            observer.outer_middleware(throttling)
            observer.outer_middleware(user_mw)
        self.dp.include_router(build_router())
        self._update_id = itertools.count(1)
        self._message_id = itertools.count(1)

    def _user(self, user_id: int, username: str | None = None) -> User:
        return User(
            id=user_id,
            is_bot=False,
            first_name=f"User{user_id}",
            username=username or f"user{user_id}",
        )

    async def send(
        self, user_id: int, text: str, *, photo: str | None = None
    ) -> MockSession:
        message = Message(
            message_id=next(self._message_id),
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type="private"),
            from_user=self._user(user_id),
            text=None if photo else text,
            caption=text if photo else None,
            photo=(
                [PhotoSize(file_id=photo, file_unique_id=f"u_{photo}", width=100, height=100)]
                if photo
                else None
            ),
        )
        await self.dp.feed_update(
            self.bot, Update(update_id=next(self._update_id), message=message)
        )
        return self.session

    async def send_location(self, user_id: int, lat: float, lon: float) -> MockSession:
        message = Message(
            message_id=next(self._message_id),
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type="private"),
            from_user=self._user(user_id),
            location=Location(latitude=lat, longitude=lon),
        )
        await self.dp.feed_update(
            self.bot, Update(update_id=next(self._update_id), message=message)
        )
        return self.session

    async def click(self, user_id: int, data: str, *, message_text: str = "карточка") -> MockSession:
        message = Message(
            message_id=next(self._message_id),
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type="private"),
            from_user=BOT_USER,
            text=message_text,
        )
        # Сообщение под кнопкой тоже «отправлено ботом»: так проверки видят,
        # какой именно текст бот потом удалил
        self.session.sent_texts[message.message_id] = message_text
        query = CallbackQuery(
            id=str(next(self._update_id)),
            from_user=self._user(user_id),
            chat_instance="ci",
            message=message,
            data=data,
        )
        await self.dp.feed_update(
            self.bot, Update(update_id=next(self._update_id), callback_query=query)
        )
        return self.session

    async def set_state_data(self, user_id: int, **data: Any) -> None:
        """Правит данные диалога напрямую — нужно для проверки таймеров."""
        from aiogram.fsm.storage.base import StorageKey

        key = StorageKey(bot_id=self.bot.id, chat_id=user_id, user_id=user_id)
        await self.dp.fsm.storage.update_data(key=key, data=data)

    def restart(self) -> None:
        """Имитирует перезапуск бота: вся память процесса теряется, база остаётся.

        Роутеры aiogram — объекты уровня модуля и подключаются только к одному
        диспетчеру, поэтому вместо второго Harness обнуляем то, что живёт в памяти.
        """
        from app.services import chat as chat_service

        self.dp.fsm.storage = MemoryStorage()
        chat_service._headers.clear()

    async def close(self) -> None:
        await self.bot.session.close()
