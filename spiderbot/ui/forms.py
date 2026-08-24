"""Modals - the typing-free intake forms behind Home's buttons.

`FeedbackModal` moved here from `cogs/community.py` unchanged in behaviour, so
the panel and the slash command open the *same* form (superbot's rule: one
factory per surface, every entry point shares it). `BugReportModal` is new and
asks for the four things that make an Android bug reproducible.

Both degrade: if the target forum is missing or is a plain text channel, the
report still reaches #mod-log rather than being lost.
"""

from __future__ import annotations

import logging

import discord

from spiderbot import audit

log = logging.getLogger("spiderbot.ui.forms")

NO_MENTIONS = discord.AllowedMentions.none()


async def _deliver(bot, channel_key: str, title: str, body: str, author) -> str:
    """Post a report to its forum, or fall back to #mod-log. Returns a receipt."""
    target = bot.channels.get(channel_key)
    if isinstance(target, discord.ForumChannel):
        created = await target.create_thread(
            name=title[:95], content=body[:1900], allowed_mentions=NO_MENTIONS
        )
        return f"Thank you! It is posted here: {created.thread.mention}"
    if target is not None:
        await target.send(f"**{title}**\n{body}"[:1900], allowed_mentions=NO_MENTIONS)
        return "Thank you! Your report reached the team."
    fallback = bot.channels.get("mod-log")
    if fallback is not None:
        await fallback.send(
            f"From {getattr(author, 'display_name', author)}: **{title}**\n{body}"[:1900],
            allowed_mentions=NO_MENTIONS,
        )
    return "Thank you! Your report reached the team."


class FeedbackModal(discord.ui.Modal, title="Slingy Spider feedback"):
    summary = discord.ui.TextInput(
        label="One-line summary",
        max_length=90,
        placeholder="e.g. Web-swing feels floaty on level 3",
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
        body = (
            f"{self.details.value}\n\n*Submitted by {interaction.user.display_name} "
            f"via the feedback form*"
        )
        receipt = await _deliver(
            self.bot, "feedback", str(self.summary.value), body, interaction.user
        )
        await interaction.response.send_message(receipt, ephemeral=True)
        audit.stdout_event("feedback_submitted", user=str(interaction.user))


class BugReportModal(discord.ui.Modal, title="Report a bug"):
    summary = discord.ui.TextInput(
        label="One-line summary",
        max_length=90,
        placeholder="e.g. Game freezes when I release the silk mid-swing",
    )
    device = discord.ui.TextInput(
        label="Phone and Android version",
        max_length=100,
        placeholder="e.g. Pixel 7a, Android 15",
    )
    details = discord.ui.TextInput(
        label="What happened, and what you expected",
        style=discord.TextStyle.paragraph,
        max_length=1200,
    )
    steps = discord.ui.TextInput(
        label="What you did just before it",
        style=discord.TextStyle.paragraph,
        max_length=800,
        required=False,
        placeholder="Smaller steps are better - it is how a bug gets reproduced.",
    )

    def __init__(self, bot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        steps = str(self.steps.value or "").strip() or "(not given)"
        body = (
            f"**Device:** {self.device.value}\n\n"
            f"**What happened**\n{self.details.value}\n\n"
            f"**Steps before it**\n{steps}\n\n"
            f"*Reported by {interaction.user.display_name} via the bug form*"
        )
        receipt = await _deliver(
            self.bot, "bug-reports", str(self.summary.value), body, interaction.user
        )
        await interaction.response.send_message(receipt, ephemeral=True)
        audit.stdout_event("bug_submitted", user=str(interaction.user))


class AskModal(discord.ui.Modal, title="Ask Spider Bot"):
    """A question for the AI, asked without needing to @-mention anything.

    The answer is ephemeral: a private answer cannot derail a public channel,
    and it keeps the AI's reply out of everyone else's notifications.
    """

    question = discord.ui.TextInput(
        label="Your question",
        style=discord.TextStyle.paragraph,
        max_length=500,
        placeholder="e.g. Why does the opt-in page say the app is not available?",
    )

    def __init__(self, bot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from spiderbot.ai import safety

        await interaction.response.defer(ephemeral=True, thinking=True)
        payload = "A member asked Spider Bot directly:" + safety.wrap_untrusted(
            str(self.question.value), kind="current_user_message"
        )
        result = await self.bot.ai.reply(payload, mode="mention")
        if result.text is None:
            await interaction.followup.send(
                "My web got tangled - I could not answer that just now. Ask in "
                "#general and Menno will see it.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(result.text[:1900], ephemeral=True)
        audit.stdout_event(
            "ai_decision",
            decision="replied" if result.text else "degraded",
            mode="ask_form",
            reason=result.reason.upper(),
            model=result.model,
            tokens_in=result.input_tokens,
            tokens_out=result.output_tokens,
        )
