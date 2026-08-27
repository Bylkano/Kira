import asyncio
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import discord
from discord import app_commands
from discord.ext import commands

import config
import store

COGS = ("cogs.automod", "cogs.moderation", "cogs.booster_roles", "cogs.boosters")
log = logging.getLogger("kira")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, format: str, *args) -> None: return

def start_health_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("Health server listening on port %s", port)

class Kira(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix=config.BOT_PREFIX, intents=intents, help_command=None)

    async def setup_hook(self) -> None:
        for extension in COGS: await self.load_extension(extension)
        if config.DEV_GUILD_ID:
            guild = discord.Object(id=config.DEV_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else: await self.tree.sync()
        log.info("Slash commands synced")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s) in %s guild(s)", self.user, self.user.id, len(self.guilds))

    async def on_command_error(self, context: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound): return
        if isinstance(error, (commands.MissingPermissions, commands.BotMissingPermissions)):
            try: await context.send("You or Kira lacks the permissions required for that command.")
            except discord.HTTPException: pass
        else: log.error("Prefix command failed", exc_info=error)

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions): message = "You lack the permissions required for that command."
        elif isinstance(error, app_commands.BotMissingPermissions): message = "Kira lacks the permissions required for that command."
        else:
            log.error("Slash command failed", exc_info=error)
            message = "Something went wrong running that command."
        try:
            if interaction.response.is_done(): await interaction.followup.send(message, ephemeral=True)
            else: await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException as exc:
            if exc.status == 429: log.warning("Rate limited while sending command error")

async def main() -> None:
    config.validate()
    store.init_db()
    log.info("PostgreSQL schema is ready")
    start_health_server()
    async with Kira() as bot: await bot.start(config.DISCORD_TOKEN)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
