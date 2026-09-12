"""Схема базы данных и миграции.

Версия схемы хранится в ``PRAGMA user_version``. Каждая миграция — список
SQL-выражений, которые применяются по порядку.
"""
from __future__ import annotations

MIGRATIONS: list[list[str]] = [
    # --- версия 1: базовая схема -------------------------------------------------
    [
        """
        CREATE TABLE IF NOT EXISTS users (
            id                  INTEGER PRIMARY KEY,
            username            TEXT,
            tg_name             TEXT,
            created_at          INTEGER NOT NULL,
            last_active_at      INTEGER NOT NULL,
            rules_accepted_at   INTEGER,
            role                INTEGER NOT NULL DEFAULT 0,
            status              TEXT    NOT NULL DEFAULT 'new',
            trust_score         INTEGER NOT NULL DEFAULT 50,
            ban_until           INTEGER,
            ban_permanent       INTEGER NOT NULL DEFAULT 0,
            ban_reason          TEXT,
            banned_by           INTEGER,
            shadow_level        INTEGER NOT NULL DEFAULT 0,
            shadow_until        INTEGER,
            shadow_reason       TEXT,
            shadow_notified     INTEGER NOT NULL DEFAULT 0,
            warns               INTEGER NOT NULL DEFAULT 0,
            captcha_passed      INTEGER NOT NULL DEFAULT 0,
            bot_blocked         INTEGER NOT NULL DEFAULT 0,
            referrer_id         INTEGER,
            source              TEXT,
            notify_likes        INTEGER NOT NULL DEFAULT 1,
            notify_matches      INTEGER NOT NULL DEFAULT 1,
            notify_messages     INTEGER NOT NULL DEFAULT 1,
            last_like_notify_at INTEGER NOT NULL DEFAULT 0,
            flags               INTEGER NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_users_status ON users(status)",
        "CREATE INDEX IF NOT EXISTS idx_users_created ON users(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_users_active ON users(last_active_at)",
        """
        CREATE TABLE IF NOT EXISTS profiles (
            user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            name            TEXT,
            age             INTEGER,
            gender          TEXT,
            seeking         TEXT NOT NULL DEFAULT 'any',
            city            TEXT,
            city_norm       TEXT,
            bio             TEXT,
            bio_hash        TEXT,
            interests       TEXT NOT NULL DEFAULT '',
            age_min         INTEGER NOT NULL DEFAULT 18,
            age_max         INTEGER NOT NULL DEFAULT 99,
            only_my_city    INTEGER NOT NULL DEFAULT 1,
            is_complete     INTEGER NOT NULL DEFAULT 0,
            is_visible      INTEGER NOT NULL DEFAULT 1,
            moderation      TEXT NOT NULL DEFAULT 'ok',
            moderation_note TEXT,
            updated_at      INTEGER,
            likes_received  INTEGER NOT NULL DEFAULT 0,
            likes_sent      INTEGER NOT NULL DEFAULT 0,
            matches_count   INTEGER NOT NULL DEFAULT 0,
            reports_count   INTEGER NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_profiles_search ON profiles(is_complete, is_visible, moderation, gender, age)",
        "CREATE INDEX IF NOT EXISTS idx_profiles_city ON profiles(city_norm)",
        "CREATE INDEX IF NOT EXISTS idx_profiles_biohash ON profiles(bio_hash)",
        """
        CREATE TABLE IF NOT EXISTS photos (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            file_id        TEXT NOT NULL,
            file_unique_id TEXT NOT NULL,
            kind           TEXT NOT NULL DEFAULT 'photo',
            position       INTEGER NOT NULL DEFAULT 0,
            created_at     INTEGER NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_photos_user ON photos(user_id, position)",
        "CREATE INDEX IF NOT EXISTS idx_photos_unique ON photos(file_unique_id)",
        """
        CREATE TABLE IF NOT EXISTS likes (
            from_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            to_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            action     TEXT NOT NULL,
            source     TEXT NOT NULL DEFAULT 'feed',
            message    TEXT,
            created_at INTEGER NOT NULL,
            seen       INTEGER NOT NULL DEFAULT 0,
            responded  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (from_id, to_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_likes_to ON likes(to_id, action, responded)",
        "CREATE INDEX IF NOT EXISTS idx_likes_from_time ON likes(from_id, action, created_at)",
        """
        CREATE TABLE IF NOT EXISTS matches (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_a          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            user_b          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at      INTEGER NOT NULL,
            active          INTEGER NOT NULL DEFAULT 1,
            closed_by       INTEGER,
            last_message_at INTEGER NOT NULL DEFAULT 0,
            last_read_a     INTEGER NOT NULL DEFAULT 0,
            last_read_b     INTEGER NOT NULL DEFAULT 0,
            UNIQUE (user_a, user_b)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_matches_a ON matches(user_a, active)",
        "CREATE INDEX IF NOT EXISTS idx_matches_b ON matches(user_b, active)",
        """
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id   INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
            from_id    INTEGER NOT NULL,
            to_id      INTEGER NOT NULL,
            kind       TEXT NOT NULL,
            text       TEXT,
            file_id    TEXT,
            created_at INTEGER NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_messages_match ON messages(match_id, created_at)",
        """
        CREATE TABLE IF NOT EXISTS reports (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id INTEGER NOT NULL,
            target_id   INTEGER NOT NULL,
            category    TEXT NOT NULL,
            comment     TEXT,
            context     TEXT NOT NULL DEFAULT 'feed',
            match_id    INTEGER,
            status      TEXT NOT NULL DEFAULT 'open',
            priority    INTEGER NOT NULL DEFAULT 0,
            created_at  INTEGER NOT NULL,
            handled_by  INTEGER,
            handled_at  INTEGER,
            resolution  TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status, priority, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_reports_target ON reports(target_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_reports_reporter ON reports(reporter_id, created_at)",
        """
        CREATE TABLE IF NOT EXISTS blocks (
            user_id    INTEGER NOT NULL,
            target_id  INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, target_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_blocks_target ON blocks(target_id)",
        """
        CREATE TABLE IF NOT EXISTS appeals (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            text       TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'open',
            created_at INTEGER NOT NULL,
            handled_by INTEGER,
            handled_at INTEGER,
            answer     TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_appeals_status ON appeals(status, created_at)",
        """
        CREATE TABLE IF NOT EXISTS events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            kind       TEXT NOT NULL,
            weight     INTEGER NOT NULL DEFAULT 0,
            meta       TEXT,
            created_at INTEGER NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, kind, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind, created_at)",
        """
        CREATE TABLE IF NOT EXISTS admin_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id   INTEGER NOT NULL,
            action     TEXT NOT NULL,
            target_id  INTEGER,
            details    TEXT,
            created_at INTEGER NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_adminlog_time ON admin_log(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_adminlog_admin ON admin_log(admin_id, created_at)",
        """
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS banned_content (
            hash       TEXT PRIMARY KEY,
            kind       TEXT NOT NULL,
            reason     TEXT,
            added_by   INTEGER,
            created_at INTEGER NOT NULL
        )
        """,
    ],
    # --- версия 2: верификация анкет, фильтр и статистика показов -----------------
    [
        "ALTER TABLE users ADD COLUMN verified INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN verify_status TEXT NOT NULL DEFAULT 'none'",
        "ALTER TABLE users ADD COLUMN verify_file_id TEXT",
        "ALTER TABLE users ADD COLUMN verify_gesture TEXT",
        "ALTER TABLE users ADD COLUMN verify_at INTEGER",
        "ALTER TABLE users ADD COLUMN verify_note TEXT",
        "CREATE INDEX IF NOT EXISTS idx_users_verify ON users(verify_status, verify_at)",
        "ALTER TABLE profiles ADD COLUMN only_verified INTEGER NOT NULL DEFAULT 0",
        """
        CREATE TABLE IF NOT EXISTS profile_stats (
            user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            day      INTEGER NOT NULL,
            shown    INTEGER NOT NULL DEFAULT 0,
            likes_in INTEGER NOT NULL DEFAULT 0,
            matches  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_profile_stats_day ON profile_stats(day)",
    ],
    # --- версия 3: стоп-лист помнит, чей это контент ------------------------------
    [
        "ALTER TABLE banned_content ADD COLUMN added_for INTEGER",
        "CREATE INDEX IF NOT EXISTS idx_banned_for ON banned_content(added_for, kind)",
    ],
    # --- версия 4: открытый диалог переживает перезапуск бота ----------------------
    [
        "ALTER TABLE users ADD COLUMN active_match_id INTEGER",
    ],
    # --- версия 5: координаты города и радиус поиска -------------------------------
    #
    # search_radius заменяет only_my_city: 0 — только свой город, 1..998 — радиус
    # в километрах, 999 и больше — без ограничения по географии. Старая колонка
    # остаётся в таблице как след истории, но код её больше не читает.
    [
        "ALTER TABLE profiles ADD COLUMN lat REAL",
        "ALTER TABLE profiles ADD COLUMN lon REAL",
        "ALTER TABLE profiles ADD COLUMN geo_source TEXT",
        "ALTER TABLE profiles ADD COLUMN search_radius INTEGER NOT NULL DEFAULT 0",
        "UPDATE profiles SET search_radius = CASE WHEN only_my_city = 1 THEN 0 ELSE 999 END",
        "CREATE INDEX IF NOT EXISTS idx_profiles_geo ON profiles(lat, lon)",
    ],
]


SCHEMA_VERSION = len(MIGRATIONS)
