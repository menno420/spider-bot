"""Tester-role management: deterministic, mod-gated, audited.

The AI never touches these paths. The Slingy Tester role mirrors the real
Play closed-test cohort, so grants happen only after a human verified the
opt-in in Play Console.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from spiderbot import audit

log = logging.getLogger("spiderbot.tester")


@app_commands.default_permissions(manage_roles=True)
class TesterGroup(app_commands.Group):
    """/tester add | remove | count"""

    def __init__(self, bot) -> None:
        super().__init__(name="tester", description="Manage the Slingy Tester roster")
        self.bot = bot

    def _role(self, guild: discord.Guild) -> discord.Role | None:
        return discord.utils.get(guild.roles, name=self.bot.cfg.tester_role_name)

    @app_commands.command(
        name="add", description="Grant the Slingy Tester role (after verifying the opt-in)"
    )
    async def add(self, interaction: discord.Interaction, user: discord.Member) -> None:
        role = self._role(interaction.guild)
        if role is None:
            await interaction.response.send_message("Tester role not found.", ephemeral=True)
            return
        await user.add_roles(role, reason=f"Verified tester, granted by {interaction.user}")
        count = sum(1 for m in interaction.guild.members if role in m.roles)
        await interaction.response.send_message(
            f"{user.display_name} is now a **{role.name}** - roster: **{count}** "
            f"(goal: 12+ opted in for 14 days).",
            ephemeral=True,
        )
        general = self.bot.channels.get("general")
        if general is not None:
            await general.send(
                f"\N{SPIDER} **{user.display_name}** joined the tester roster - "
                f"that makes **{count}**! Thank you!",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        await audit.modlog_event(
            self.bot.channels.get("mod-log"), "Tester granted",
            f"{user} granted by {interaction.user}. Roster now {count}.",
            discord.Color.green(),
        )
        audit.stdout_event("tester_granted", user=str(user), by=str(interaction.user), count=count)

    @app_commands.command(name="remove", description="Remove the Slingy Tester role")
    async def remove(self, interaction: discord.Interaction, user: discord.Member) -> None:
        role = self._role(interaction.guild)
        if role is None or role not in user.roles:
            await interaction.response.send_message("Nothing to remove.", ephemeral=True)
            return
        await user.remove_roles(role, reason=f"Removed by {interaction.user}")
        count = sum(1 for m in interaction.guild.members if role in m.roles)
        await interaction.response.send_message(
            f"Removed. Roster: **{count}**.", ephemeral=True
        )
        await audit.modlog_event(
            self.bot.channels.get("mod-log"), "Tester removed",
            f"{user} removed by {interaction.user}. Roster now {count}.",
            discord.Color.orange(),
        )
        audit.stdout_event("tester_removed", user=str(user), by=str(interaction.user), count=count)

    @app_commands.command(name="count", description="How many verified testers the roster holds")
    async def count(self, interaction: discord.Interaction) -> None:
        role = self._role(interaction.guild)
        count = 0 if role is None else sum(1 for m in interaction.guild.members if role in m.roles)
        await interaction.response.send_message(
            f"**{count}** verified tester(s). Google needs **12 opted in for 14 "
            f"continuous days**; recruit 15-16 so dropouts cannot reset the clock.",
            ephemeral=True,
        )


class TesterCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        bot.tree.add_command(TesterGroup(bot), guild=discord.Object(id=bot.cfg.guild_id))


async def setup(bot) -> None:
    await bot.add_cog(TesterCog(bot))
