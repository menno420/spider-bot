"""Community funnel: /jointest, /feedback, in-channel welcome, opted-in watcher.

Everything here is deterministic and works with AI disabled. The bot never
DMs anyone (server rule 4 applies to the bot too) and never grants the tester
role automatically - the roster must mirror the real Play cohort.
"""

from __future__ import annotations

import contextlib
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from spiderbot import audit, presets, style
from spiderbot.ui.forms import FeedbackModal

log = logging.getLogger("spiderbot.community")

_OPTED_IN = re.compile(r"\bopt(?:ed)?[ -]?in\b", re.IGNORECASE)


class CommunityCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.cfg = bot.cfg

    @app_commands.command(name="jointest", description="How to join the Slingy Spider closed test")
    async def jointest(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=presets.steps_embed(self.cfg), ephemeral=True)
        audit.stdout_event("jointest_used", user=str(interaction.user))

    @app_commands.command(name="feedback", description="Send feedback about Slingy Spider")
    async def feedback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(FeedbackModal(self.bot))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot or member.guild.id != self.cfg.guild_id:
            return
        general = self.bot.channels.get("general")
        start_here = self.bot.channels.get("start-here")
        if general is None:
            return
        where = start_here.mention if start_here else "#start-here"
        try:
            await general.send(
                f"Welcome to the web, {member.mention}! :spider_web: "
                f"Everything you need to become a Slingy Spider tester is pinned in "
                f"{where} - or just type `/jointest`. Questions? Ask right here.",
                allowed_mentions=discord.AllowedMentions(users=[member]),
            )
        except discord.HTTPException:
            log.exception("welcome message failed")
        audit.stdout_event("member_join", user=str(member))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Opted-in watcher: react + surface to mod-log. Never auto-grants."""
        if (
            message.guild is None
            or message.author.bot
            or message.channel.name != self.cfg.ch_general
            or not _OPTED_IN.search(message.content or "")
        ):
            return
        member = message.author
        has_role = any(r.name == self.cfg.tester_role_name for r in getattr(member, "roles", []))
        if has_role:
            return
        with contextlib.suppress(discord.HTTPException):
            await message.add_reaction("\N{SPIDER}")
        await audit.modlog_event(
            self.bot.channels.get("mod-log"),
            "Possible new tester",
            f"{member.display_name} said \"opted in\" in #{self.cfg.ch_general}.\n"
            f"Verify the opted-in count moved in Play Console, then run "
            f"`/tester add` with user `{member.display_name}`.",
            style.WARNING,
        )
        audit.stdout_event("opted_in_claim", user=str(member))


async def setup(bot) -> None:
    await bot.add_cog(CommunityCog(bot))
