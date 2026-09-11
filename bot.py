"""Точка входа: запуск бота знакомств."""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent

from app.config import ConfigError, load_config
from app.db import Database
from app.handlers import build_router
from app.middlewares import ThrottlingMiddleware, UserMiddleware
from app.services import users as users_service
from app.services.scheduler import Maintenance
from app.services.settings import Settings

log = logging.getLogger("bot")

COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="feed", description="Смотреть анкеты"),
    BotCommand(command="likes", description="Кто меня лайкнул"),
    BotCommand(command="matches", description="Взаимные симпатии"),
    BotCommand(command="profile", description="Моя анкета"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="safety", description="Памятка по безопасности"),
    BotCommand(command="help", description="Как всё работает"),
    BotCommand(command="stop", description="Скрыть анкету из поиска"),
    BotCommand(command="delete", description="Удалить анкету"),
]


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


async def main() -> None:
    try:
        config = load_config()
    except ConfigError as error:
        print(f"\n❌ {error}\n", file=sys.stderr)
        raise SystemExit(1) from error

    setup_logging(config.log_level)

    db = Database(config.db_path)
    await db.connect()
    log.info("База данных готова: %s", config.db_path)

    settings = Settings(db)
    await settings.load()
    await users_service.sync_owners(db, config.owner_ids)

    bot = Bot(
        token=config.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["db"] = db
    dispatcher["settings"] = settings
    dispatcher["config"] = config

    throttling = ThrottlingMiddleware()
    user_middleware = UserMiddleware(db)
    for observer in (dispatcher.message, dispatcher.callback_query):
        observer.outer_middleware(throttling)
        observer.outer_middleware(user_middleware)

    dispatcher.include_router(build_router())

    @dispatcher.errors()
    async def on_error(event: ErrorEvent) -> bool:
        log.exception("Необработанная ошибка: %s", event.exception)
        return True

    maintenance = Maintenance(bot, db, settings, config)

    try:
        me = await bot.get_me()
        log.info("Запускаюсь как @%s (id=%s)", me.username, me.id)
        await bot.set_my_commands(COMMANDS)
        await bot.delete_webhook(drop_pending_updates=True)
        maintenance.start(config.tasks_interval)
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await maintenance.stop()
        await bot.session.close()
        await db.close()
        log.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
