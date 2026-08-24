# Kira Discord moderation bot

Kira is a focused Discord moderation bot built with Python 3.11+ and discord.py. It stores banned words and automod settings in PostgreSQL and runs as a Render Web Service.

## Features

- Per-server, admin-configurable banned words and phrases
- Automatic message deletion with escalating 5-minute and 30-minute timeouts
- Warning DMs with the current count and reason
- Optional per-server automod channel
- Channel lock/unlock with audit reasons and embeds
- Automatic PostgreSQL table initialization at startup
- Health-check HTTP server bound to Render's PORT before Discord login

## Local setup

1. Create a Discord application and bot, then enable Message Content Intent and Server Members Intent.
2. Clone this repository and create a virtual environment.
3. Install dependencies with: pip install -r requirements.txt
4. Copy env.example to .env and set DISCORD_TOKEN and DATABASE_URL. Add your Discord user ID to ADMIN_COMMAND_USER_IDS.
5. Invite Kira with the bot and applications.commands scopes and moderation permissions.
6. Run python bot.py.

DEV_GUILD_ID is optional and makes slash commands sync immediately to one development server. Without it, commands sync globally and Discord propagation can take time.

## Commands

/addbadword, /removebadword, /listbadwords
/setautomodchannel, /getautomodchannel
/warns, /clearwarns
/lock, /unlock

Commands that change filter settings or clear warnings also require the user to be in ADMIN_COMMAND_USER_IDS (or be OWNER_ID).

## Deploy on Render

1. Open your Render PostgreSQL database and copy its Internal Database URL. Use the Internal URL when the database and web service are in the same Render region.
2. Open the Kira web service's Environment settings and add DATABASE_URL with that URL. Also add DISCORD_TOKEN, OWNER_ID, and ADMIN_COMMAND_USER_IDS.
3. Deploy or trigger a manual redeploy. Kira creates the kira_guilds and kira_banned_words tables automatically on startup.
4. Confirm the logs show the health server, database initialization, and a Discord login.

Do not commit database URLs or bot tokens. Kira does not use Replit hosting, Replit DB, or Replit deployment configuration.
