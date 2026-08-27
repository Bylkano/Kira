import threading
from datetime import datetime

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
                CREATE TABLE IF NOT EXISTS kira_booster_role_shares (
                    guild_id BIGINT NOT NULL,
                    owner_id BIGINT NOT NULL,
                    member_id BIGINT NOT NULL,
                    PRIMARY KEY (guild_id, owner_id, member_id),
                    FOREIGN KEY (guild_id, owner_id) REFERENCES kira_booster_roles (guild_id, user_id) ON DELETE CASCADE
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


SHARE_LIMIT = 2


def get_role_shares(guild_id: int, owner_id: int) -> list[int]:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT member_id FROM kira_booster_role_shares
                WHERE guild_id = %s AND owner_id = %s ORDER BY member_id""", (guild_id, owner_id))
            rows = cursor.fetchall()
    return [int(row[0]) for row in rows]


def add_role_share(guild_id: int, owner_id: int, member_id: int) -> tuple[bool, str]:
    if owner_id == member_id: return False, "You already have your own booster role."
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM kira_booster_roles WHERE guild_id = %s AND user_id = %s", (guild_id, owner_id))
            if cursor.fetchone() is None: return False, "Create your booster role first from the menu."
            cursor.execute("""SELECT member_id FROM kira_booster_role_shares
                WHERE guild_id = %s AND owner_id = %s""", (guild_id, owner_id))
            current = [int(row[0]) for row in cursor.fetchall()]
            if member_id in current: return False, "That member already has your booster role. Remove them first if you want to add someone else."
            if len(current) >= SHARE_LIMIT:
                return False, f"You can share your booster role with {SHARE_LIMIT} members at most. Remove someone first to add them back or share with someone else."
            cursor.execute("""INSERT INTO kira_booster_role_shares (guild_id, owner_id, member_id)
                VALUES (%s, %s, %s)""", (guild_id, owner_id, member_id))
        connection.commit()
    return True, "ok"


def remove_role_share(guild_id: int, owner_id: int, member_id: int) -> bool:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""DELETE FROM kira_booster_role_shares
                WHERE guild_id = %s AND owner_id = %s AND member_id = %s""", (guild_id, owner_id, member_id))
            removed = cursor.rowcount == 1
        connection.commit()
    return removed


def delete_shares_for_member(guild_id: int, member_id: int) -> None:
    with _LOCK, _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM kira_booster_role_shares WHERE guild_id = %s AND member_id = %s", (guild_id, member_id))
        connection.commit()
