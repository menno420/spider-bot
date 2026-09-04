"""The moderation listener and the staff review commands.

Thin by design: everything that decides anything lives in
`spiderbot/moderation/`, which has no Discord handle and is exhaustively
testable. This cog is the wiring — one listener, three commands — so that the
part with judgement in it can be exercised without a gateway.

The listener runs the whole pipeline and stores a case for every decision,
including the ones where nothing happened. In shadow mode that is the entire
point: a decision nobody recorded is a decision nobody can evaluate.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from spiderbot import audit, style
from spiderbot.moderation.cases import ReviewOutcome
from spiderbot.moderation.contracts import Operation

log = logging.getLogger("spiderbot.cogs.moderation")


class ModerationCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.cfg = bot.cfg

    @property
    def _service(self):
        return getattr(self.bot, "moderation", None)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """One message through the pipeline. Never raises into the gateway."""
        service = self._service
        if service is None:
            return
        try:
            case = await service.handle_message(
                message, bot_user_id=getattr(self.bot.user, "id", None)
            )
        except Exception:  # a listener must never take the gateway down
            log.exception("moderation listener failed")
            return
        if case is None:
            return
        # Anything that acted, would have acted, or was refused is worth a
        # moderator's eye. Everything else is just corpus.
        if case.acted or case.would_have_acted or case.refusal_reason:
            await audit.modlog_event(
                self.bot.channels.get("mod-log"),
                f"{style.SIREN} Moderation — {case.mode}",
                "\n".join(
                    [
                        case.summary_line(),
                        "",
                        (case.decision or {}).get("rationale", ""),
                        "",
                        f"Review it with `/case review {case.id} <verdict>`.",
                    ]
                ),
                style.WARNING if not case.acted else style.ALARM,
            )

    # -- staff commands ------------------------------------------------------

    case_group = app_commands.Group(
        name="case",
        description="Moderation cases: what was decided, and whether it was right",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @case_group.command(name="list", description="Recent moderation decisions")
    async def list_cases(self, interaction: discord.Interaction) -> None:
        service = self._service
        if service is None:
            await interaction.response.send_message(
                "Moderation is not configured.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        cases = await service.cases(limit=15)
        body = "\n".join(c.summary_line() for c in cases) or "Nothing recorded yet."
        await interaction.followup.send(
            embed=style.embed(
                title=f"{style.SIREN} Recent decisions",
                description=body[:3900],
                color=style.NEUTRAL,
                icon_url=style.avatar_url(self.bot),
            ),
            ephemeral=True,
        )

    @case_group.command(
        name="review", description="Say whether a decision was right"
    )
    @app_commands.describe(
        case_id="The case id, e.g. SB-M-...",
        verdict="Was the decision correct, too strict, too lenient, or the wrong category?",
        note="Optional: why",
    )
    @app_commands.choices(
        verdict=[
            app_commands.Choice(name="correct", value="correct"),
            app_commands.Choice(name="too strict", value="too_strict"),
            app_commands.Choice(name="too lenient", value="too_lenient"),
            app_commands.Choice(name="wrong category", value="wrong_category"),
        ]
    )
    async def review_case(
        self,
        interaction: discord.Interaction,
        case_id: str,
        verdict: app_commands.Choice[str],
        note: str = "",
    ) -> None:
        service = self._service
        if service is None:
            await interaction.response.send_message(
                "Moderation is not configured.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        reviewed = await service.review(
            case_id.strip(),
            ReviewOutcome(verdict.value),
            by=str(interaction.user),
            note=note,
        )
        if reviewed is None:
            await interaction.followup.send(
                f"No case `{case_id[:40]}`.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"Marked `{reviewed.id}` **{verdict.value}**. "
            "That is what the policy gets evaluated against.",
            ephemeral=True,
        )

    @case_group.command(
        name="status", description="Moderation mode, policy and review tally"
    )
    async def status(self, interaction: discord.Interaction) -> None:
        service = self._service
        if service is None:
            await interaction.response.send_message(
                "Moderation is not configured — set MOD_MODE and create the "
                f"private #{self.cfg.ch_case_state} channel.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        from spiderbot.moderation.cases import review_tally

        cases = await service.cases(limit=200)
        tally = review_tally(cases)
        lines = list(service.describe())
        lines += [
            "",
            f"**{len(cases)}** decisions recorded",
            "· "
            + " · ".join(f"{name}: {count}" for name, count in tally.items() if count),
        ]
        await interaction.followup.send(
            embed=style.embed(
                title=f"{style.GEAR} Moderation status",
                description="\n".join(lines)[:3900],
                color=style.NEUTRAL,
                icon_url=style.avatar_url(self.bot),
            ),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        """Re-judge a message whose content changed.

        Codex, spider-bot#3, 2026-09-04: only `on_message` was registered, so a
        member could post harmless text in a watched channel, let it be
        classified, and edit the same message into abuse or a fake tester link
        without the new content ever entering the pipeline. Everything
        downstream is unchanged — the same precheck, the same budget, the same
        gate — so an edit costs exactly what a new message costs.

        Edits that do not change the content (an embed resolving, a pin) are
        ignored: Discord fires this for those too, and re-judging them would
        spend the classifier on nothing.
        """
        if (before.content or "") == (after.content or ""):
            return
        await self._judge_edit(after)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        """The same, for a message discord.py never cached.

        Codex, spider-bot#3, 2026-09-04: `on_message_edit` fires only when the
        original is in the bounded in-memory cache — so after a restart, or
        once a message ages out, editing it into abuse ran nothing at all. The
        raw event always fires; `payload.cached_message` being present means
        the typed listener above already handled it.
        """
        if payload.cached_message is not None:
            return
        message = getattr(payload, "message", None)
        if message is None:
            return
        await self._judge_edit(message)

    async def _judge_edit(self, message: discord.Message) -> None:
        """Route edited content through the same pipeline, exempt from the
        member cooldown but NOT from the global cap.

        Codex, spider-bot#3, 2026-09-04: delegating to `on_message` meant the
        cooldown the ORIGINAL message had just armed suppressed the edit — so
        the edit listener did nothing in exactly the case it exists for. An
        edit is not extra volume a member chose to send; it is the same message
        changing under a judgement already made, and the global hourly cap
        still bounds the spend.
        """
        service = getattr(self.bot, "moderation", None)
        if service is None:
            return
        await service.handle_message(
            message, bot_user_id=getattr(self.bot.user, "id", None), reason="edit"
        )

    @app_commands.command(
        name="modact", description="Take a moderation action, recorded as a case"
    )
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(
        member="Who", action="What", reason="Why (goes in the audit log and the case)"
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="warn", value="warn"),
            app_commands.Choice(name="timeout 10 minutes", value="timeout_short"),
            app_commands.Choice(name="timeout 24 hours", value="timeout_long"),
            app_commands.Choice(name="kick", value="kick"),
            app_commands.Choice(name="ban", value="ban"),
        ]
    )
    async def modact(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        action: app_commands.Choice[str],
        reason: str,
    ) -> None:
        """A moderator's own action, through the same typed operations.

        Kick and ban live here and nowhere else — no policy rule produces
        either — so the actor of record is always a person.
        """
        service = self._service
        if service is None:
            await interaction.response.send_message(
                "Moderation is not configured.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        case = await service.staff_action(
            Operation(action.value),
            guild=interaction.guild,
            subject=member,
            actor=interaction.user,
            # WARN posts in a channel, so the executor needs one. Without it
            # `/modact … warn` always answered "no channel to warn in" — the
            # action was in the choice list and could never be performed.
            message=_ChannelOnly(interaction.channel),
            reason=reason,
        )
        if case.refusal_reason:
            await interaction.followup.send(
                f"Not done: {case.refusal_reason} (case `{case.id}`)", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"Done — `{case.performed}` on {style.escape_name(member.display_name)}. "
            f"Case `{case.id}`.",
            ephemeral=True,
        )


class _ChannelOnly:
    """A message-shaped object carrying only a channel.

    `EnforcingExecutor.perform` reads `message.channel` to post a warning and
    `message.delete` to remove one. A staff `/modact warn` has a channel and no
    message to delete, and passing the interaction itself would hand a delete
    path to something that must not have one.
    """

    __slots__ = ("channel",)

    def __init__(self, channel) -> None:
        self.channel = channel


async def setup(bot) -> None:
    await bot.add_cog(ModerationCog(bot))
