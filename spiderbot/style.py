"""The locked visual system - plan `docs/plan-onboarding-ux-and-site.md` §5.

One place decides what Spider Bot looks like, so a reader can tell at a glance
who is talking and how much attention something wants. Two rules do most of the
work:

- **Colour is semantic only.** Green is never decorative. Orange means "you
  need to do something" and nothing else - which is why the brand colour is
  green and not orange: on a thin embed stripe on a phone, an orange brand
  reads as a warning.
- **The AI never speaks without the purple accent and the speech balloon**, so
  a member can always tell a generated reply from an operational one.

The emoji vocabulary is closed. Nothing outside `VOCABULARY` belongs on a
public surface, and `tests/test_style.py` fails the build if something drifts
in - a lock nobody enforces is a preference.

This module sits *below* `ui/` rather than inside it: `presets.py` and `cogs/`
need the same palette, and the layering rule is `cogs -> ui -> (presets,
roster, cohort, config)` - anything both layers need lives below them, never in
a view (CLAUDE.md invariant 13).
"""

from __future__ import annotations

import discord

# -- palette (plan §5, hex values locked) -----------------------------------

BRAND = discord.Color(0x1A8F5C)  # the mascot's voice, the happy path
SUCCESS = discord.Color(0x2ECC71)  # confirmed, opted in, feedback sent
WARNING = discord.Color(0xF39C12)  # needs your attention, something is unset
ALARM = discord.Color(0xE74C3C)  # tester lost, streak broken, error
AI = discord.Color(0x9B59B6)  # every message the AI authored - no exceptions
NEUTRAL = discord.Color(0x34495E)  # hubs, settings, read-only status
ACCENT = discord.Color(0x00D4AA)  # mascot glow, milestone moments only

PALETTE: dict[str, discord.Color] = {
    "brand": BRAND,
    "success": SUCCESS,
    "warning": WARNING,
    "alarm": ALARM,
    "ai": AI,
    "neutral": NEUTRAL,
    "accent": ACCENT,
}

# -- the closed emoji vocabulary (plan §5) ----------------------------------

SPIDER = "\N{SPIDER}"  # the bot itself
WEB = "\N{SPIDER WEB}"  # webs, links, joining
OK = "\N{WHITE HEAVY CHECK MARK}"  # success / confirmed
WARN = "\N{WARNING SIGN}"  # warning
SIREN = "\N{POLICE CARS REVOLVING LIGHT}"  # alarm
BUG = "\N{BUG}"  # bug report
SPEECH = "\N{SPEECH BALLOON}"  # feedback / the AI speaking
QUESTION = "\N{BLACK QUESTION MARK ORNAMENT}"  # question / help
CHART = "\N{BAR CHART}"  # status / the clock
ANNOUNCE = "\N{PUBLIC ADDRESS LOUDSPEAKER}"  # announcement / preset
GEAR = "\N{GEAR}"  # settings / staff tools

VOCABULARY: frozenset[str] = frozenset(
    {SPIDER, WEB, OK, WARN, SIREN, BUG, SPEECH, QUESTION, CHART, ANNOUNCE, GEAR}
)

# Discord renders these with or without the emoji-presentation selector; it is
# a rendering hint, never part of the identity, so comparisons strip it.
VARIATION_SELECTOR = "\N{VARIATION SELECTOR-16}"

# -- the house embed ---------------------------------------------------------

AUTHOR_NAME = "Spider Bot"
FOOTER_BASE = "Slingy Spider closed test"
EXPIRY_HINT_MINUTES = "This panel stays open for {minutes} minutes."


def avatar_url(bot) -> str | None:
    """The bot's own avatar, or None before it is connected (and in tests)."""
    avatar = getattr(getattr(bot, "user", None), "display_avatar", None)
    return getattr(avatar, "url", None)


def panel_footer(timeout: float | None) -> str:
    """The footer for a panel, stating its lifetime up front.

    A member who knows the panel expires reads a greyed-out button as "it
    timed out" rather than "the bot is broken".
    """
    if timeout is None:
        return f"{FOOTER_BASE} \N{BULLET} this panel stays put"
    return f"{FOOTER_BASE} \N{BULLET} open for {int(timeout // 60)} min"


def embed(
    *,
    title: str | None = None,
    description: str | None = None,
    color: discord.Color = BRAND,
    footer: str | None = None,
    icon_url: str | None = None,
) -> discord.Embed:
    """The one embed factory. Same author, same footer shape, every time.

    Never "Bot" and never "System": a public embed that does not look like the
    others reads as a different, less trustworthy sender.
    """
    e = discord.Embed(title=title, description=description, color=color)
    e.set_author(name=AUTHOR_NAME, icon_url=icon_url)
    e.set_footer(text=footer or FOOTER_BASE)
    return e


def ai_embed(text: str) -> discord.Embed:
    """Anything the AI authored. Purple and the balloon, without exception.

    Deliberately minimal - no author line, no footer. In a chat reply Discord
    already shows the bot as the message author, so repeating it is noise. The
    purple stripe and the balloon are the signal that has to carry, and the
    contrast is the system: an embed in purple means the AI wrote it, plain
    text means the bot itself is speaking.
    """
    return discord.Embed(description=f"{SPEECH} {text}", color=AI)


def escape_name(name: str) -> str:
    """A member's display name, safe inside one of our embeds.

    Lives here rather than in `redact` because every embed built through this
    module needs it and `style` is what both `ui/` and `cogs/` already import.
    """
    from spiderbot import redact

    return redact.for_discord(name or "", limit=64)
