# Kira Discord moderation bot

Kira is a focused Discord moderation bot built with Python 3.11+ and discord.py 2.6+. It stores moderation settings and booster role customizations in PostgreSQL and runs as a Render Web Service.

## Features

- Per-server, admin-configurable banned words and phrases
- Automatic message deletion with escalating timeouts
- Booster custom roles with solid colors, Level 3 gradients, and static custom emoji icons
- Visual /color menu with presets, custom hex colors, preview, confirm, and back controls
- Booster roles are automatically removed after three days without boosting
- Channel lock/unlock with audit reasons and embeds
- Automatic PostgreSQL table initialization at startup
- Health-check HTTP server bound to Render's PORT before Discord login

## Local setup

1. Create a Discord application and bot, then enable Message Content Intent and Server Members Intent.
2. Clone this repository and create a virtual environment.
3. Install dependencies with: pip install -r requirements.txt
4. Copy env.example to .env and set DISCORD_TOKEN and DATABASE_URL. Add your Discord user ID to ADMIN_COMMAND_USER_IDS if needed.
5. Invite Kira with the bot and applications.commands scopes and moderation permissions.
6. Run python bot.py.

DEV_GUILD_ID is optional and makes slash commands sync immediately to one development server. Without it, commands sync globally and Discord propagation can take time.

## Commands

### Automod

- /addbadword word
- /removebadword word
- /listbadwords
- /setautomodchannel channel
- /getautomodchannel
- /warns member
- /clearwarns member

### Moderation

- /lock channel reason
- /unlock channel reason

### Booster roles

- /color menu — visual customization flow with presets and live preview
- /color solid hex_code — fast solid color shortcut
- /color gradient hex1 hex2 — fast gradient shortcut; requires Boost Level 3
- /color icon emoji — paste a static custom emoji such as <:sparkles:123456789012345678>
- /color preview
- /color remove

All /color commands require the user to currently be boosting the server. The bot needs Manage Roles, and its highest role must be above the server booster role and all personal color roles. Role icons require the server's ROLE_ICONS feature. Animated and Unicode emoji are not accepted as role icons.

## Deploy on Render

1. Open your Render PostgreSQL database and copy its Internal Database URL. Use the Internal URL when the database and web service are in the same Render region.
2. Open the Kira web service's Environment settings and add DATABASE_URL with that URL. Also add DISCORD_TOKEN, OWNER_ID, and ADMIN_COMMAND_USER_IDS.
3. Deploy or trigger a manual redeploy. Kira creates the kira_guilds, kira_banned_words, and kira_booster_roles tables automatically at startup.
4. Confirm the logs show the health server, PostgreSQL schema initialization, and a Discord login.

Do not commit database URLs or bot tokens. Kira does not use Replit hosting, Replit DB, or Replit deployment configuration.
