"""Owner/mod utilities: /announce and /status. Deterministic, gated, audited."""

from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from spiderbot import __version__, audit

log = logging.getLogger("spiderbot.admin")
_START = time.time()


class AdminCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.cfg = bot.cfg

    @app_commands.command(name="announce", description="Post an announcement in #announcements")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        message="The announcement text",
        ping_testers="Also ping the Slingy Tester role",
    )
    async def announce(
        self, interaction: discord.Interaction, message: str, ping_testers: bool = False
    ) -> None:
        channel = self.bot.channels.get("announcements")
        if channel is None:
            await interaction.response.send_message("#announcements not found.", ephemeral=True)
            return
        role = discord.utils.get(interaction.guild.roles, name=self.cfg.tester_role_name)
        content = message[:1800]
        mentions = discord.AllowedMentions.none()
        if ping_testers and role is not None:
            content = f"{role.mention} {content}"
            mentions = discord.AllowedMentions(roles=[role])
        await channel.send(content, allowed_mentions=mentions)
        await interaction.response.send_message("Announced.", ephemeral=True)
        audit.stdout_event("announce", by=str(interaction.user), ping_testers=ping_testers)

    @app_commands.command(name="status", description="Spider Bot health and configuration")
    @app_commands.default_permissions(manage_guild=True)
    async def status(self, interaction: discord.Interaction) -> None:
        cfg = self.cfg
        uptime_min = int((time.time() - _START) / 60)
        ai = "on" if self.bot.ai.enabled else "OFF (no key or AI_ENABLED=false)"
        lines = [
            f"Spider Bot v{__version__} - up {uptime_min} min",
            f"AI: {ai} | model `{cfg.ai_model}` | effort `{cfg.ai_effort}`",
            f"Initiative channels: {', '.join(cfg.initiative_channels) or '(none)'} "
            f"| cooldown {cfg.initiative_cooldown_s}s | cap {cfg.initiative_hourly_cap}/h",
            f"Resolved channels: {', '.join(sorted(self.bot.channels)) or '(none)'}",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(AdminCog(bot))
