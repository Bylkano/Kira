import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

DATA_PATH = Path(os.getenv("KIRA_DATA_PATH", "data/kira_data.json"))
_LOCK = threading.Lock()
_DEFAULT: dict[str, Any] = {"guilds": {}}


def _read() -> dict[str, Any]:
    try:
        with DATA_PATH.open(encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict) or not isinstance(data.get("guilds"), dict):
            return {"guilds": {}}
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"guilds": {}}


def _write(data: dict[str, Any]) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="kira-", suffix=".json", dir=DATA_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, DATA_PATH)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _guild(data: dict[str, Any], guild_id: int) -> dict[str, Any]:
    guilds = data.setdefault("guilds", {})
    return guilds.setdefault(str(guild_id), {"automod_channel_id": None, "banned_words": []})


def get_automod_channel(guild_id: int) -> int | None:
    with _LOCK:
        value = _guild(_read(), guild_id).get("automod_channel_id")
        return int(value) if value else None


def set_automod_channel(guild_id: int, channel_id: int | None) -> None:
    with _LOCK:
        data = _read()
        _guild(data, guild_id)["automod_channel_id"] = channel_id
        _write(data)


def get_banned_words(guild_id: int) -> list[str]:
    with _LOCK:
        words = _guild(_read(), guild_id).get("banned_words", [])
        return [str(word) for word in words]


def add_banned_word(guild_id: int, word: str) -> bool:
    word = word.strip()
    if not word:
        return False
    with _LOCK:
        data = _read()
        guild = _guild(data, guild_id)
        words = guild.setdefault("banned_words", [])
        if word.casefold() in {str(item).casefold() for item in words}:
            return False
        words.append(word)
        _write(data)
        return True


def remove_banned_word(guild_id: int, word: str) -> bool:
    with _LOCK:
        data = _read()
        guild = _guild(data, guild_id)
        words = guild.setdefault("banned_words", [])
        for index, item in enumerate(words):
            if str(item).casefold() == word.strip().casefold():
                words.pop(index)
                _write(data)
                return True
        return False
