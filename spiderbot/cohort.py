"""The closed-test clock: how far the tester cohort is through Google's
14-day continuous-opt-in window.

Google Play will not let Slingy Spider leave closed testing until
`REQUIRED_TESTERS` testers have been opted in continuously for `WINDOW_DAYS`
days. Discord cannot see Play opt-in state - but the Slingy Tester role is
granted only after a human verified the opt-in (invariant 5), so the role's
most recent grant is the best available proxy for the start of a streak.

Deliberately pure: it takes `(name, granted_at)` records plus a `now` and
returns a standing. No Discord, no I/O, no clock of its own - the cog reads
the grant dates from the guild audit log and passes them in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

REQUIRED_TESTERS = 12
WINDOW_DAYS = 14
# Recruit above the bar: with exactly 12, one dropout restarts the clock.
RECRUIT_TARGET = 16


@dataclass(frozen=True)
class RosterEntry:
    """One current holder of the tester role.

    `granted_at` is None when the grant is not in the audit log - either it
    predates Discord's 45-day retention or the bot cannot read the log. An
    unknown date is never optimistically counted as qualified.
    """

    name: str
    granted_at: datetime | None


@dataclass(frozen=True)
class Standing:
    name: str
    days: int | None
    qualified: bool
    clears_at: datetime | None


@dataclass(frozen=True)
class CohortStatus:
    standings: tuple[Standing, ...]
    roster: int
    qualified: int
    unknown_dates: int
    short_by: int
    buffer: int
    clears_at: datetime | None
    verdict: str


def summarize(
    records,
    *,
    now: datetime,
    required: int = REQUIRED_TESTERS,
    window_days: int = WINDOW_DAYS,
) -> CohortStatus:
    """Standing of the cohort at `now`. Never raises on odd input."""
    window = timedelta(days=window_days)
    standings: list[Standing] = []
    # Longest-serving first; unknown dates sink to the bottom.
    ordered = sorted(
        records, key=lambda r: (r.granted_at is None, r.granted_at or now, r.name.lower())
    )
    for rec in ordered:
        if rec.granted_at is None:
            standings.append(Standing(rec.name, None, False, None))
            continue
        elapsed = now - rec.granted_at
        days = max(0, elapsed.days)
        standings.append(
            Standing(rec.name, days, elapsed >= window, rec.granted_at + window)
        )

    roster = len(standings)
    qualified = sum(1 for s in standings if s.qualified)
    unknown = sum(1 for s in standings if s.days is None)
    short_by = max(0, required - roster)
    buffer = roster - required

    known_clears = sorted(s.clears_at for s in standings if s.clears_at is not None)
    # The required-th earliest clear date is when that many are simultaneously
    # past the window - assuming nobody drops out before then.
    clears_at = known_clears[required - 1] if len(known_clears) >= required else None

    return CohortStatus(
        standings=tuple(standings),
        roster=roster,
        qualified=qualified,
        unknown_dates=unknown,
        short_by=short_by,
        buffer=buffer,
        clears_at=clears_at,
        verdict=_verdict(roster, qualified, short_by, buffer, clears_at, required, window_days),
    )


def _verdict(roster, qualified, short_by, buffer, clears_at, required, window_days) -> str:
    if roster == 0:
        return (
            f"No verified testers yet. Google needs **{required}** people opted in for "
            f"**{window_days} continuous days** - recruit {RECRUIT_TARGET} so dropouts "
            f"cannot reset the clock."
        )
    if qualified >= required:
        return (
            f"**Bar cleared** - {qualified} testers have held {window_days}+ continuous "
            f"days. Keep them opted in until the release actually ships."
        )
    if short_by:
        return (
            f"**{short_by} more tester(s) needed** before the clock can finish. "
            f"The {window_days}-day window only completes once {required} people are "
            f"in it at the same time."
        )
    if clears_at is None:
        return (
            f"{roster} on the roster, but too many grant dates are unknown to project "
            f"a finish date. Treat the unknowns as unverified."
        )
    when = clears_at.strftime("%a %d %b %Y")
    if buffer <= 0:
        return (
            f"On track to clear on **{when}** - but with no spare testers, a single "
            f"dropout restarts the clock. Recruit up to {RECRUIT_TARGET}."
        )
    return (
        f"On track to clear on **{when}**, with {buffer} spare tester(s) of buffer "
        f"if someone drops out."
    )


def report_lines(status: CohortStatus, *, window_days: int = WINDOW_DAYS) -> list[str]:
    """The owner-facing report, as plain lines. Pure so it can be tested."""
    lines = [
        f"**Closed-test clock** - {status.roster} verified tester(s), "
        f"{status.qualified} past {window_days} days.",
        status.verdict,
    ]
    if not status.standings:
        return lines
    lines.append("")
    for s in status.standings:
        if s.days is None:
            lines.append(f"- {s.name} - grant date unknown (not in the audit log)")
        elif s.qualified:
            lines.append(f"- {s.name} - day {s.days} \N{WHITE HEAVY CHECK MARK} cleared")
        else:
            left = max(0, window_days - s.days)
            lines.append(f"- {s.name} - day {s.days}, {left} to go")
    if status.unknown_dates:
        lines.append("")
        lines.append(
            f"*{status.unknown_dates} grant date(s) unknown - Discord keeps audit "
            f"history for about 45 days, and grants made before the bot existed are "
            f"not recorded.*"
        )
    return lines
