"""Reading the tester roster out of live Discord state.

`cohort.py` is pure arithmetic and must stay that way, so the part that talks
to Discord lives here: who currently holds the role, and when each grant
happened according to the guild audit log. Both the roster commands and the
Home panel read through this module, so they can never disagree.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import discord

from spiderbot import cohort

log = logging.getLogger("spiderbot.roster")


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
