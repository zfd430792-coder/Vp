"""Фоновые задачи: снятие истёкших ограничений и уборка базы."""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot

from app import texts
from app.config import Config
from app.constants import DAY, STATUS_ACTIVE
from app.db import Database
from app.keyboards import inline
from app.services import antifraud, insights, moderation, notify
from app.services import chat as chat_service
from app.services.settings import Settings
from app.utils.text import plural
from app.utils.time import now

log = logging.getLogger(__name__)

SLA_ALERT_INTERVAL = 6 * 3600


class Maintenance:
    """Периодические задачи бота."""

    def __init__(self, bot: Bot, db: Database, settings: Settings, config: Config) -> None:
        self.bot = bot
        self.db = db
        self.settings = settings
        self.config = config
        self._task: asyncio.Task | None = None
        self._last_sla_alert = 0
        self._last_cleanup = 0
        self._last_nudge = 0

    def start(self, interval: int = 300) -> None:
        self._task = asyncio.create_task(self._loop(interval))

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self, interval: int) -> None:
        # Небольшая задержка на старте, чтобы не мешать первому опросу Telegram
        await asyncio.sleep(10)
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - фоновая задача не должна падать
                log.exception("Ошибка в фоновой задаче")
            await asyncio.sleep(interval)

    async def run_once(self) -> None:
        await self.expire_bans()
        await self.expire_shadows()
        await self.check_sla()
        if now() - self._last_nudge > 20 * 3600:
            self._last_nudge = now()
            await self.nudge_inactive()
        if now() - self._last_cleanup > 6 * 3600:
            self._last_cleanup = now()
            await self.cleanup()

    # ------------------------------------------------------------------ ограничения
    async def expire_bans(self) -> None:
        moment = now()
        rows = await self.db.fetchall(
            "SELECT id FROM users WHERE status = 'banned' AND ban_permanent = 0 "
            "AND ban_until IS NOT NULL AND ban_until <= ?",
            (moment,),
        )
        for row in rows:
            user_id = int(row["id"])
            await self.db.execute(
                "UPDATE users SET status = ?, ban_until = NULL, ban_reason = NULL, "
                "banned_by = NULL WHERE id = ?",
                (STATUS_ACTIVE, user_id),
            )
            await notify.send_message(self.bot, self.db, user_id, texts.BAN_LIFTED)
            log.info("Снята истёкшая блокировка: %s", user_id)

    async def expire_shadows(self) -> None:
        moment = now()
        rows = await self.db.fetchall(
            "SELECT id, shadow_notified FROM users WHERE shadow_level > 0 "
            "AND shadow_until IS NOT NULL AND shadow_until <= ?",
            (moment,),
        )
        for row in rows:
            user_id = int(row["id"])
            await self.db.execute(
                "UPDATE users SET shadow_level = 0, shadow_until = NULL, shadow_reason = NULL, "
                "shadow_notified = 0 WHERE id = ?",
                (user_id,),
            )
            if row.get("shadow_notified"):
                await notify.send_message(self.bot, self.db, user_id, texts.SHADOW_LIFTED)
            log.info("Снято ограничение охвата: %s", user_id)

    # ------------------------------------------------------------------ модерация
    async def check_sla(self) -> None:
        """Не даём жалобам «протухнуть»: напоминаем модераторам."""
        overdue = await moderation.overdue_reports(self.db, self.settings)
        if not overdue:
            return
        if now() - self._last_sla_alert < SLA_ALERT_INTERVAL:
            return
        self._last_sla_alert = now()
        hours = self.settings.get_int("report_sla_hours", 6)
        await notify.notify_staff(
            self.bot,
            self.db,
            f"⏰ <b>{overdue} жалоб(ы) ждут дольше {hours} ч.</b>\n"
            "Откройте «🛠 Админ-панель → 🚩 Жалобы».",
            chat_id=self.config.moderation_chat_id,
        )

    # ------------------------------------------------------------------ возврат людей
    async def nudge_inactive(self) -> int:
        """Напоминает о непросмотренных лайках тем, кто давно не заходил.

        Пишем только если новости действительно есть, не чаще раза в неделю и
        только тем, кто не отключил уведомления о лайках.
        """
        if not self.settings.get_bool("nudge_enabled", True):
            return 0

        moment = now()
        quiet_days = max(1, self.settings.get_int("nudge_after_days", 3))
        repeat_days = max(1, self.settings.get_int("nudge_every_days", 7))
        batch = max(1, self.settings.get_int("nudge_batch", 200))

        rows = await self.db.fetchall(
            """
            SELECT u.id,
                   (SELECT COUNT(*)
                      FROM likes l
                      JOIN profiles lp ON lp.user_id = l.from_id
                      JOIN users lu ON lu.id = l.from_id
                     WHERE l.to_id = u.id
                       AND l.action IN ('like', 'superlike')
                       AND l.responded = 0
                       AND lp.is_complete = 1
                       AND lp.moderation IN ('ok', 'review')
                       AND lu.status = 'active'
                       AND NOT EXISTS (
                             SELECT 1 FROM likes mine
                              WHERE mine.from_id = u.id AND mine.to_id = l.from_id
                           )
                   ) AS pending
              FROM users u
              JOIN profiles p ON p.user_id = u.id
             WHERE u.status = 'active'
               AND u.ban_permanent = 0
               AND (u.ban_until IS NULL OR u.ban_until <= :now)
               AND u.bot_blocked = 0
               AND u.notify_likes = 1
               AND p.is_complete = 1
               AND u.last_active_at BETWEEN :oldest AND :newest
               AND NOT EXISTS (
                     SELECT 1 FROM events e
                      WHERE e.user_id = u.id AND e.kind = 'nudge' AND e.created_at > :repeat_cutoff
                   )
             ORDER BY u.last_active_at DESC
             LIMIT :batch
            """,
            {
                "now": moment,
                "oldest": moment - self.settings.inactive_cutoff_days * DAY,
                "newest": moment - quiet_days * DAY,
                "repeat_cutoff": moment - repeat_days * DAY,
                "batch": batch,
            },
        )

        sent = 0
        for row in rows:
            pending = int(row["pending"] or 0)
            if pending <= 0:
                continue
            user_id = int(row["id"])
            message = await notify.send_message(
                self.bot,
                self.db,
                user_id,
                texts.NUDGE.format(
                    count=pending, word=plural(pending, "лайк", "лайка", "лайков")
                ),
                reply_markup=inline.likes_notify(),
            )
            await antifraud.log_event(self.db, user_id, "nudge", weight=0)
            if message:
                sent += 1
            await asyncio.sleep(0.05)

        if sent:
            log.info("Напоминаний о лайках отправлено: %s", sent)
        return sent

    # ------------------------------------------------------------------ уборка
    async def cleanup(self) -> None:
        keep_days = max(1, self.settings.get_int("message_keep_days", 30))
        removed = await chat_service.cleanup_old(self.db, keep_days)
        if removed:
            log.info("Удалено старых сообщений: %s", removed)

        moment = now()
        await self.db.execute("DELETE FROM events WHERE created_at < ?", (moment - 60 * DAY,))
        await self.db.execute(
            "DELETE FROM likes WHERE action = 'pass' AND created_at < ?",
            (moment - max(2, self.settings.get_int("pass_ttl_days", 14) * 2) * DAY,),
        )
        await self.db.execute(
            "DELETE FROM admin_log WHERE created_at < ?", (moment - 180 * DAY,)
        )
        removed_stats = await insights.cleanup(self.db)
        if removed_stats:
            log.info("Удалено старых записей статистики: %s", removed_stats)

        antifraud.tracker.prune()
        chat_service.prune()
        await self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
