"""The deterministic pass that runs before any model call.

Two jobs, and the second one is the point.

**Skip cheaply.** Most messages need no judgement and every model call costs
money and latency. A message from a bot, a command, an empty message, a
moderator's own message, or a channel moderation is not enabled in never
reaches the classifier.

**Do not rebuild what Discord already does.** `docs/product-shape.md` lists
rebuilding AutoMod as a standing non-goal, and it is right: Discord's own
AutoMod handles floods, duplicate spam, mention abuse, invite and link rules
and keyword lists natively, at the gateway, before the message is delivered —
faster and more reliably than a Python listener can. discord.py 2.7.1 exposes
the whole API (`AutoModRule`, six trigger types, `Guild.create_automod_rule`,
needing `manage_guild`), so the useful thing this bot can do is **recommend**
rules the owner enables, not duplicate them.

`AUTOMOD_RECOMMENDATIONS` is that: the rules worth turning on, why, and what
they cover, so the mod console can show them and this package can honestly say
what it is not doing. Nothing here creates a rule — that changes server
configuration, which is the owner's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from spiderbot.moderation import gate

#: Rules Discord's own AutoMod handles better than this bot could. Shown in
#: the mod console as "turn these on", never created automatically.
AUTOMOD_RECOMMENDATIONS: tuple[tuple[str, str], ...] = (
    (
        "Mention spam",
        "AutoMod's mention_spam trigger blocks a message mentioning more than N "
        "people, at the gateway. A listener can only react after delivery.",
    ),
    (
        "Spam and scam links",
        "AutoMod's built-in spam and harmful_link presets are maintained by "
        "Discord against live abuse data. Nothing here can match that.",
    ),
    (
        "Invite links",
        "A keyword rule on discord.gg stops server-poaching in a testing "
        "community without any AI involvement.",
    ),
    (
        "Slur keyword list",
        "A keyword_preset rule catches the unambiguous cases with zero latency "
        "and zero cost, leaving the classifier for the context-dependent ones "
        "that are the actual reason it exists.",
    ),
)


@dataclass(frozen=True)
class Precheck:
    """Whether this message is worth judging, and why not if not."""

    proceed: bool
    reason: str = ""

    @classmethod
    def skip(cls, reason: str) -> Precheck:
        return cls(False, reason)


#: A message this short cannot carry the context a judgement needs and is
#: almost always "lol" or an emoji. Cheap and it removes most traffic.
MIN_LENGTH = 12

_COMMANDISH = ("/", "!", "?", ".", "-")
_LINK = re.compile(r"https?://", re.IGNORECASE)


def should_analyse(
    message,
    *,
    bot_user_id: int | None,
    enabled_channels: tuple[str, ...],
    staff_exempt: bool = True,
) -> Precheck:
    """The deterministic gate before a model ever sees a message.

    `enabled_channels` empty means moderation is not configured anywhere, and
    unconfigured is silent (invariant 4) — not "everywhere".
    """
    if getattr(message, "guild", None) is None:
        return Precheck.skip("not a guild message")
    author = getattr(message, "author", None)
    if author is None or getattr(author, "bot", False):
        return Precheck.skip("author is a bot")
    if bot_user_id is not None and getattr(author, "id", None) == bot_user_id:
        return Precheck.skip("our own message")

    channel_name = getattr(getattr(message, "channel", None), "name", "") or ""
    if not enabled_channels:
        return Precheck.skip("moderation is not enabled in any channel")
    if channel_name not in enabled_channels:
        return Precheck.skip(f"#{channel_name} is not a moderated channel")

    content = (getattr(message, "content", "") or "").strip()
    if not content:
        return Precheck.skip("no text to judge")
    if content.startswith(_COMMANDISH):
        return Precheck.skip("looks like a command")
    if len(content) < MIN_LENGTH and not _LINK.search(content):
        return Precheck.skip("too short to judge")

    if staff_exempt and gate.is_staff(author):
        # One definition, in gate.py. When these two disagree the disagreement
        # is invisible: the precheck decides whether a message is judged at
        # all, the gate decides whether the resulting action may land, and a
        # member in the gap is analysed and actable.
        return Precheck.skip("author is staff")

    return Precheck(True)
