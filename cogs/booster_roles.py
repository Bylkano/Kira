import asyncio
import logging
import re
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import store

log = logging.getLogger("kira.booster_roles")
HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
CUSTOM_EMOJI_RE = re.compile(r"^<(a?):([A-Za-z0-9_]+):(\d+)>$")
PRESETS = (("Ruby", "#EF4444"), ("Coral", "#F97316"), ("Amber", "#F59E0B"), ("Lime", "#84CC16"), ("Emerald", "#10B981"), ("Teal", "#14B8A6"), ("Sky", "#0EA5E9"), ("Blue", "#3B82F6"), ("Indigo", "#6366F1"), ("Violet", "#8B5CF6"), ("Fuchsia", "#D946EF"), ("Pink", "#EC4899"), ("Rose", "#F43F5E"), ("Gold", "#EAB308"), ("Mint", "#2DD4BF"), ("Cyan", "#06B6D4"), ("Lavender", "#A78BFA"), ("Slate", "#64748B"), ("White", "#F8FAFC"), ("Black", "#111827"))
ROLE_ICON_FEATURE = "ROLE_ICONS"
DEFAULT_COLOR = "#5865F2"


def parse_hex(value: str) -> tuple[str, int] | None:
    match = HEX_RE.fullmatch(value.strip())
    if not match: return None
    normalized = f"#{match.group(1).upper()}"
    return normalized, int(match.group(1), 16)


def parse_custom_emoji(value: str) -> dict[str, Any] | None:
    match = CUSTOM_EMOJI_RE.fullmatch(value.strip())
    if not match: return None
    return {"id": int(match.group(3)), "name": match.group(2), "animated": bool(match.group(1))}


def emoji_markup(icon: dict[str, Any] | None) -> str:
    if not icon or not icon.get("id") or not icon.get("name"): return "No icon set"
    return f"<{('a' if icon.get('animated') else '')}:{icon['name']}:{icon['id']}>"


def record_icon(record: dict | None) -> dict[str, Any] | None:
    if not record or not record.get("icon_emoji_id") or not record.get("icon_emoji_name"): return None
    return {"id": record["icon_emoji_id"], "name": record["icon_emoji_name"], "animated": bool(record.get("icon_animated"))}


def normalize_role_name(value: str) -> str | None:
    name = " ".join(value.split())
    if not name or len(name) > 100: return None
    return name


class BoosterRoles(commands.Cog):
    boosterole = app_commands.Group(name="boosterole", description="Booster role menu")

    def __init__(self, bot: commands.Bot) -> None: self.bot = bot
    async def cog_load(self) -> None: self.cleanup_expired.start()
    def cog_unload(self) -> None: self.cleanup_expired.cancel()

    def _is_booster(self, interaction: discord.Interaction) -> bool:
        booster_role = interaction.guild.premium_subscriber_role if interaction.guild else None
        return bool(booster_role and isinstance(interaction.user, discord.Member) and booster_role in interaction.user.roles)

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        return config.can_use_admin_commands(interaction.user.id, interaction.guild)

    async def _require_access(self, interaction: discord.Interaction) -> bool:
        if self._is_booster(interaction) or self._is_admin(interaction): return True
        await interaction.response.send_message(embed=discord.Embed(title="Access required", description="You need to be boosting this server, or be a server administrator, to use the booster role menu.", color=discord.Color.red()), ephemeral=True)
        return False

    def _existing_role(self, guild: discord.Guild, member: discord.Member) -> tuple[discord.Role | None, dict | None]:
        record = store.get_booster_role(guild.id, member.id)
        role = guild.get_role(record["role_id"]) if record else None
        if record and not role:
            store.delete_booster_role(guild.id, member.id)
            return None, None
        return role, record

    @staticmethod
    def _jailed_role(guild: discord.Guild) -> discord.Role | None:
        matches = [role for role in guild.roles if role.name.casefold() == "jailed"]
        return max(matches, key=lambda role: role.position) if matches else None

    async def _position_role(self, guild: discord.Guild, role: discord.Role) -> str | None:
        jailed = self._jailed_role(guild)
        if not jailed: return "I could not find a role named Jailed. Create that role, or rename your jail role to Jailed, so I can place booster roles under it."
        top = guild.me.top_role if guild.me else None
        if not top or jailed >= top: return "Move my highest role above Jailed so I can place booster roles under it."
        try:
            await role.move(above=jailed, reason="Position Kira booster role under the Jailed role")
            return None
        except discord.Forbidden: return "I need Manage Roles, and my highest role must be above Jailed and the new booster role."
        except discord.HTTPException as exc:
            if exc.status == 429: log.warning("Rate limited while positioning booster role"); return "Discord is rate-limiting role changes. Please try again shortly."
            log.warning("Could not position booster role: %s", exc); return "Discord rejected the booster role change."
        except ValueError:
            return "I could not place that booster role under Jailed."

    async def _add_role(self, member: discord.Member, role: discord.Role) -> str | None:
        try:
            if role not in member.roles: await member.add_roles(role, reason="Apply Kira booster customization")
            return None
        except discord.Forbidden: return "I could not assign the role. Check that my highest role is above the personal role."
        except discord.HTTPException as exc:
            if exc.status == 429: log.warning("Rate limited while assigning booster role"); return "Discord is rate-limiting role changes. Please try again shortly."
            log.warning("Could not assign booster role: %s", exc); return "Discord rejected the role assignment."

    async def _create_role(self, member: discord.Member, name: str) -> tuple[bool, str]:
        role, record = self._existing_role(member.guild, member)
        if role: return False, f"You already have a booster role named **{role.name}**. Use Rename to change it."
        try:
            role = await member.guild.create_role(name=name, permissions=discord.Permissions.none(), mentionable=False, hoist=False, reason=f"Kira booster role for {member} ({member.id})")
        except discord.Forbidden: return False, "I need Manage Roles, and my highest role must be above Jailed."
        except discord.HTTPException as exc:
            if exc.status == 429: log.warning("Rate limited while creating booster role"); return False, "Discord is rate-limiting role changes. Please try again shortly."
            log.warning("Could not create booster role: %s", exc); return False, "Discord rejected the booster role change."
        error = await self._position_role(member.guild, role)
        if error:
            try: await role.delete(reason="Kira booster role create failed during positioning")
            except discord.HTTPException: pass
            return False, error
        error = await self._add_role(member, role)
        if error:
            try: await role.delete(reason="Kira booster role create failed during assignment")
            except discord.HTTPException: pass
            return False, error
        store.upsert_booster_role(member.guild.id, member.id, role.id, "solid", DEFAULT_COLOR, None, None, None, None)
        return True, f"Created your booster role **{role.name}**."

    async def _rename_role(self, member: discord.Member, name: str) -> tuple[bool, str]:
        role, record = self._existing_role(member.guild, member)
        if not role: return False, "Create your booster role first from the menu."
        try:
            updated = await role.edit(name=name, reason=f"Rename booster role for {member} ({member.id})")
            role = updated or role
        except discord.Forbidden: return False, "I need Manage Roles, and my highest role must be above your personal role."
        except discord.HTTPException as exc:
            if exc.status == 429: log.warning("Rate limited while renaming booster role"); return False, "Discord is rate-limiting role changes. Please try again shortly."
            log.warning("Could not rename booster role: %s", exc); return False, "Discord rejected that role name."
        return True, f"Renamed your booster role to **{role.name}**."

    async def _apply_color(self, member: discord.Member, color_type: str, primary: str, secondary: str | None) -> tuple[bool, str]:
        if color_type == "gradient" and member.guild.premium_tier < 3: return False, "Gradient roles require Boost Level 3 on this server."
        role, old = self._existing_role(member.guild, member)
        if not role: return False, "Create your booster role first from the menu."
        primary_value = parse_hex(primary)[1]
        secondary_value = parse_hex(secondary)[1] if secondary else None
        try:
            if color_type == "gradient": updated = await role.edit(color=discord.Color(primary_value), secondary_color=discord.Color(secondary_value), reason=f"Set booster gradient for {member} ({member.id})")
            else: updated = await role.edit(color=discord.Color(primary_value), secondary_color=None, reason=f"Set booster color for {member} ({member.id})")
            role = updated or role
        except discord.Forbidden: return False, "I need Manage Roles, and my highest role must be above your personal role."
        except discord.HTTPException as exc:
            if exc.status == 429: log.warning("Rate limited while editing booster role color"); return False, "Discord is rate-limiting role changes. Please try again shortly."
            log.warning("Could not edit booster role color: %s", exc); return False, "Discord rejected that role color."
        error = await self._add_role(member, role)
        if error: return False, error
        icon = record_icon(old)
        store.upsert_booster_role(member.guild.id, member.id, role.id, color_type, primary, secondary if color_type == "gradient" else None, icon["id"] if icon else None, icon["name"] if icon else None, icon["animated"] if icon else None)
        return True, "Your color is set!"

    async def _download_icon(self, icon: dict[str, Any]) -> tuple[bytes | None, str | None]:
        if icon["animated"]: return None, "Animated emojis cannot be used as role icons because role icons must be static."
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(f"https://cdn.discordapp.com/emojis/{icon['id']}.png") as response:
                    if response.status == 404: return None, "That custom emoji was not found. It may have been deleted."
                    if response.status != 200: return None, "Discord could not download that custom emoji."
                    return await response.read(), None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("Could not fetch emoji %s: %s", icon["id"], exc); return None, "I could not reach Discord's emoji CDN. Please try again."

    async def _apply_icon(self, member: discord.Member, icon: dict[str, Any]) -> tuple[bool, str]:
        if ROLE_ICON_FEATURE not in member.guild.features: return False, "Role icons are not available on this server yet; Boost Level 2 is required."
        raw, error = await self._download_icon(icon)
        if error or raw is None: return False, error or "I could not download that emoji."
        role, old = self._existing_role(member.guild, member)
        if not role: return False, "Create your booster role first from the menu."
        try:
            updated = await role.edit(display_icon=raw, reason=f"Set booster role icon for {member} ({member.id})")
            role = updated or role
        except discord.Forbidden: return False, "I need Manage Roles, and my highest role must be above your personal role."
        except discord.HTTPException as exc:
            if exc.status == 429: log.warning("Rate limited while editing booster role icon"); return False, "Discord is rate-limiting role changes. Please try again shortly."
            log.warning("Could not edit booster role icon: %s", exc); return False, "Discord rejected that role icon."
        error = await self._add_role(member, role)
        if error: return False, error
        old = old or {}
        store.upsert_booster_role(member.guild.id, member.id, role.id, old.get("color_type", "solid"), old.get("color_primary", DEFAULT_COLOR), old.get("color_secondary"), icon["id"], icon["name"], False)
        return True, "Your icon is set!"

    async def _remove(self, member: discord.Member) -> tuple[bool, str]:
        role, record = self._existing_role(member.guild, member)
        if not record and not role: return True, "Your booster role was already removed."
        if role:
            try: await role.delete(reason=f"Remove booster role for {member} ({member.id})")
            except discord.NotFound: pass
            except discord.Forbidden: return False, "I need Manage Roles to remove your personal role."
            except discord.HTTPException as exc:
                if exc.status == 429: log.warning("Rate limited while deleting booster role"); return False, "Discord is rate-limiting role changes. Please try again shortly."
                log.warning("Could not delete booster role: %s", exc); return False, "Discord rejected the role deletion."
        store.delete_booster_role(member.guild.id, member.id)
        return True, "Your booster role was deleted."

    def _preview_embed(self, member: discord.Member, record: dict | None, pending: dict | None = None, title: str = "Booster role menu") -> discord.Embed:
        state = dict(record or {})
        if pending: state.update(pending)
        primary = state.get("color_primary", DEFAULT_COLOR)
        parsed = parse_hex(primary) or (DEFAULT_COLOR, 0x5865F2)
        color_type, secondary = state.get("color_type", "solid"), state.get("color_secondary")
        icon = {"id": state.get("icon_emoji_id"), "name": state.get("icon_emoji_name"), "animated": state.get("icon_animated")} if state.get("icon_emoji_id") and state.get("icon_emoji_name") else None
        role = member.guild.get_role(record["role_id"]) if record else None
        description = f"**{member.display_name}**\nRole: **{role.name}**" if role else f"**{member.display_name}**\nRole: not created yet"
        description += f"\n\nColor: {primary}" if color_type == "solid" else f"\n\nGradient: {primary} → {secondary}"
        description += f"\nIcon: {emoji_markup(icon)}"
        embed = discord.Embed(title=title, description=description, color=discord.Color(parsed[1]))
        embed.set_footer(text="Your personal role has no permissions.")
        return embed

    @boosterole.command(name="menu", description="Booster role menu")
    @app_commands.guild_only()
    async def menu(self, interaction: discord.Interaction) -> None:
        if not await self._require_access(interaction): return
        view = ColorMenuView(self, interaction.user)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @tasks.loop(hours=1)
    async def cleanup_expired(self) -> None:
        for record in store.get_expired_booster_roles(3):
            guild = self.bot.get_guild(record["guild_id"])
            if not guild: continue
            role = guild.get_role(record["role_id"])
            if role:
                try: await role.delete(reason="Kira booster role cleanup after 3 days without boosting")
                except discord.NotFound: pass
                except discord.Forbidden: log.warning("Missing permission to clean up booster role %s", role.id); continue
                except discord.HTTPException as exc:
                    if exc.status == 429: log.warning("Rate limited during booster cleanup")
                    else: log.warning("Could not clean up booster role %s: %s", role.id, exc)
                    continue
            store.delete_booster_role(record["guild_id"], record["user_id"])
            log.info("Cleaned up booster role %s for user %s", record["role_id"], record["user_id"])

    @cleanup_expired.before_loop
    async def before_cleanup(self) -> None: await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.premium_since and not after.premium_since: store.mark_boosting_stopped(after.guild.id, after.id)
        elif not before.premium_since and after.premium_since: store.clear_boosting_stopped(after.guild.id, after.id)


class HexModal(discord.ui.Modal):
    def __init__(self, view: "ColorMenuView", target: str) -> None:
        super().__init__(title="Choose a custom hex color")
        self.view_ref, self.target = view, target
        self.value = discord.ui.TextInput(label="Hex color", placeholder="#5865F2", min_length=6, max_length=7, required=True)
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        parsed = parse_hex(str(self.value))
        if not parsed:
            await interaction.response.send_message("Use a six-digit hex color such as #5865F2.", ephemeral=True); return
        await interaction.response.defer()
        if self.target == "solid": self.view_ref.pending = {"color_type": "solid", "color_primary": parsed[0], "color_secondary": None}; self.view_ref.mode = "confirm"
        elif self.target == "gradient_first": self.view_ref.pending["color_primary"] = parsed[0]; self.view_ref.mode = "gradient_second"
        else: self.view_ref.pending["color_secondary"] = parsed[0]; self.view_ref.pending["color_type"] = "gradient"; self.view_ref.mode = "confirm"
        self.view_ref.rebuild()
        await self.view_ref.message.edit(embed=self.view_ref.embed(), view=self.view_ref)


class RoleNameModal(discord.ui.Modal):
    def __init__(self, view: "ColorMenuView", action: str) -> None:
        super().__init__(title="Create booster role" if action == "create" else "Rename booster role")
        self.view_ref, self.action = view, action
        current = ""
        if action == "rename":
            record = store.get_booster_role(view.user.guild.id, view.user.id)
            role = view.user.guild.get_role(record["role_id"]) if record else None
            current = role.name if role else ""
        self.value = discord.ui.TextInput(label="Role name", placeholder="e.g. Ruby Booster", default=current or None, min_length=1, max_length=100, required=True)
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = normalize_role_name(str(self.value))
        if not name:
            await interaction.response.send_message("Enter a role name between 1 and 100 characters.", ephemeral=True); return
        await interaction.response.defer()
        if self.action == "create": ok, text = await self.view_ref.cog._create_role(self.view_ref.user, name)
        else: ok, text = await self.view_ref.cog._rename_role(self.view_ref.user, name)
        self.view_ref.pending = {}
        self.view_ref.mode = "root"
        self.view_ref.set_status(text, ok)
        self.view_ref.rebuild()
        await self.view_ref.message.edit(content=None, embed=self.view_ref.embed(), view=self.view_ref)


class ColorMenuView(discord.ui.View):
    def __init__(self, cog: BoosterRoles, user: discord.Member) -> None:
        super().__init__(timeout=180)
        self.cog, self.user = cog, user
        self.message: discord.InteractionMessage | None = None
        self.mode, self.pending = "root", {}
        self.status: str | None = None
        self.status_ok: bool | None = None
        self.rebuild()

    def set_status(self, text: str | None, ok: bool | None = None) -> None:
        self.status, self.status_ok = text, ok

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("This isn't your booster role menu.", ephemeral=True); return False
        return True

    def embed(self) -> discord.Embed:
        record = store.get_booster_role(self.user.guild.id, self.user.id)
        title = "Booster role menu"
        if self.mode == "gradient_second": title = "Choose the second gradient color"
        elif self.mode == "icon_prompt": title = "Set a custom emoji icon"
        elif self.mode == "confirm": title = "Delete this booster role?" if self.pending.get("action") == "remove" else "Review your change"
        elif self.mode == "expired": title = "Booster role menu expired"
        embed = self.cog._preview_embed(self.user, record, self.pending, title)
        if self.status: embed.description = f"{self.status}\n\n" + (embed.description or "")
        if self.status_ok is True: embed.color = discord.Color.green()
        elif self.status_ok is False: embed.color = discord.Color.red()
        if self.mode == "gradient_first": embed.description += "\n\nChoose the first gradient color."
        if self.mode == "gradient_second": embed.description += "\n\nFirst color selected. Choose the second color."
        if self.mode == "root": embed.description += "\n\nCreate or rename your role, delete it, or customize its color and icon."
        return embed

    def rebuild(self) -> None:
        self.clear_items()
        if self.mode == "root":
            select = discord.ui.Select(placeholder="Choose an action", options=[
                discord.SelectOption(label="Create Role", value="create", description="Create your booster role and choose its name"),
                discord.SelectOption(label="Rename Role", value="rename", description="Change the name of your booster role"),
                discord.SelectOption(label="Delete Role", value="delete", description="Remove your personal booster role"),
                discord.SelectOption(label="Solid Color", value="solid", description="Pick one color"),
                discord.SelectOption(label="Gradient", value="gradient", description="Blend two colors; Level 3"),
                discord.SelectOption(label="Set Icon", value="icon", description="Use a static custom emoji"),
            ])
            select.callback = self.choose; self.add_item(select); return
        if self.mode in {"solid", "gradient_first", "gradient_second"}:
            for index, (label, value) in enumerate(PRESETS):
                button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, row=index // 5); button.callback = self._preset_callback(value); self.add_item(button)
            custom = discord.ui.Button(label="Custom Hex", style=discord.ButtonStyle.primary, row=4); custom.callback = self.custom; self.add_item(custom)
        if self.mode == "icon_prompt":
            back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary); back.callback = self.back; self.add_item(back); return
        if self.mode == "confirm":
            confirm = discord.ui.Button(label="Confirm", style=discord.ButtonStyle.success); confirm.callback = self.confirm; self.add_item(confirm)
        back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, row=4); back.callback = self.back; self.add_item(back)

    def _preset_callback(self, value: str):
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            if self.mode == "solid": self.pending = {"color_type": "solid", "color_primary": value, "color_secondary": None}; self.mode = "confirm"
            elif self.mode == "gradient_first": self.pending["color_primary"] = value; self.pending["color_type"] = "gradient"; self.mode = "gradient_second"
            else: self.pending["color_secondary"] = value; self.pending["color_type"] = "gradient"; self.mode = "confirm"
            self.set_status(None); self.rebuild(); await self.message.edit(embed=self.embed(), view=self)
        return callback

    async def choose(self, interaction: discord.Interaction) -> None:
        choice = interaction.data["values"][0]
        role, _record = self.cog._existing_role(self.user.guild, self.user)
        if choice == "create":
            if role:
                await interaction.response.defer()
                self.set_status(f"You already have a booster role named **{role.name}**. Use Rename to change it.", False)
                self.rebuild(); await self.message.edit(content=None, embed=self.embed(), view=self); return
            await interaction.response.send_modal(RoleNameModal(self, "create")); return
        if choice == "rename":
            if not role:
                await interaction.response.defer()
                self.set_status("Create your booster role first, then you can rename it.", False)
                self.rebuild(); await self.message.edit(content=None, embed=self.embed(), view=self); return
            await interaction.response.send_modal(RoleNameModal(self, "rename")); return
        await interaction.response.defer()
        if choice in {"delete", "solid", "gradient", "icon"} and not role:
            self.set_status("Create your booster role first, then you can customize or delete it.", False)
            self.rebuild(); await self.message.edit(content=None, embed=self.embed(), view=self); return
        self.set_status(None)
        if choice == "solid": self.mode = "solid"
        elif choice == "gradient": self.mode = "gradient_first"
        elif choice == "icon":
            self.mode = "icon_prompt"; self.rebuild()
            await self.message.edit(content="Paste a static custom emoji in the chat, for example <:sparkles:123456789012345678>. Unicode emoji and attachments are not supported. I will show a preview before applying it.", embed=self.embed(), view=self)
            await self.wait_for_icon(); return
        else: self.pending = {"action": "remove"}; self.mode = "confirm"
        self.rebuild(); await self.message.edit(content=None, embed=self.embed(), view=self)

    async def custom(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(HexModal(self, "solid" if self.mode == "solid" else "gradient_first" if self.mode == "gradient_first" else "gradient_second"))

    async def wait_for_icon(self) -> None:
        while not self.is_finished() and self.mode == "icon_prompt":
            try: message = await self.cog.bot.wait_for("message", timeout=180, check=lambda item: item.author.id == self.user.id and item.guild and item.guild.id == self.user.guild.id and self.mode == "icon_prompt")
            except asyncio.TimeoutError: await self.on_timeout(); return
            parsed = parse_custom_emoji(message.content)
            if parsed and parsed["animated"]:
                if self.message: await self.message.edit(content="Animated emojis cannot be role icons. Paste a static custom emoji in <:name:id> format, or use Back.", embed=self.embed(), view=self)
                continue
            if not parsed:
                if self.message: await self.message.edit(content="That is not a static custom emoji. Paste it in <:name:id> format, or use Back.", embed=self.embed(), view=self)
                continue
            self.pending = {"action": "icon", "icon_emoji_id": parsed["id"], "icon_emoji_name": parsed["name"], "icon_animated": parsed["animated"]}; self.mode = "confirm"; self.rebuild()
            if self.message: await self.message.edit(content=f"Emoji received: {emoji_markup(parsed)}. Review the preview, then confirm.", embed=self.embed(), view=self)

    async def back(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(); self.pending = {}; self.mode = "root"; self.set_status(None); self.rebuild(); await self.message.edit(content=None, embed=self.embed(), view=self)

    async def confirm(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        if self.pending.get("action") == "remove": ok, text = await self.cog._remove(self.user)
        elif self.pending.get("action") == "icon": ok, text = await self.cog._apply_icon(self.user, {"id": self.pending["icon_emoji_id"], "name": self.pending["icon_emoji_name"], "animated": self.pending["icon_animated"]})
        elif self.pending.get("color_type") == "gradient": ok, text = await self.cog._apply_color(self.user, "gradient", self.pending["color_primary"], self.pending["color_secondary"])
        else: ok, text = await self.cog._apply_color(self.user, "solid", self.pending["color_primary"], None)
        if ok:
            self.pending = {}
            self.mode = "root"
        self.set_status(text, ok)
        self.rebuild()
        await self.message.edit(content=None, embed=self.embed(), view=self)

    async def on_timeout(self) -> None:
        self.mode = "expired"; self.rebuild()
        for child in self.children: child.disabled = True
        if self.message: await self.message.edit(content="This booster role menu expired. Run /boosterole menu to start again.", embed=self.embed(), view=self)
        self.stop()


async def setup(bot: commands.Bot) -> None: await bot.add_cog(BoosterRoles(bot))
