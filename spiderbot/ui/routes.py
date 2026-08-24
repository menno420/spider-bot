"""The route registry - the one place that knows what Spider Bot can do.

Ported in shape from superbot `disbot/utils/hub_registry.py` +
`subsystem_registry.py`, restructured: superbot carries route metadata in
untyped dicts (`meta.get("display_name")`) spread across two registries, which
its own docstring admits makes new hubs painful to land. Here one frozen
dataclass carries everything, so a typo is an import-time failure rather than a
button that silently renders as `None`.

Home, the pinned panel and (later) help all read this tuple. Adding a feature
means adding a `Route` and a handler - never editing a panel's layout by hand.

Audience is a *floor*: a route is shown when the viewer's standing is at least
the route's. Nobody is ever shown a button they cannot use (a dead button that
answers "you may not do that" is worse than no button).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from spiderbot import style
from spiderbot.ui.base import BUTTONS_PER_ROW

# Discord allows five action rows on a message; Home keeps one spare for the
# preset select menu.
MAX_ROWS = 4


class Audience(IntEnum):
    """Who a route is for. Ordered: each level can see everything below it."""

    EVERYONE = 0
    TESTER = 1
    MOD = 2


@dataclass(frozen=True)
class Route:
    key: str
    label: str
    emoji: str
    purpose: str
    audience: Audience = Audience.EVERYONE
    row: int = 0


ROUTES: tuple[Route, ...] = (
    Route(
        key="join",
        label="How do I join?",
        emoji=style.WEB,
        purpose="The four steps to become a tester, and the trap that catches people.",
        row=0,
    ),
    Route(
        key="optedin",
        label="I've opted in",
        emoji=style.OK,
        purpose="Tell Menno you joined the test, so he can verify and hand you the role.",
        row=0,
    ),
    Route(
        key="feedback",
        label="Send feedback",
        emoji=style.SPEECH,
        purpose="An idea, or how something feels to play.",
        row=0,
    ),
    Route(
        key="bug",
        label="Report a bug",
        emoji=style.BUG,
        purpose="Something broken, with the details that make it fixable.",
        row=0,
    ),
    Route(
        key="ask",
        label="Ask a question",
        emoji=style.QUESTION,
        purpose="Ask Spider Bot about the game or the test.",
        row=0,
    ),
    Route(
        key="clock",
        label="Test status",
        emoji=style.CHART,
        purpose="Where the closed-test clock stands: roster, days, projected finish.",
        audience=Audience.MOD,
        row=1,
    ),
    Route(
        key="post",
        label="Post a message",
        emoji=style.ANNOUNCE,
        purpose="Send one of the ready-made messages without typing it.",
        audience=Audience.MOD,
        row=1,
    ),
    Route(
        key="health",
        label="Bot health",
        emoji=style.GEAR,
        purpose="Version, AI state, which channels the bot resolved.",
        audience=Audience.MOD,
        row=1,
    ),
)

ROUTES_BY_KEY: dict[str, Route] = {r.key: r for r in ROUTES}


def audience_for(member, cfg) -> Audience:
    """The viewer's standing, resolved from live Discord state.

    Re-resolved on every render and every button press - opening a panel is
    never taken as authorisation for what happens later (the donor's rule).
    """
    perms = getattr(member, "guild_permissions", None)
    if perms is not None and (
        getattr(perms, "manage_guild", False) or getattr(perms, "administrator", False)
    ):
        return Audience.MOD
    roles = getattr(member, "roles", ()) or ()
    if any(getattr(r, "name", None) == cfg.tester_role_name for r in roles):
        return Audience.TESTER
    return Audience.EVERYONE


def visible_routes(audience: Audience) -> tuple[Route, ...]:
    """Routes this viewer may actually use, in declaration order."""
    return tuple(r for r in ROUTES if r.audience <= audience)


def validate() -> list[str]:
    """Registry self-check, run at boot: keys unique, rows inside Discord's budget.

    Returns a list of problems (empty means healthy) rather than raising, so a
    registry mistake degrades to a logged warning instead of a bot that will
    not start (invariant 2).
    """
    problems: list[str] = []
    seen: set[str] = set()
    for route in ROUTES:
        if route.key in seen:
            problems.append(f"duplicate route key {route.key!r}")
        seen.add(route.key)
        if not 0 <= route.row < MAX_ROWS:
            problems.append(f"route {route.key!r} row {route.row} outside 0..{MAX_ROWS - 1}")
    for row in {r.row for r in ROUTES}:
        # Worst case is a mod, who sees every route on the row.
        count = sum(1 for r in ROUTES if r.row == row)
        if count > BUTTONS_PER_ROW:
            problems.append(f"row {row} holds {count} buttons, Discord allows {BUTTONS_PER_ROW}")
    return problems
