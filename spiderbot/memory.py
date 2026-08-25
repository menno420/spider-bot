"""Durable memory, without a database.

The bot has no store and deliberately so (`docs/product-shape.md`: Postgres
waits until a job needs it). But remembering what a departing member had needs
memory that outlives a deploy, and Discord's audit log only retains ~45 days.

So Discord *is* the store - the same answer the plan already gives for the
site's email capture. Each record is one message in a private staff channel,
carrying a JSON payload the bot can read back. At this server's size the volume
is trivial, and a human can read the channel to see exactly what the bot
believes without any tooling.

Everything here is best-effort and never raises: an unreachable or unconfigured
state channel degrades the features above it to "no memory", never to a crash
(invariant 4 - unconfigured = silent).

The seam is deliberately narrow - `write` and `read_latest` - so moving to a
real database later is this one file, not a rewrite.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import discord

log = logging.getLogger("spiderbot.memory")

# Stamped into every record so a later reader can reject shapes it predates,
# rather than silently mis-parsing them (the digest-contract lesson from the
# plan's §7.5, applied to our own writes).
SCHEMA_VERSION = 1

_FENCE = "```json"
# Discord caps a message at 2000; a snapshot of 25 role names is far under it,
# but clamp anyway so one absurd record cannot fail the write.
_MAX = 1900


def encode(record: dict[str, Any]) -> str:
    """One record as a human-readable, machine-parsable message."""
    body = json.dumps({"v": SCHEMA_VERSION, **record}, ensure_ascii=False, default=str)
    return f"{_FENCE}\n{body[:_MAX]}\n```"


def decode(content: str) -> dict[str, Any] | None:
    """Parse a record back, or None if this message is not one of ours.

    Never raises: the channel is human-visible, so someone will eventually type
    in it, and a stray message must not break a read.
    """
    if not content or _FENCE not in content:
        return None
    try:
        body = content.split(_FENCE, 1)[1].split("```", 1)[0].strip()
        record = json.loads(body)
    except (ValueError, IndexError):
        return None
    if not isinstance(record, dict) or record.get("v") != SCHEMA_VERSION:
        return None
    return record


async def write(channel, record: dict[str, Any]) -> bool:
    """Append one record. Returns False when memory is unavailable."""
    if channel is None:
        return False
    try:
        await channel.send(
            encode(record), allowed_mentions=discord.AllowedMentions.none()
        )
        return True
    except discord.HTTPException:
        log.warning("memory write failed for kind=%r", record.get("kind"))
        return False


async def read_latest(
    channel, kind: str, user_id: int, *, limit: int = 500
) -> dict[str, Any] | None:
    """The newest record of `kind` for `user_id`, or None.

    Newest-first, so the first hit wins and a member who left twice gets what
    they had the last time rather than the first.
    """
    if channel is None:
        return None
    try:
        async for message in channel.history(limit=limit):
            record = decode(getattr(message, "content", ""))
            if record and record.get("kind") == kind and record.get("user") == user_id:
                return record
    except discord.HTTPException:
        log.warning("memory read failed for kind=%r user=%s", kind, user_id)
    return None
