import threading

import psycopg2

import config

_LOCK = threading.Lock()


def _connect():
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(config.DATABASE_URL)


def init_db() -> None:
    """Create Kira's small persistence schema if it does not exist."""
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
