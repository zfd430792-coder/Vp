"""Обычные (нижние) клавиатуры."""
from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from app.texts import (
    BTN_ADMIN,
    BTN_CHAT_EXIT,
    BTN_FEED,
    BTN_LIKES,
    BTN_MATCHES,
    BTN_PROFILE,
    BTN_SAFETY,
    BTN_SETTINGS,
)

remove = ReplyKeyboardRemove()


def main_menu(*, is_staff: bool = False, likes_badge: int = 0, unread: int = 0) -> ReplyKeyboardMarkup:
    likes_btn = BTN_LIKES + (f" ({likes_badge})" if likes_badge else "")
    matches_btn = BTN_MATCHES + (f" ({unread})" if unread else "")
    rows = [
        [KeyboardButton(text=BTN_FEED)],
        [KeyboardButton(text=likes_btn), KeyboardButton(text=matches_btn)],
        [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_SETTINGS)],
        [KeyboardButton(text=BTN_SAFETY)],
    ]
    if is_staff:
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def chat_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CHAT_EXIT)]],
        resize_keyboard=True,
    )


def cancel(text: str = "❌ Отмена") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=text)]], resize_keyboard=True)


def skip_or_cancel(skip_text: str = "⏭ Пропустить") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=skip_text)], [KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )
