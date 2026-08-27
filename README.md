# Kira Discord moderation bot

Kira is a focused Discord moderation bot built with Python 3.11+ and discord.py 2.6+. It stores moderation settings and booster role customizations in PostgreSQL and runs as a Render Web Service.

## Features

- Per-server, admin-configurable banned words and phrases
- Automatic message deletion with escalating timeouts
- Booster custom roles with a chosen name, solid colors, Level 3 gradients, and static custom emoji icons
- Visual /boosterole menu for boosters and administrators: create, rename, delete, share with up to 2 members, color, and icon
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

- /boosterole menu — booster role menu for boosters and server administrators

From the menu you can create a personal role and type its name, rename it, delete it, share it with up to 2 members (and remove or add them back), pick a solid color or Level 3 gradient, and set a static custom emoji icon.

New booster roles are placed directly under a role named Jailed. The bot needs Manage Roles, and its highest role must be above Jailed and all personal color roles. Role icons require the server's ROLE_ICONS feature. Animated and Unicode emoji are not accepted as role icons.

## Deploy on Render

1. Open your Render PostgreSQL database and copy its Internal Database URL. Use the Internal URL when the database and web service are in the same Render region.
2. Open the Kira web service's Environment settings and add DATABASE_URL with that URL. Also add DISCORD_TOKEN, OWNER_ID, and ADMIN_COMMAND_USER_IDS.
3. Deploy or trigger a manual redeploy. Kira creates the kira_guilds, kira_banned_words, kira_booster_roles, and kira_booster_role_shares tables automatically at startup.
4. Confirm the logs show the health server, PostgreSQL schema initialization, and a Discord login.

Do not commit database URLs or bot tokens. Kira does not use Replit hosting, Replit DB, or Replit deployment configuration.
