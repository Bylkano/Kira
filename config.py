import os
import logging

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)


def _token() -> str:
    discord_token = os.getenv("DISCORD_TOKEN", "").strip()
    legacy_token = os.getenv("BOT_TOKEN", "").strip()
    if discord_token and legacy_token and discord_token != legacy_token:
        log.warning("DISCORD_TOKEN and BOT_TOKEN differ; using DISCORD_TOKEN")
    return discord_token or legacy_token


DISCORD_TOKEN = _token()
DATABASE_URL = ""  # Kept intentionally unused: Kira persists to local JSON.
BOT_PREFIX = os.getenv("BOT_PREFIX", "!")
DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID", "0") or 0) or None
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0) or None


def _admin_ids() -> set[int]:
    values = os.getenv("ADMIN_COMMAND_USER_IDS", "")
    ids: set[int] = set()
    for value in values.split(","):
        value = value.strip()
        if value.isdigit():
            ids.add(int(value))
    return ids


ADMIN_COMMAND_USER_IDS = _admin_ids()


def can_use_admin_commands(user_id: int) -> bool:
    """Return whether a user is allowed to run sensitive automod commands."""
    return user_id in ADMIN_COMMAND_USER_IDS or (OWNER_ID is not None and user_id == OWNER_ID)


def validate() -> None:
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN (or legacy BOT_TOKEN) is not configured")
