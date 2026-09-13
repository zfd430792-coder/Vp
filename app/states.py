"""Состояния диалогов (FSM)."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Reg(StatesGroup):
    """Создание анкеты."""

    captcha = State()
    name = State()
    age = State()
    gender = State()
    seeking = State()
    city = State()
    bio = State()
    photos = State()
    preview = State()


class Edit(StatesGroup):
    """Изменение отдельных полей анкеты."""

    name = State()
    age = State()
    city = State()
    bio = State()
    photo = State()
    age_filter = State()


class Like(StatesGroup):
    note = State()


class Verify(StatesGroup):
    selfie = State()


class Report(StatesGroup):
    comment = State()


class Appeal(StatesGroup):
    text = State()


class Admin(StatesGroup):
    find_user = State()
    broadcast = State()
    broadcast_confirm = State()
    setting_value = State()
    appeal_reply = State()
    warn_reason = State()
    ban_reason = State()
    staff_add = State()
