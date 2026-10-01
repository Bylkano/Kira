import datetime
import logging
import re
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


def contains_exact_phrase(content: str, phrase: str) -> bool:
    """Return True only when phrase appears as a whole word/phrase, not a substring."""
    pattern = rf"(?<!\w){re.escape(phrase.casefold())}(?!\w)"
    return re.search(pattern, content.casefold()) is not None


class AutoMod(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.warn_counts: dict[int, dict[int, int]] = defaultdict(dict)

    def _sensitive(self, interaction: discord.Interaction) -> bool:
        return config.can_use_admin_commands(interaction.user.id, interaction.guild)

    async def _timeout(self, member: discord.Member, minutes: int, reason: str) -> None:
        try:
            await member.timeout(datetime.timedelta(minutes=minutes), reason=reason)
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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or not message.content: return

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
