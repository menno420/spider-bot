"""Community funnel: /jointest, /feedback, in-channel welcome, opted-in watcher.

Everything here is deterministic and works with AI disabled. The bot never
DMs anyone (server rule 4 applies to the bot too) and never grants the tester
role automatically - the roster must mirror the real Play cohort.
"""

from __future__ import annotations

import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from spiderbot import audit

log = logging.getLogger("spiderbot.community")

_OPTED_IN = re.compile(r"\bopt(?:ed)?[ -]?in\b", re.IGNORECASE)


def _steps_embed(cfg) -> discord.Embed:
    e = discord.Embed(
        title="Become a Slingy Spider tester",
        description=(
            "Four steps, ~3 minutes. **Use the same Google account everywhere** - "
            "a different account is the #1 reason joining silently fails.\n\n"
            "**Step 0** - Check which Google account your phone's Play Store uses: "
            "Play Store -> your profile picture (top right).\n"
            f"**Step 1** - [Join the tester group]({cfg.group_url}) - click *Join group*.\n"
            "**Step 2** - Wait ~15 minutes, then open the opt-in page signed into "
            f"that same account and tap **Become a tester**: [opt-in page]({cfg.optin_url})\n"
            "**Step 3** - Install Slingy Spider from the Play link the page shows.\n\n"
            f"Then post *\"opted in\"* in #{cfg.ch_general} and Menno will give you "
            "the **Slingy Tester** role once your opt-in is verified.\n\n"
            "*Trouble? \"App not available\" almost always means the wrong Google "
            "account, or the group join has not caught up yet - wait an hour and "
            "retry with the Step-0 account.*"
        ),
        color=discord.Color.green(),
    )
    e.set_footer(text="Stay opted in for the full 14 days - it protects everyone's progress.")
    return e


class FeedbackModal(discord.ui.Modal, title="Slingy Spider feedback"):
    summary = discord.ui.TextInput(
        label="One-line summary", max_length=90, placeholder="e.g. Web-swing feels floaty on level 3"
    )
    details = discord.ui.TextInput(
        label="Details",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        placeholder="What happened / what you expected / device and Android version if relevant",
    )

    def __init__(self, bot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        forum = self.bot.channels.get("feedback")
        body = (
            f"{self.details.value}\n\n*Submitted by {interaction.user.display_name} "
            f"via /feedback*"
        )
        if isinstance(forum, discord.ForumChannel):
            thread = await forum.create_thread(
                name=str(self.summary.value)[:95],
                content=body[:1900],
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await interaction.response.send_message(
                f"Thank you! Your feedback is posted: {thread.thread.mention}", ephemeral=True
            )
        else:  # degrade: feedback channel missing or not a forum
            target = self.bot.channels.get("mod-log")
            if target is not None:
                await target.send(
                    f"Feedback from {interaction.user.display_name}: "
                    f"**{self.summary.value}**\n{body}"[:1900],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            await interaction.response.send_message(
                "Thank you! Your feedback reached the team.", ephemeral=True
            )
        audit.stdout_event("feedback_submitted", user=str(interaction.user))


class CommunityCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.cfg = bot.cfg

    @app_commands.command(name="jointest", description="How to join the Slingy Spider closed test")
    async def jointest(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=_steps_embed(self.cfg), ephemeral=True)
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
        try:
            await message.add_reaction("\N{SPIDER}")
        except discord.HTTPException:
            pass
        await audit.modlog_event(
            self.bot.channels.get("mod-log"),
            "Possible new tester",
            f"{member.display_name} said \"opted in\" in #{self.cfg.ch_general}.\n"
            f"Verify the opted-in count moved in Play Console, then run "
            f"`/tester add` with user `{member.display_name}`.",
            discord.Color.green(),
        )
        audit.stdout_event("opted_in_claim", user=str(member))


async def setup(bot) -> None:
    await bot.add_cog(CommunityCog(bot))
