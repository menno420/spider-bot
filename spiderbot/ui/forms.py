"""Modals - the typing-free intake forms behind Home's buttons.

**Every one of these now files through the SAME intake service** (`spiderbot/
intake/service.py`). The forum post stays: it is the reporter's own visible
return path, a thread they can watch. What changed is that the report is also a
durable record with a stable id before the forum post is attempted, so a report
is no longer just a Discord message that a channel cleanup would erase.

The order matters and is deliberate: file first, post second. If the durable
write fails the person is told plainly that it was not saved, rather than
thanked for a thread that is the only copy.

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

from spiderbot import audit, style
from spiderbot.intake.models import Category, Reporter
from spiderbot.ui.safe import safe_defer, safe_followup

log = logging.getLogger("spiderbot.ui.forms")

NO_MENTIONS = discord.AllowedMentions.none()



def reporter_from(interaction) -> Reporter:
    """The private return path, from the interaction that produced it."""
    return Reporter(
        user_id=getattr(interaction.user, "id", 0),
        display_name=getattr(interaction.user, "display_name", "") or "",
        channel_id=getattr(interaction, "channel_id", None),
    )


async def file_report(
    bot,
    interaction,
    *,
    category: Category,
    title: str,
    description: str,
    device: str = "",
    repro_steps: str = "",
):
    """File through the one intake service, degrading honestly if it is absent.

    Returns the `Outcome`, or None when no intake service is configured — in
    which case the caller falls back to the old post-only behaviour, which is
    still better than losing the report.
    """
    service = getattr(bot, "intake", None)
    if service is None:
        return None
    return await service.file(
        category=category,
        title=title,
        description=description,
        reporter=reporter_from(interaction),
        device=device,
        repro_steps=repro_steps,
        # Set here because every form that reaches this function carries
        # `PUBLIC_NOTICE` in the field the member reads before they type. A
        # caller that has not shown it must not pass this.
        reporter_cleared=True,
    )


def receipt_for(outcome, fallback: str) -> str:
    """What to tell the reporter: the intake service's words, or the old ones."""
    return outcome.reporter_message if outcome is not None else fallback


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


def _receipt_embed(bot, verb: str, receipt: str) -> discord.Embed:
    """A confirmation. The title starts with a verb, never "Success!" (plan §5)."""
    return style.embed(
        title=f"{style.OK} {verb}",
        description=receipt,
        color=style.SUCCESS,
        icon_url=style.avatar_url(bot),
    )


#: Shown in a placeholder on every form whose report can become a public
#: GitHub issue, so the member reads it BEFORE they type rather than in the
#: receipt afterwards. `ComplaintModal` deliberately does not carry it: a
#: complaint is never publishable at all, and telling someone their private
#: message might be published would be false.
PUBLIC_NOTICE = "Menno may put this on the game's public issue tracker."


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
        placeholder=(
            "What happened / what you expected / device and Android version. "
            + PUBLIC_NOTICE
        ),
    )

    def __init__(self, bot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:

        # Defer FIRST: the store write below can take a cold history
        # scan on the first submission after a deploy, and Discord kills
        # the interaction token at 3 seconds — the report lands and the
        # member sees "interaction failed" and files it again.
        if not await safe_defer(interaction, ephemeral=True):
            return
        body = (
            f"{self.details.value}\n\n*Submitted by {interaction.user.display_name} "
            f"via the feedback form*"
        )
        outcome = await file_report(
            self.bot,
            interaction,
            category=Category.GAMEPLAY_FEEDBACK,
            title=str(self.summary.value),
            description=str(self.details.value),
        )
        receipt = await _deliver(
            self.bot, "feedback", str(self.summary.value), body, interaction.user
        )
        await safe_followup(
            interaction,
            embed=_receipt_embed(
                self.bot, "Feedback sent", receipt_for(outcome, receipt)
            ),
            ephemeral=True,
        )
        audit.stdout_event(
            "feedback_submitted",
            user=str(interaction.user),
            report_id=getattr(getattr(outcome, "report", None), "id", None),
        )


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
        placeholder=(
            "Smaller steps are better - it is how a bug gets reproduced. "
            + PUBLIC_NOTICE
        ),
    )

    def __init__(self, bot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:

        # Defer FIRST: the store write below can take a cold history
        # scan on the first submission after a deploy, and Discord kills
        # the interaction token at 3 seconds — the report lands and the
        # member sees "interaction failed" and files it again.
        if not await safe_defer(interaction, ephemeral=True):
            return
        steps = str(self.steps.value or "").strip() or "(not given)"
        body = (
            f"**Device:** {self.device.value}\n\n"
            f"**What happened**\n{self.details.value}\n\n"
            f"**Steps before it**\n{steps}\n\n"
            f"*Reported by {interaction.user.display_name} via the bug form*"
        )
        outcome = await file_report(
            self.bot,
            interaction,
            category=Category.BUG,
            title=str(self.summary.value),
            description=str(self.details.value),
            device=str(self.device.value),
            repro_steps=steps,
        )
        receipt = await _deliver(
            self.bot, "bug-reports", str(self.summary.value), body, interaction.user
        )
        await safe_followup(
            interaction,
            embed=_receipt_embed(
                self.bot, "Bug reported", receipt_for(outcome, receipt)
            ),
            ephemeral=True,
        )
        audit.stdout_event(
            "bug_submitted",
            user=str(interaction.user),
            report_id=getattr(getattr(outcome, "report", None), "id", None),
        )


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
            await interaction.followup.send(
                embed=style.ai_embed(result.text[:1900]), ephemeral=True
            )
        audit.stdout_event(
            "ai_decision",
            decision="replied" if result.text else "degraded",
            mode="ask_form",
            reason=result.reason.upper(),
            model=result.model,
            tokens_in=result.input_tokens,
            tokens_out=result.output_tokens,
        )


class IdeaModal(discord.ui.Modal, title="Share an idea"):
    """An improvement idea. Its own category so the developer can filter one."""

    summary = discord.ui.TextInput(
        label="One-line summary",
        max_length=90,
        placeholder="e.g. The bird should slow down after you dive",
    )
    details = discord.ui.TextInput(
        label="What is the idea, and why?",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        placeholder=(
            "What it would change, and what problem it solves for you. "
            + PUBLIC_NOTICE
        ),
    )

    def __init__(self, bot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:

        # Defer FIRST: the store write below can take a cold history
        # scan on the first submission after a deploy, and Discord kills
        # the interaction token at 3 seconds — the report lands and the
        # member sees "interaction failed" and files it again.
        if not await safe_defer(interaction, ephemeral=True):
            return
        outcome = await file_report(
            self.bot,
            interaction,
            category=Category.IDEA,
            title=str(self.summary.value),
            description=str(self.details.value),
        )
        body = (
            f"{self.details.value}\n\n*An idea from "
            f"{interaction.user.display_name}*"
        )
        receipt = await _deliver(
            self.bot, "feedback", str(self.summary.value), body, interaction.user
        )
        await safe_followup(
            interaction,
            embed=_receipt_embed(self.bot, "Idea saved", receipt_for(outcome, receipt)),
            ephemeral=True,
        )
        audit.stdout_event(
            "idea_submitted",
            user=str(interaction.user),
            report_id=getattr(getattr(outcome, "report", None), "id", None),
        )


class ComplaintModal(discord.ui.Modal, title="Tell Menno something"):
    """The private route. Never posted publicly and never projected to GitHub.

    A complaint is the ambiguous category - it can be "the game is too hard" or
    "this member is harassing me" - so it stays private until a human decides,
    and the copy here says so before anyone types. Telling someone what will
    happen to their words is the difference between a report and a surprise.
    """

    summary = discord.ui.TextInput(
        label="One-line summary",
        max_length=90,
        placeholder="What is this about?",
    )
    details = discord.ui.TextInput(
        label="What happened?",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        placeholder=(
            "If this is about another member, say who and what they did. "
            "Only Menno and the moderators will see this."
        ),
    )

    def __init__(self, bot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:

        # Defer FIRST: the store write below can take a cold history
        # scan on the first submission after a deploy, and Discord kills
        # the interaction token at 3 seconds — the report lands and the
        # member sees "interaction failed" and files it again.
        if not await safe_defer(interaction, ephemeral=True):
            return
        outcome = await file_report(
            self.bot,
            interaction,
            category=Category.COMPLAINT,
            title=str(self.summary.value),
            description=str(self.details.value),
        )
        # Deliberately NOT posted to a public forum. The only place it goes is
        # the private store and a staff-only note.
        await audit.modlog_event(
            self.bot.channels.get("mod-log"),
            f"{style.WARN} Private report",
            (
                f"**{style.escape_name(interaction.user.display_name)}** sent a "
                f"private report: `{getattr(getattr(outcome, 'report', None), 'id', '?')}`\n"
                "Open it from Home -> Reports. It is not public and will not be "
                "filed on GitHub."
            ),
            style.WARNING,
        )
        await safe_followup(
            interaction,
            embed=_receipt_embed(
                self.bot,
                "Sent privately",
                receipt_for(
                    outcome,
                    "Thank you - Menno will see this. It stays between you and "
                    "the moderators.",
                ),
            ),
            ephemeral=True,
        )
        audit.stdout_event(
            "complaint_submitted",
            user=str(interaction.user),
            report_id=getattr(getattr(outcome, "report", None), "id", None),
        )
