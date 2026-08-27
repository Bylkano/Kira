import threading
from datetime import datetime, timezone

import psycopg2

import config

_LOCK = threading.Lock()


def _connect():
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(config.DATABASE_URL)


def init_db() -> None:
    """Create Kira's persistence schema if it does not exist."""
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS kira_guilds (
                    guild_id BIGINT PRIMARY KEY,
                    automod_channel_id BIGINT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS kira_banned_words (
                    guild_id BIGINT NOT NULL REFERENCES kira_guilds(guild_id) ON DELETE CASCADE,
                    word TEXT NOT NULL,
                    normalized_word TEXT NOT NULL,
                    PRIMARY KEY (guild_id, normalized_word)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS kira_booster_roles (
                    guild_id BIGINT NOT NULL REFERENCES kira_guilds(guild_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    role_id BIGINT NOT NULL,
                    color_type TEXT NOT NULL CHECK (color_type IN ('solid', 'gradient')),
                    color_primary TEXT NOT NULL,
                    color_secondary TEXT,
                    icon_emoji_id BIGINT,
                    icon_emoji_name TEXT,
                    icon_animated BOOLEAN,
                    boosting_stopped_at TIMESTAMPTZ,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS kira_boosters (
                    guild_id BIGINT NOT NULL REFERENCES kira_guilds(guild_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    boosting_since TIMESTAMPTZ NOT NULL,
                    boosting_stopped_at TIMESTAMPTZ,
                    boost_count INTEGER NOT NULL DEFAULT 1 CHECK (boost_count >= 1),
                    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
        connection.commit()


def _ensure_guild(cursor, guild_id: int) -> None:
    cursor.execute("INSERT INTO kira_guilds (guild_id) VALUES (%s) ON CONFLICT DO NOTHING", (guild_id,))


def get_automod_channel(guild_id: int) -> int | None:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            _ensure_guild(cursor, guild_id)
            cursor.execute("SELECT automod_channel_id FROM kira_guilds WHERE guild_id = %s", (guild_id,))
            row = cursor.fetchone()
        connection.commit()
    return int(row[0]) if row and row[0] else None


def set_automod_channel(guild_id: int, channel_id: int | None) -> None:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""INSERT INTO kira_guilds (guild_id, automod_channel_id) VALUES (%s, %s)
                ON CONFLICT (guild_id) DO UPDATE SET automod_channel_id = EXCLUDED.automod_channel_id""", (guild_id, channel_id))
        connection.commit()


def get_banned_words(guild_id: int) -> list[str]:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            _ensure_guild(cursor, guild_id)
            cursor.execute("SELECT word FROM kira_banned_words WHERE guild_id = %s ORDER BY word", (guild_id,))
            words = [row[0] for row in cursor.fetchall()]
        connection.commit()
    return words


def add_banned_word(guild_id: int, word: str) -> bool:
    word = word.strip()
    if not word: return False
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            _ensure_guild(cursor, guild_id)
            cursor.execute("""INSERT INTO kira_banned_words (guild_id, word, normalized_word)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""", (guild_id, word, word.casefold()))
            added = cursor.rowcount == 1
        connection.commit()
    return added


def remove_banned_word(guild_id: int, word: str) -> bool:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM kira_banned_words WHERE guild_id = %s AND normalized_word = %s", (guild_id, word.strip().casefold()))
            removed = cursor.rowcount == 1
        connection.commit()
    return removed


def _booster_row(row) -> dict | None:
    if not row: return None
    keys = ("guild_id", "user_id", "role_id", "color_type", "color_primary", "color_secondary", "icon_emoji_id", "icon_emoji_name", "icon_animated", "boosting_stopped_at")
    return dict(zip(keys, row))


def get_booster_role(guild_id: int, user_id: int) -> dict | None:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT guild_id, user_id, role_id, color_type, color_primary, color_secondary,
                icon_emoji_id, icon_emoji_name, icon_animated, boosting_stopped_at
                FROM kira_booster_roles WHERE guild_id = %s AND user_id = %s""", (guild_id, user_id))
            row = cursor.fetchone()
    return _booster_row(row)


def upsert_booster_role(guild_id: int, user_id: int, role_id: int, color_type: str, color_primary: str,
                        color_secondary: str | None = None, icon_emoji_id: int | None = None,
                        icon_emoji_name: str | None = None, icon_animated: bool | None = None) -> None:
    if color_type not in {"solid", "gradient"}: raise ValueError("color_type must be solid or gradient")
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            _ensure_guild(cursor, guild_id)
            cursor.execute("""INSERT INTO kira_booster_roles
                (guild_id, user_id, role_id, color_type, color_primary, color_secondary,
                 icon_emoji_id, icon_emoji_name, icon_animated, boosting_stopped_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
                ON CONFLICT (guild_id, user_id) DO UPDATE SET
                  role_id = EXCLUDED.role_id, color_type = EXCLUDED.color_type,
                  color_primary = EXCLUDED.color_primary, color_secondary = EXCLUDED.color_secondary,
                  icon_emoji_id = EXCLUDED.icon_emoji_id, icon_emoji_name = EXCLUDED.icon_emoji_name,
                  icon_animated = EXCLUDED.icon_animated, boosting_stopped_at = NULL""",
                (guild_id, user_id, role_id, color_type, color_primary, color_secondary,
                 icon_emoji_id, icon_emoji_name, icon_animated))
        connection.commit()


def mark_boosting_stopped(guild_id: int, user_id: int) -> None:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""UPDATE kira_booster_roles SET boosting_stopped_at = COALESCE(boosting_stopped_at, NOW())
                WHERE guild_id = %s AND user_id = %s""", (guild_id, user_id))
        connection.commit()


def clear_boosting_stopped(guild_id: int, user_id: int) -> None:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE kira_booster_roles SET boosting_stopped_at = NULL WHERE guild_id = %s AND user_id = %s", (guild_id, user_id))
        connection.commit()


def get_expired_booster_roles(days: int = 3) -> list[dict]:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT guild_id, user_id, role_id, color_type, color_primary, color_secondary,
                icon_emoji_id, icon_emoji_name, icon_animated, boosting_stopped_at
                FROM kira_booster_roles
                WHERE boosting_stopped_at IS NOT NULL AND boosting_stopped_at < NOW() - (%s * INTERVAL '1 day')""", (days,))
            rows = cursor.fetchall()
    return [_booster_row(row) for row in rows]


def delete_booster_role(guild_id: int, user_id: int) -> None:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM kira_booster_roles WHERE guild_id = %s AND user_id = %s", (guild_id, user_id))
        connection.commit()


def _boost_tracker_row(row) -> dict | None:
    if not row: return None
    keys = ("guild_id", "user_id", "boosting_since", "boosting_stopped_at", "boost_count")
    return dict(zip(keys, row))


def record_boost_start(guild_id: int, user_id: int, boosting_since: datetime) -> None:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            _ensure_guild(cursor, guild_id)
            cursor.execute("""INSERT INTO kira_boosters
                (guild_id, user_id, boosting_since, boosting_stopped_at, boost_count, last_seen_at)
                VALUES (%s, %s, %s, NULL, 1, NOW())
                ON CONFLICT (guild_id, user_id) DO UPDATE SET
                  boosting_since = COALESCE(EXCLUDED.boosting_since, kira_boosters.boosting_since),
                  boosting_stopped_at = NULL,
                  boost_count = CASE
                    WHEN kira_boosters.boosting_stopped_at IS NOT NULL THEN 1
                    ELSE GREATEST(kira_boosters.boost_count, 1)
                  END,
                  last_seen_at = NOW()""", (guild_id, user_id, boosting_since))
        connection.commit()


def mark_boost_stopped(guild_id: int, user_id: int) -> None:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""UPDATE kira_boosters SET boosting_stopped_at = COALESCE(boosting_stopped_at, NOW()), last_seen_at = NOW()
                WHERE guild_id = %s AND user_id = %s AND boosting_stopped_at IS NULL""", (guild_id, user_id))
        connection.commit()


def increment_boost_count(guild_id: int, user_id: int, boosting_since: datetime | None = None) -> None:
    started = boosting_since or datetime.now(timezone.utc)
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            _ensure_guild(cursor, guild_id)
            cursor.execute("""INSERT INTO kira_boosters
                (guild_id, user_id, boosting_since, boosting_stopped_at, boost_count, last_seen_at)
                VALUES (%s, %s, %s, NULL, 1, NOW())
                ON CONFLICT (guild_id, user_id) DO UPDATE SET
                  boosting_since = COALESCE(kira_boosters.boosting_since, EXCLUDED.boosting_since),
                  boosting_stopped_at = NULL,
                  boost_count = CASE
                    WHEN kira_boosters.boosting_stopped_at IS NOT NULL THEN 1
                    ELSE kira_boosters.boost_count + 1
                  END,
                  last_seen_at = NOW()""", (guild_id, user_id, started))
        connection.commit()


def get_boost_tracker(guild_id: int, user_id: int) -> dict | None:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT guild_id, user_id, boosting_since, boosting_stopped_at, boost_count
                FROM kira_boosters WHERE guild_id = %s AND user_id = %s""", (guild_id, user_id))
            row = cursor.fetchone()
    return _boost_tracker_row(row)


def get_active_boost_trackers(guild_id: int) -> list[dict]:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT guild_id, user_id, boosting_since, boosting_stopped_at, boost_count
                FROM kira_boosters WHERE guild_id = %s AND boosting_stopped_at IS NULL
                ORDER BY boosting_since""", (guild_id,))
            rows = cursor.fetchall()
    return [_boost_tracker_row(row) for row in rows]


def get_stopped_boost_trackers(guild_id: int, limit: int = 10) -> list[dict]:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT guild_id, user_id, boosting_since, boosting_stopped_at, boost_count
                FROM kira_boosters WHERE guild_id = %s AND boosting_stopped_at IS NOT NULL
                ORDER BY boosting_stopped_at DESC LIMIT %s""", (guild_id, limit))
            rows = cursor.fetchall()
    return [_boost_tracker_row(row) for row in rows]


def mark_missing_boosters_stopped(guild_id: int, active_user_ids: list[int]) -> None:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            if active_user_ids:
                cursor.execute("""UPDATE kira_boosters SET boosting_stopped_at = NOW(), last_seen_at = NOW()
                    WHERE guild_id = %s AND boosting_stopped_at IS NULL AND NOT (user_id = ANY(%s))""",
                    (guild_id, active_user_ids))
            else:
                cursor.execute("""UPDATE kira_boosters SET boosting_stopped_at = NOW(), last_seen_at = NOW()
                    WHERE guild_id = %s AND boosting_stopped_at IS NULL""", (guild_id,))
        connection.commit()
