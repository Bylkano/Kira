# Kira Discord moderation bot

Kira is a focused Discord moderation bot built with Python 3.11+ and discord.py. It stores the configurable word filter in a local JSON file and is designed to run as a Render Web Service.

## Features

- Per-server, admin-configurable banned words and phrases
- Automatic message deletion with escalating 5-minute and 30-minute timeouts
- Warning DMs with the current count and reason
- Optional per-server automod channel
- Channel lock/unlock with audit reasons and embeds
- Health-check HTTP server bound to Render's PORT before Discord login

## Local setup

1. Create a Discord application and bot, then enable Message Content Intent and Server Members Intent.
2. Clone this repository and create a virtual environment.
3. Install dependencies with: pip install -r requirements.txt
4. Copy env.example to .env and set DISCORD_TOKEN. Add your Discord user ID to ADMIN_COMMAND_USER_IDS.
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

1. Push this repository to GitHub and choose New > Web Service in Render.
2. Connect the repository and use the included render.yaml, or set build command to pip install -r requirements.txt and start command to python bot.py.
3. Add DISCORD_TOKEN, OWNER_ID, and ADMIN_COMMAND_USER_IDS as Render environment variables. Do not commit secrets.
4. Deploy, then confirm the service logs show the health server and a Discord login.
5. Attach a Render persistent disk mounted at /opt/render/project/src/data if banned words must survive redeploys. Local JSON storage without a disk is not durable across instance replacement.

Kira does not use Replit hosting, Replit DB, or Replit deployment configuration.
