"""The `/home` front door and the pinnable panel.

Two entry points, one factory: `/home` opens a private panel for whoever ran
it, `/panel` drops a permanent public one in a channel (for #start-here, so a
new arrival never has to know a command exists). Both render from the same
route registry, so they cannot drift.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from spiderbot import audit
from spiderbot.ui.home import build_home, build_pinned_home
from spiderbot.ui.routes import audience_for

log = logging.getLogger("spiderbot.home")


class HomeCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.cfg = bot.cfg

    @app_commands.command(
        name="home", description="Everything Spider Bot can do - one press away"
    )
    async def home(self, interaction: discord.Interaction) -> None:
        embed, panel = build_home(self.bot, interaction.user)
        await interaction.response.send_message(embed=embed, view=panel, ephemeral=True)
        panel.message = await interaction.original_response()
        audit.stdout_event(
            "home_opened",
            user=str(interaction.user),
            audience=audience_for(interaction.user, self.cfg).name,
        )

    @app_commands.command(
        name="panel", description="Post the permanent Spider Bot panel in this channel"
    )
    @app_commands.default_permissions(manage_guild=True)
    async def panel(self, interaction: discord.Interaction) -> None:
        embed, view = build_pinned_home(self.bot)
        await interaction.response.send_message(
            "Posting the panel here - it keeps working after restarts.", ephemeral=True
        )
        message = await interaction.channel.send(embed=embed, view=view)
        pinned = True
        try:
            await message.pin()
        except discord.HTTPException:  # no Pin Messages, or the pin list is full
            pinned = False
            log.warning("panel posted but could not be pinned in #%s", interaction.channel)
        if not pinned:
            await interaction.followup.send(
                "Posted, but I could not pin it - pin it yourself, or give me "
                "**Pin Messages** here.",
                ephemeral=True,
            )
        audit.stdout_event(
            "panel_posted",
            by=str(interaction.user),
            channel=getattr(interaction.channel, "name", "?"),
            pinned=pinned,
        )


async def setup(bot) -> None:
    await bot.add_cog(HomeCog(bot))
