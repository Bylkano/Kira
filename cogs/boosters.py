import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import store

log = logging.getLogger("kira.boosters")
TIER_BOOSTS = (0, 2, 7, 14)
BOOST_MESSAGE_TYPES = {discord.MessageType.premium_guild_subscription}


def _aware(value: datetime | None) -> datetime | None:
    if value is None: return None
    if value.tzinfo is None: return value.replace(tzinfo=timezone.utc)
    return value


def _timestamp(value: datetime | None) -> str:
    value = _aware(value)
    if not value: return "unknown"
    unix = int(value.timestamp())
    return f"<t:{unix}:D> (<t:{unix}:R>)"


def _duration(start: datetime | None, end: datetime | None = None) -> str:
    start = _aware(start)
    if not start: return "unknown"
    finish = _aware(end) or datetime.now(timezone.utc)
    seconds = max(int((finish - start).total_seconds()), 0)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days: parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours and days < 30: parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if not parts and minutes: parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if not parts: return "just now"
    return ", ".join(parts[:2])


def _boost_count_text(count: int | None) -> str:
    if not count or count <= 1: return "at least 1 boost"
    return f"at least {count} boosts"


def _next_level(tier: int, boosts: int) -> str:
    if tier >= 3: return "Max boost level"
    needed = TIER_BOOSTS[min(tier + 1, 3)]
    remaining = max(needed - boosts, 0)
    return f"{remaining} more to Level {tier + 1}"


class Boosters(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.sync_boosters.start()

    def cog_unload(self) -> None:
        self.sync_boosters.cancel()

    async def _guild_boosters(self, guild: discord.Guild) -> list[discord.Member]:
        if not guild.chunked:
            try: await guild.chunk()
            except discord.HTTPException as exc:
                if exc.status == 429: log.warning("Rate limited while loading members for booster tracking")
                else: log.warning("Could not chunk guild %s: %s", guild.id, exc)
        members = [member for member in guild.members if member.premium_since]
        members.sort(key=lambda member: member.premium_since or datetime.now(timezone.utc))
        return members

    def _sync_guild(self, guild: discord.Guild, members: list[discord.Member]) -> None:
        active_ids = []
        for member in members:
            if not member.premium_since: continue
            store.record_boost_start(guild.id, member.id, member.premium_since)
            active_ids.append(member.id)
        store.mark_missing_boosters_stopped(guild.id, active_ids)

    def _member_line(self, member: discord.Member | None, record: dict | None, live_since: datetime | None) -> str:
        name = member.display_name if member else f"User {record['user_id']}" if record else "Unknown member"
        started = live_since or (record.get("boosting_since") if record else None)
        count = record.get("boost_count") if record else 1
        return f"**{name}** — since {_timestamp(started)} • {_duration(started)} • {_boost_count_text(count)}"

    def _server_embed(self, guild: discord.Guild, members: list[discord.Member]) -> discord.Embed:
        boosts = guild.premium_subscription_count or 0
        tier = guild.premium_tier
        embed = discord.Embed(title=f"Boosters in {guild.name}", color=discord.Color.from_str("#F47FFF"))
        embed.description = (
            f"**Level {tier}** • **{boosts}** boost{'s' if boosts != 1 else ''} from **{len(members)}** booster{'s' if len(members) != 1 else ''}\n"
            f"{_next_level(tier, boosts)}"
        )
        records = {row["user_id"]: row for row in store.get_active_boost_trackers(guild.id)}
        if members:
            lines = [self._member_line(member, records.get(member.id), member.premium_since) for member in members[:20]]
            if len(members) > 20: lines.append(f"…and {len(members) - 20} more")
            embed.add_field(name="Currently boosting", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Currently boosting", value="Nobody is boosting this server right now.", inline=False)
        stopped = store.get_stopped_boost_trackers(guild.id, 5)
        if stopped:
            history = []
            for row in stopped:
                member = guild.get_member(row["user_id"])
                name = member.display_name if member else f"User {row['user_id']}"
                history.append(f"**{name}** — stopped {_timestamp(row['boosting_stopped_at'])} after {_duration(row['boosting_since'], row['boosting_stopped_at'])}")
            embed.add_field(name="Recently stopped", value="\n".join(history), inline=False)
        embed.set_footer(text="Discord does not tell bots the exact boost count per person. Counts are a lower bound from boost announcements Kira has seen.")
        return embed

    def _member_embed(self, guild: discord.Guild, member: discord.Member) -> discord.Embed:
        record = store.get_boost_tracker(guild.id, member.id)
        embed = discord.Embed(title=f"Boost status for {member.display_name}", color=discord.Color.from_str("#F47FFF"))
        if member.premium_since:
            store.record_boost_start(guild.id, member.id, member.premium_since)
            record = store.get_boost_tracker(guild.id, member.id) or record
            embed.description = f"{member.mention} is currently boosting this server."
            embed.add_field(name="Boosting since", value=_timestamp(member.premium_since), inline=False)
            embed.add_field(name="Duration", value=_duration(member.premium_since), inline=True)
            embed.add_field(name="Boosts seen", value=_boost_count_text(record["boost_count"] if record else 1), inline=True)
        elif record:
            embed.description = f"{member.mention} is not boosting right now."
            embed.add_field(name="Last boosted from", value=_timestamp(record["boosting_since"]), inline=False)
            embed.add_field(name="Stopped", value=_timestamp(record["boosting_stopped_at"]), inline=False)
            embed.add_field(name="Last streak", value=_duration(record["boosting_since"], record["boosting_stopped_at"]), inline=True)
            embed.add_field(name="Boosts seen", value=_boost_count_text(record["boost_count"]), inline=True)
        else:
            embed.description = f"{member.mention} is not boosting, and Kira has no boost history for them yet."
        color_role = store.get_booster_role(guild.id, member.id)
        if color_role:
            role = guild.get_role(color_role["role_id"])
            embed.add_field(name="Kira color role", value=role.mention if role else "Saved, but the role is missing", inline=False)
        embed.set_footer(text="Discord does not tell bots the exact boost count per person. Counts are a lower bound from boost announcements Kira has seen.")
        return embed

    @app_commands.command(name="boosters", description="Show who is boosting, since when, and server boost totals.")
    @app_commands.guild_only()
    @app_commands.describe(member="A member to inspect; omit for the full booster list")
    async def boosters(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True); return
        await interaction.response.defer()
        current = await self._guild_boosters(guild)
        self._sync_guild(guild, current)
        embed = self._member_embed(guild, member) if member else self._server_embed(guild, current)
        await interaction.followup.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if not before.premium_since and after.premium_since:
            store.record_boost_start(after.guild.id, after.id, after.premium_since)
        elif before.premium_since and not after.premium_since:
            store.mark_boost_stopped(after.guild.id, after.id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.premium_since: store.record_boost_start(member.guild.id, member.id, member.premium_since)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.premium_since: store.mark_boost_stopped(member.guild.id, member.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.type not in BOOST_MESSAGE_TYPES: return
        member = message.guild.get_member(message.author.id)
        started = member.premium_since if member else None
        if started and (discord.utils.utcnow() - started).total_seconds() > 15:
            store.increment_boost_count(message.guild.id, message.author.id, started)
        elif started:
            store.record_boost_start(message.guild.id, message.author.id, started)

    @tasks.loop(hours=1)
    async def sync_boosters(self) -> None:
        for guild in self.bot.guilds:
            try:
                members = await self._guild_boosters(guild)
                self._sync_guild(guild, members)
            except Exception:
                log.exception("Could not sync boosters for guild %s", guild.id)

    @sync_boosters.before_loop
    async def before_sync(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Boosters(bot))
