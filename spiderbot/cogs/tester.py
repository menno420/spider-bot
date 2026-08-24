"""Tester-role management and the closed-test clock: deterministic, mod-gated,
audited.

The AI never touches these paths. The Slingy Tester role mirrors the real Play
closed-test cohort, so grants happen only after a human verified the opt-in.
Because the grant is human-verified, its timestamp is the honest start of a
tester's 14-day streak - `/tester count` reads those timestamps back out of
the guild audit log (no database needed) and reports where the cohort stands.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from spiderbot import audit, cohort

log = logging.getLogger("spiderbot.tester")

# How far back to scan the audit log for role grants. Discord keeps ~45 days
# of history, comfortably more than the 14-day window we measure.
AUDIT_SCAN_LIMIT = 500


async def grant_dates(guild, role) -> tuple[dict[int, datetime], bool]:
    """Most recent grant of `role` per user id, newest-wins.

    Returns `(dates, readable)`. `readable` is False when the audit log cannot
    be read - the report then degrades to "date unknown" rather than refusing
    (invariant 2: degrade gracefully, report what is missing).
    """
    dates: dict[int, datetime] = {}
    try:
        async for entry in guild.audit_logs(
            limit=AUDIT_SCAN_LIMIT, action=discord.AuditLogAction.member_role_update
        ):
            target = entry.target
            if target is None or target.id in dates:
                continue  # entries arrive newest-first: the first hit is current
            added = getattr(entry.after, "roles", None) or ()
            if any(getattr(r, "id", None) == role.id for r in added):
                dates[target.id] = entry.created_at
    except discord.Forbidden:
        log.warning("no View Audit Log permission - grant dates unavailable")
        return {}, False
    except discord.HTTPException:
        log.exception("audit log read failed")
        return dates, False
    return dates, True


async def cohort_status(guild, role) -> tuple[cohort.CohortStatus, bool]:
    """Current standing of the tester cohort, read from live guild state."""
    holders = [m for m in guild.members if role in m.roles]
    dates, readable = await grant_dates(guild, role)
    entries = [cohort.RosterEntry(m.display_name, dates.get(m.id)) for m in holders]
    return cohort.summarize(entries, now=datetime.now(UTC)), readable


@app_commands.default_permissions(manage_roles=True)
class TesterGroup(app_commands.Group):
    """/tester add | remove | count"""

    def __init__(self, bot, cog) -> None:
        super().__init__(name="tester", description="Manage the Slingy Tester roster")
        self.bot = bot
        self.cog = cog

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
        short = max(0, cohort.REQUIRED_TESTERS - count)
        goal = (
            f"{short} more to reach {cohort.REQUIRED_TESTERS}"
            if short
            else f"at the {cohort.REQUIRED_TESTERS} needed - the clocks are running"
        )
        await interaction.response.send_message(
            f"{user.display_name} is now a **{role.name}** - roster: **{count}** ({goal}).\n"
            f"Their {cohort.WINDOW_DAYS}-day window starts now; `/tester count` tracks it.",
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
        # Deliberate removal: suppress the streak-broken alarm for this one.
        self.cog.expect_removal(user.id)
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

    @app_commands.command(
        name="count",
        description="Where the closed-test clock stands (roster, days, projected finish)",
    )
    async def count(self, interaction: discord.Interaction) -> None:
        role = self._role(interaction.guild)
        if role is None:
            await interaction.response.send_message(
                f"Role **{self.bot.cfg.tester_role_name}** not found - nothing to count.",
                ephemeral=True,
            )
            return
        # The audit-log read is a network round trip; defer so it cannot expire.
        await interaction.response.defer(ephemeral=True)
        status, readable = await cohort_status(interaction.guild, role)
        lines = cohort.report_lines(status)
        if not readable:
            lines.append(
                "*Grant dates unavailable: the bot cannot read this audit log. "
                "Give it **View Audit Log** to see day counts.*"
            )
        await interaction.followup.send("\n".join(lines)[:1990], ephemeral=True)
        audit.stdout_event(
            "cohort_reported", by=str(interaction.user), roster=status.roster,
            qualified=status.qualified, unknown=status.unknown_dates,
        )


class RosterCog(commands.Cog):
    """Roster commands plus the streak-broken watch.

    Google counts *continuous* opt-in, so a tester losing the role or leaving
    the server restarts their 14-day clock. That is the one thing the owner
    must hear about without having to ask.
    """

    def __init__(self, bot) -> None:
        self.bot = bot
        self.cfg = bot.cfg
        self._deliberate: set[int] = set()
        bot.tree.add_command(TesterGroup(bot, self), guild=discord.Object(id=bot.cfg.guild_id))

    def expect_removal(self, user_id: int) -> None:
        """Mark the next role loss for this user as intentional (/tester remove)."""
        self._deliberate.add(user_id)

    def _is_tester(self, member) -> bool:
        return any(r.name == self.cfg.tester_role_name for r in getattr(member, "roles", []))

    @commands.Cog.listener()
    async def on_member_update(self, before, after) -> None:
        if getattr(after.guild, "id", None) != self.cfg.guild_id:
            return
        if not (self._is_tester(before) and not self._is_tester(after)):
            return
        if after.id in self._deliberate:
            self._deliberate.discard(after.id)
            return
        await self._streak_broken(after, "lost the Slingy Tester role")

    @commands.Cog.listener()
    async def on_member_remove(self, member) -> None:
        if getattr(member.guild, "id", None) != self.cfg.guild_id:
            return
        if self._is_tester(member):
            await self._streak_broken(member, "left the server")

    async def _streak_broken(self, member, what: str) -> None:
        await audit.modlog_event(
            self.bot.channels.get("mod-log"),
            "Tester streak broken",
            f"**{member.display_name}** {what}.\n"
            f"Google counts *continuous* opt-in, so their {cohort.WINDOW_DAYS}-day clock "
            f"restarts from zero if they come back. Run `/tester count` to see where the "
            f"cohort stands now.",
            discord.Color.red(),
        )
        audit.stdout_event("tester_streak_broken", user=str(member), reason=what)


async def setup(bot) -> None:
    await bot.add_cog(RosterCog(bot))
