import logging
import os

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
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
BOT_PREFIX = os.getenv("BOT_PREFIX", "!")
DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID", "0") or 0) or None
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0) or None


def _admin_ids() -> set[int]:
    return {int(value.strip()) for value in os.getenv("ADMIN_COMMAND_USER_IDS", "").split(",") if value.strip().isdigit()}


ADMIN_COMMAND_USER_IDS = _admin_ids()


def can_use_admin_commands(user_id: int) -> bool:
    return user_id in ADMIN_COMMAND_USER_IDS or (OWNER_ID is not None and user_id == OWNER_ID)


def validate() -> None:
    if not DISCORD_TOKEN: raise RuntimeError("DISCORD_TOKEN (or legacy BOT_TOKEN) is not configured")
    if not DATABASE_URL: raise RuntimeError("DATABASE_URL is not configured")
