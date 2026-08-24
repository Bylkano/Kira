import logging
import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("kira.moderation")

class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None: self.bot = bot

    async def _set_lock(self, interaction: discord.Interaction, channel: discord.TextChannel, locked: bool, reason: str | None) -> None:
        audit_reason = f"{reason or ('Channel locked' if locked else 'Channel unlocked')} — by {interaction.user} ({interaction.user.id})"
        try:
            overwrite = channel.overwrites_for(interaction.guild.default_role)
            overwrite.send_messages = False if locked else None
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=audit_reason)
        except discord.Forbidden:
            await interaction.response.send_message("I need Manage Channels and a role positioned above the members being restricted.", ephemeral=True); return
        except discord.HTTPException as exc:
            if exc.status == 429: log.warning("Rate limited while changing channel lock")
            await interaction.response.send_message("Discord rejected or rate-limited the channel permission change.", ephemeral=True); return
        action = "locked" if locked else "unlocked"
        embed = discord.Embed(title=f"Channel {action}", description=f"{channel.mention} was {action}.", color=discord.Color.red() if locked else discord.Color.green())
        if reason: embed.add_field(name="Reason", value=reason, inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="lock", description="Prevent @everyone from sending messages in a channel.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None, reason: str | None = None) -> None:
        await self._set_lock(interaction, channel or interaction.channel, True, reason)

    @app_commands.command(name="unlock", description="Allow channel permissions to inherit again.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None, reason: str | None = None) -> None:
        await self._set_lock(interaction, channel or interaction.channel, False, reason)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
