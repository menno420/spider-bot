"""Neutralising text that a member wrote before it reaches somewhere it can act.

Three destinations, three different things that are dangerous, one module so
the rules live together and can be tested together.

**Discord.** `AllowedMentions.none()` already stops a ping from resolving, and
the bot uses it on every send. But it does not stop the *text* `@everyone` from
appearing in an embed and reading as though the bot pinged the server, and it
does not stop markdown from restructuring a panel — a member whose bug title is
`# FREE NITRO` gets a heading in the mod's console. So mentions are defanged
visually and markdown is escaped wherever member text is rendered inside the
bot's own chrome.

**GitHub.** This is the one with teeth. A GitHub issue body renders `@name` as a
real mention that notifies a real person, and `#123` as a cross-reference that
posts a backlink onto someone else's issue. A member typing `@menno420 #1` into
a bug report would, without this, cause Spider Bot to notify the owner's GitHub
account and graffiti an unrelated issue — from a public Discord anyone can join.
`for_github` breaks both by inserting a zero-width space after the sigil: the
text still reads correctly to a human and no longer resolves.

**Log lines and audit records.** Newlines are what let one field forge another,
so anything going into a single-line context gets them folded.

Nothing here is a security boundary against a *determined* attacker rendering
their own markdown — it is a boundary against member text acquiring authority it
was never given. The privacy decision about whether text may be published at all
is `intake/privacy.py`'s job, not this file's; this file assumes the decision to
publish was already made correctly.
"""

from __future__ import annotations

import re

# U+200B. Invisible in every client tested, and it is what stops GitHub's and
# Discord's parsers from seeing a sigil followed by a name.
ZERO_WIDTH = "​"

_DISCORD_MARKDOWN = re.compile(r"([*_~`|\\>#\-])")
_MENTION_SIGIL = re.compile(r"([@])")
_GITHUB_SIGIL = re.compile(r"([@#])")
_FENCE_RUN = re.compile(r"`{3,}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NEWLINES = re.compile(r"[\r\n]+")
# Invisible characters a member could use to hide text inside a field, or to
# make two different reports look identical. Zero-width joiner and friends;
# the one we insert ourselves is added back after stripping.
_INVISIBLES = re.compile(r"[​-‏‪-‮⁠-⁯﻿]")


def clean(text: str, *, limit: int | None = None) -> str:
    """Strip control characters and invisibles, collapse runs, optionally clip.

    The base pass. Everything else in this module runs after it, so no other
    function has to think about a NUL byte or a right-to-left override.
    """
    if not isinstance(text, str):
        text = str(text)
    out = _INVISIBLES.sub("", _CONTROL.sub("", text)).strip()
    if limit is not None and len(out) > limit:
        out = out[: max(0, limit - 1)].rstrip() + "\N{HORIZONTAL ELLIPSIS}"
    return out


def one_line(text: str, *, limit: int = 200) -> str:
    """For a log line, an embed title, or an issue title. Never multi-line."""
    return clean(_NEWLINES.sub(" ", clean(text)), limit=limit)


def for_discord(text: str, *, limit: int | None = None) -> str:
    """Member text rendered inside the bot's own embeds.

    Markdown is escaped so a title cannot become a heading and a description
    cannot become a quote block. `@` is defanged so no rendering of member text
    can read as the bot having pinged anyone, whatever `AllowedMentions` did.
    """
    escaped = _DISCORD_MARKDOWN.sub(r"\\\1", clean(text, limit=limit))
    return _MENTION_SIGIL.sub(rf"@{ZERO_WIDTH}", escaped)


def for_github(text: str, *, limit: int | None = None) -> str:
    """Member text going into a PUBLIC GitHub issue.

    `@name` and `#123` are the two sigils that make GitHub act rather than
    render: one notifies a person, the other posts a backlink onto an unrelated
    issue. Both are broken with a zero-width space, which a reader does not see
    and a parser does not cross.

    A triple backtick is neutralised too. It is not an injection - it opens a
    code block - but an unbalanced one swallows the whole rest of the issue
    body, so a member could hide everything after their own text from the
    developer reading it. Cheap to prevent, invisible when it does not apply.
    """
    broken = _GITHUB_SIGIL.sub(rf"\1{ZERO_WIDTH}", clean(text, limit=limit))
    return _FENCE_RUN.sub("'''", broken)


def fenced_for_github(text: str, *, limit: int | None = None) -> str:
    """Member text inside a fenced block, with the fence made unbreakable.

    A backtick run inside member text would otherwise close the fence early and
    let everything after it render as markdown. `for_github` already replaces
    fence runs; this alias exists so a caller putting text inside a fence says
    so at the call site rather than relying on that.
    """
    return for_github(text, limit=limit)


def is_safe_reference(value: str) -> bool:
    """True for a value safe to put in a URL path or an issue label.

    Deliberately strict: ids this system mints, and nothing else.
    """
    return bool(value) and all(c.isalnum() or c in "-_." for c in value)
