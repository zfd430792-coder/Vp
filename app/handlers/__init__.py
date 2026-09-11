"""Хендлеры бота. Порядок регистрации важен: общий перехватчик идёт последним."""
from __future__ import annotations

from aiogram import Router

from app.handlers import (
    admin,
    chat,
    common,
    feed,
    likes,
    matches,
    profile,
    registration,
    reports,
    settings,
)


def build_router() -> Router:
    root = Router(name="root")
    root.include_router(common.router)
    root.include_router(registration.router)
    root.include_router(admin.router)
    root.include_router(profile.router)
    root.include_router(settings.router)
    root.include_router(feed.router)
    root.include_router(likes.router)
    root.include_router(matches.router)
    root.include_router(reports.router)
    # Пересылка сообщений в диалоге ловит всё подряд, поэтому регистрируется последней
    root.include_router(chat.router)
    return root
