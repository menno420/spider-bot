"""Stable, human-quotable ids — the thread that ties Discord to GitHub to a case.

Two kinds of identifier, and they answer different questions:

**Record ids** name a durable thing: a report, a moderation case. They are
minted once, printed to the person who caused them, written into a GitHub issue
body, and never change. Someone must be able to read one off a phone screen and
type it back, so the alphabet excludes the four characters people transcribe
wrongly (I, L, O, U) and the whole id is uppercase.

**Correlation ids** name one journey through the pipeline. They are minted at
the edge — a message arriving, a button pressed — and carried into every audit
event that journey produces, so

    Discord interaction → intake → GitHub issue

and

    Discord message → AI verdict → moderation action → case

can each be reconstructed from the log afterwards. There was no such thing here
before: `grep -riE "correlation.?id|trace.?id"` over `spiderbot/` matched
nothing, which is why an AI decision and the action it led to could only be
joined by eye and by timestamp.

Both are sortable by mint time, because the time component leads. Neither
carries a Discord id, a name, or anything else about a person: an id is printed
in public and pasted into a public GitHub issue.

The clock and the entropy source are injectable so tests can assert on an exact
string rather than a shape.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable

# Crockford-style base32 minus I, L, O and U: the four that get read back wrong.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

PREFIX = "SB"
SUFFIX_LENGTH = 6

# One letter per record kind. Short, because these are read aloud and typed.
KIND_REPORT = "R"
KIND_CASE = "M"
KIND_CORRELATION = "C"

KINDS: frozenset[str] = frozenset({KIND_REPORT, KIND_CASE, KIND_CORRELATION})


def _encode(value: int, length: int) -> str:
    """`value` in the id alphabet, left-padded to `length`, most significant first."""
    out = []
    for _ in range(length):
        value, digit = divmod(value, len(ALPHABET))
        out.append(ALPHABET[digit])
    return "".join(reversed(out))


def _random_suffix(rand: Callable[[int], int]) -> str:
    return _encode(rand(len(ALPHABET) ** SUFFIX_LENGTH), SUFFIX_LENGTH)


def mint(
    kind: str,
    *,
    now: Callable[[], float] = time.time,
    rand: Callable[[int], int] = secrets.randbelow,
) -> str:
    """A new id of `kind`, e.g. `SB-R-01K2M9WQ-7F3KZ2`.

    The middle group is the mint time in milliseconds, base-32; it leads so
    string order is time order. The last group is 6 random characters — with a
    32-character alphabet that is 2^30 possibilities inside a single
    millisecond, so a collision needs two records minted in the same
    millisecond AND drawing the same suffix.

    `kind` must be one of the KIND_* constants. An unknown kind is a
    programming error and raises, unlike almost everything else in this
    codebase: an id is minted before anything durable is written, so failing
    loudly here costs nothing and a malformed id would poison every record and
    every GitHub issue that quotes it.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown id kind {kind!r}; expected one of {sorted(KINDS)}")
    stamp = _encode(int(now() * 1000), 8)
    return f"{PREFIX}-{kind}-{stamp}-{_random_suffix(rand)}"


def report_id(**kw) -> str:
    """A durable report id. Printed to the reporter and written into GitHub."""
    return mint(KIND_REPORT, **kw)


def case_id(**kw) -> str:
    """A durable moderation-case id. Printed to staff, never to the subject."""
    return mint(KIND_CASE, **kw)


def correlation_id(**kw) -> str:
    """One journey through the pipeline, from the edge event to the last audit row."""
    return mint(KIND_CORRELATION, **kw)


def is_valid(value: str, kind: str | None = None) -> bool:
    """True when `value` is an id this module could have minted.

    Used wherever an id arrives from outside — a person typing it into a form,
    a string parsed back out of a GitHub issue body. Never trust the shape of
    an id you did not mint: it reaches a store lookup.
    """
    if not isinstance(value, str):
        return False
    parts = value.split("-")
    if len(parts) != 4:
        return False
    prefix, got_kind, stamp, suffix = parts
    if prefix != PREFIX or got_kind not in KINDS:
        return False
    if kind is not None and got_kind != kind:
        return False
    if len(stamp) != 8 or len(suffix) != SUFFIX_LENGTH:
        return False
    return all(c in ALPHABET for c in stamp + suffix)
