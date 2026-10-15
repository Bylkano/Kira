import datetime
import logging
import re
import time
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

import config
import store

log = logging.getLogger("kira.automod")
MAX_WARNS = 3
SHORT_TIMEOUT_MINUTES = 5
MAX_TIMEOUT_MINUTES = 30

ANTISPAM_REPLY = "SHUT UP NO ONE CARES LOSER!"
ANTISPAM_TIMEOUT_DAYS = 7
ANTISPAM_FALLBACK_WINDOW_SECONDS = 180
ANTISPAM_NAME_WORDS = ("bylka", "bylkas", "kindly", "ismael", "sonny")
CHEAT_WORD_RE = re.compile(r"(?<!\w)cheat\w*(?!\w)", re.IGNORECASE)
LVL1_ROLE_RE = re.compile(r"\blvl\s*1\b", re.IGNORECASE)
USER_MENTION_RE = re.compile(r"<@!?(\d+)>")
ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")


def contains_exact_phrase(content: str, phrase: str) -> bool:
    """Return True only when phrase appears as a whole word/phrase, not a substring."""
    pattern = rf"(?<!\w){re.escape(phrase.casefold())}(?!\w)"
    return re.search(pattern, content.casefold()) is not None


def normalize_antispam_context(content: str) -> str:
    """Strip mentions/pings so we can compare message wording."""
    text = USER_MENTION_RE.sub(" ", content)
    text = ROLE_MENTION_RE.sub(" ", text)
    text = re.sub(r"@everyone|@here", " ", text, flags=re.IGNORECASE)
    return " ".join(text.casefold().split())


def has_antispam_trigger_words(content: str) -> bool:
    if any(contains_exact_phrase(content, word) for word in ANTISPAM_NAME_WORDS):
        return True
    return CHEAT_WORD_RE.search(content) is not None


def normalize_role_label(name: str) -> str:
    """Keep letters/digits only so decorative Member role names still match."""
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


def is_members_role_name(name: str) -> bool:
    label = normalize_role_label(name)
    return label in {"members", "member"}


def collect_message_pings(message: discord.Message) -> frozenset[str]:
    """Collect user + role pings (and @everyone/@here). Role pings count the same as user pings."""
    pings: set[str] = {f"user:{user.id}" for user in message.mentions}
    pings.update(f"role:{role.id}" for role in message.role_mentions)
    # Raw content backup in case Discord omits some resolved mention objects.
    pings.update(f"user:{user_id}" for user_id in USER_MENTION_RE.findall(message.content))
    pings.update(f"role:{role_id}" for role_id in ROLE_MENTION_RE.findall(message.content))
    if message.mention_everyone:
        lowered = message.content.casefold()
        if "@everyone" in lowered:
            pings.add("everyone")
        if "@here" in lowered:
            pings.add("here")
        if "@everyone" not in lowered and "@here" not in lowered:
            pings.add("everyone")
    return frozenset(pings)


def is_antispam_role_target(member: discord.Member) -> bool:
    """True for lvl 1, Members-only, or Members + roles that never include 'lvl'."""
    if member.guild_permissions.administrator or member.guild_permissions.manage_messages:
        return False
    roles = [role for role in member.roles if not role.is_default()]
    if not roles:
        return False
    if any(LVL1_ROLE_RE.search(role.name) for role in roles):
        return True
    member_roles = [role for role in roles if is_members_role_name(role.name)]
    if not member_roles:
        return False
    other_roles = [role for role in roles if not is_members_role_name(role.name)]
    if not other_roles:
        return True
    return not any("lvl" in role.name.casefold() for role in other_roles)


class AutoMod(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.warn_counts: dict[int, dict[int, int]] = defaultdict(dict)
        # guild_id -> user_id -> list[(ping_keys, context, timestamp)]
        self._antispam_recent: dict[int, dict[int, list[tuple[frozenset[str], str, float]]]] = defaultdict(
            lambda: defaultdict(list)
        )

    def _sensitive(self, interaction: discord.Interaction) -> bool:
        return config.can_use_admin_commands(interaction.user.id, interaction.guild)

    async def _timeout(self, member: discord.Member, minutes: int, reason: str) -> None:
        try:
            await member.timeout(datetime.timedelta(minutes=minutes), reason=reason)
        except discord.HTTPException as exc:
            if exc.status == 429: log.warning("Rate limited while timing out %s", member.id)
            else: log.warning("Could not timeout %s: %s", member.id, exc)
        except discord.Forbidden: log.warning("Missing permission to timeout %s", member.id)

    async def _timeout_days(self, member: discord.Member, days: int, reason: str) -> None:
        try:
            await member.timeout(datetime.timedelta(days=days), reason=reason)
        except discord.HTTPException as exc:
            if exc.status == 429: log.warning("Rate limited while timing out %s", member.id)
            else: log.warning("Could not timeout %s: %s", member.id, exc)
        except discord.Forbidden: log.warning("Missing permission to timeout %s", member.id)

    async def _dm(self, member: discord.Member, embed: discord.Embed) -> None:
        try:
            await member.send(embed=embed)
        except discord.HTTPException as exc:
            if exc.status == 429: log.warning("Rate limited while DMing %s", member.id)
            else: log.warning("Could not DM %s: %s", member.id, exc)
        except discord.Forbidden: log.info("DMs closed for %s", member.id)

    async def _punish(self, message: discord.Message, matched: str, filter_label: str) -> None:
        try:
            await message.delete()
        except discord.HTTPException as exc:
            if exc.status == 429:
                log.warning("Rate limited while deleting a filtered message")
                return
            log.warning("Could not delete filtered message: %s", exc)
        except discord.Forbidden:
            log.warning("Missing permission to delete messages in guild %s", message.guild.id)
        warns = self.warn_counts[message.guild.id]
        count = warns.get(message.author.id, 0) + 1
        if count >= MAX_WARNS:
            warns[message.author.id] = 0
            minutes, title = MAX_TIMEOUT_MINUTES, "Maximum warnings reached"
            description = f"You reached {MAX_WARNS}/{MAX_WARNS} warnings. Your warnings were reset."
        else:
            warns[message.author.id] = count
            minutes, title = SHORT_TIMEOUT_MINUTES, f"Warning {count}/{MAX_WARNS}"
            description = f"You have {MAX_WARNS - count} warning(s) remaining before reset."
        await self._timeout(message.author, minutes, f"Automatic moderation: filtered {filter_label} {matched!r}")
        embed = discord.Embed(title=title, description=description, color=discord.Color.red())
        embed.add_field(name="Reason", value=f"Your message contained a filtered {filter_label}.", inline=False)
        embed.add_field(name="Timeout", value=f"{minutes} minutes")
        await self._dm(message.author, embed)

    def _prune_antispam_recent(self, guild_id: int, user_id: int) -> None:
        cutoff = time.monotonic() - ANTISPAM_FALLBACK_WINDOW_SECONDS
        recent = self._antispam_recent[guild_id][user_id]
        self._antispam_recent[guild_id][user_id] = [item for item in recent if item[2] >= cutoff]

    def _remember_antispam_message(self, guild_id: int, user_id: int, pings: frozenset[str], context: str) -> None:
        self._prune_antispam_recent(guild_id, user_id)
        recent = self._antispam_recent[guild_id][user_id]
        recent.append((pings, context, time.monotonic()))
        del recent[:-8]

    def _antispam_fallback_match(self, guild_id: int, user_id: int, pings: frozenset[str], context: str) -> bool:
        if not context:
            return False
        self._prune_antispam_recent(guild_id, user_id)
        for prev_pings, prev_context, _ts in self._antispam_recent[guild_id][user_id]:
            if prev_context != context:
                continue
            same_pings = bool(prev_pings) and prev_pings == pings
            multi_ping = len(pings) > 1
            if same_pings or multi_ping:
                return True
        return False

    async def _antispam_punish(self, message: discord.Message, reason: str) -> bool:
        deleted = False
        try:
            await message.delete()
            deleted = True
        except discord.HTTPException as exc:
            if exc.status == 429:
                log.warning("Rate limited while deleting an antispam message")
            else:
                log.warning("Could not delete antispam message: %s", exc)
        except discord.Forbidden:
            log.warning("Missing permission to delete antispam message in guild %s", message.guild.id)

        if isinstance(message.author, discord.Member):
            await self._timeout_days(
                message.author,
                ANTISPAM_TIMEOUT_DAYS,
                f"Automatic antispam: {reason}",
            )

        try:
            await message.channel.send(ANTISPAM_REPLY)
        except discord.HTTPException as exc:
            if exc.status == 429:
                log.warning("Rate limited while sending antispam reply")
            else:
                log.warning("Could not send antispam reply: %s", exc)
        except discord.Forbidden:
            log.warning("Missing permission to send antispam reply in guild %s", message.guild.id)

        self._antispam_recent[message.guild.id].pop(message.author.id, None)
        return deleted

    async def _check_antispam(self, message: discord.Message) -> bool:
        """Return True when antispam handled the message (caller should stop)."""
        if not message.guild:
            return False
        author = message.author if isinstance(message.author, discord.Member) else message.guild.get_member(message.author.id)
        if author is None or not is_antispam_role_target(author):
            return False

        pings = collect_message_pings(message)
        context = normalize_antispam_context(message.content)
        multi_ping = len(pings) > 1

        if multi_ping and has_antispam_trigger_words(message.content):
            await self._antispam_punish(message, "multi-ping harassment phrase")
            return True

        # Fallback: primary conditions did not fire; catch repeat same-context ping spam.
        if self._antispam_fallback_match(message.guild.id, author.id, pings, context):
            if multi_ping or pings:
                await self._antispam_punish(message, "repeat multi-ping spam")
                return True

        if multi_ping or pings:
            self._remember_antispam_message(message.guild.id, author.id, pings, context)
        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or not message.content: return

        # Harassment antispam: role-gated, server-wide.
        if await self._check_antispam(message):
            return

        # Slur filter: exact match, every channel in the server.
        slur_words = store.get_slur_words(message.guild.id)
        slur_matched = next((word for word in slur_words if contains_exact_phrase(message.content, word)), None)
        if slur_matched is not None:
            await self._punish(message, slur_matched, "slur")
            return

        # Bad-word filter: exact match, limited to the configured automod channel when set.
        channel_id = store.get_automod_channel(message.guild.id)
        if channel_id and message.channel.id != channel_id: return
        words = store.get_banned_words(message.guild.id)
        matched = next((word for word in words if contains_exact_phrase(message.content, word)), None)
        if matched is None: return
        await self._punish(message, matched, "word or phrase")

    @app_commands.command(name="addbadword", description="Add a word or phrase to this server's channel filter.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def addbadword(self, interaction: discord.Interaction, word: str) -> None:
        if not self._sensitive(interaction):
            await interaction.response.send_message("You need to be a server administrator, the bot owner, or an approved admin to use this command.", ephemeral=True); return
        added = store.add_banned_word(interaction.guild_id, word)
        await interaction.response.send_message(f"Added `{word.strip()}` to the filter." if added else "That word is already filtered or empty.", ephemeral=True)

    @app_commands.command(name="removebadword", description="Remove a word or phrase from this server's channel filter.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def removebadword(self, interaction: discord.Interaction, word: str) -> None:
        if not self._sensitive(interaction):
            await interaction.response.send_message("You are not on the admin command allowlist.", ephemeral=True); return
        await interaction.response.send_message("Removed." if store.remove_banned_word(interaction.guild_id, word) else "That phrase is not filtered.", ephemeral=True)

    @app_commands.command(name="listbadwords", description="List this server's channel-filtered words.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def listbadwords(self, interaction: discord.Interaction) -> None:
        words = store.get_banned_words(interaction.guild_id)
        await interaction.response.send_message(", ".join(f"`{word}`" for word in words) or "No banned words configured.", ephemeral=True)

    @app_commands.command(name="addslurwords", description="Add a slur that is filtered in every channel.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def addslurwords(self, interaction: discord.Interaction, word: str) -> None:
        if not self._sensitive(interaction):
            await interaction.response.send_message("You need to be a server administrator, the bot owner, or an approved admin to use this command.", ephemeral=True); return
        added = store.add_slur_word(interaction.guild_id, word)
        await interaction.response.send_message(
            f"Added `{word.strip()}` to the server-wide slur filter." if added else "That slur is already filtered or empty.",
            ephemeral=True,
        )

    @app_commands.command(name="removeslurwords", description="Remove a slur from the server-wide filter.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def removeslurwords(self, interaction: discord.Interaction, word: str) -> None:
        if not self._sensitive(interaction):
            await interaction.response.send_message("You are not on the admin command allowlist.", ephemeral=True); return
        await interaction.response.send_message(
            "Removed." if store.remove_slur_word(interaction.guild_id, word) else "That slur is not filtered.",
            ephemeral=True,
        )

    @app_commands.command(name="listslurwords", description="List this server's server-wide slur filter.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def listslurwords(self, interaction: discord.Interaction) -> None:
        words = store.get_slur_words(interaction.guild_id)
        await interaction.response.send_message(
            ", ".join(f"`{word}`" for word in words) or "No slur words configured.",
            ephemeral=True,
        )

    @app_commands.command(name="setautomodchannel", description="Limit bad-word filtering to one channel (slurs stay server-wide).")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def setautomodchannel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        if not self._sensitive(interaction):
            await interaction.response.send_message("You are not on the admin command allowlist.", ephemeral=True); return
        store.set_automod_channel(interaction.guild_id, channel.id if channel else None)
        await interaction.response.send_message(
            f"Bad-word filtering is now limited to {channel.mention}. Slur filtering remains server-wide."
            if channel else "Bad-word filtering now applies server-wide. Slur filtering remains server-wide.",
            ephemeral=True,
        )

    @app_commands.command(name="getautomodchannel", description="Show the current bad-word filter channel.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def getautomodchannel(self, interaction: discord.Interaction) -> None:
        channel_id = store.get_automod_channel(interaction.guild_id)
        await interaction.response.send_message(
            f"Bad-word filtering channel: <#{channel_id}>. Slur filtering is server-wide."
            if channel_id else "Bad-word filtering is server-wide. Slur filtering is server-wide.",
            ephemeral=True,
        )

    @app_commands.command(name="warns", description="Show a member's current warning count.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def warns(self, interaction: discord.Interaction, member: discord.Member) -> None:
        await interaction.response.send_message(f"{member.mention} has {self.warn_counts[interaction.guild_id].get(member.id, 0)}/{MAX_WARNS} warning(s).", ephemeral=True)

    @app_commands.command(name="clearwarns", description="Reset a member's warning count.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clearwarns(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if not self._sensitive(interaction):
            await interaction.response.send_message("You are not on the admin command allowlist.", ephemeral=True); return
        self.warn_counts[interaction.guild_id][member.id] = 0
        await interaction.response.send_message(f"Cleared warnings for {member.mention}.", ephemeral=True)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoMod(bot))
