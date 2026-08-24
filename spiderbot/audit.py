"""Audit trail: every AI decision and privileged action leaves a record.

Two sinks, both best-effort and never raising into the caller:
- stdout as one JSON line per event (Railway logs)
- an embed in #mod-log for events the owner should see
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

import discord

from spiderbot import style

log = logging.getLogger("spiderbot.audit")


def stdout_event(kind: str, **fields: Any) -> None:
    rec = {"ts": round(time.time(), 3), "kind": kind, **fields}
    try:
        print(json.dumps(rec, ensure_ascii=False, default=str), file=sys.stdout, flush=True)
    except Exception:  # audit must never break the bot
        log.exception("stdout audit failed")


async def modlog_event(
    channel: discord.TextChannel | None,
    title: str,
    description: str,
    color: discord.Color | None = None,
) -> None:
    if channel is None:
        return
    try:
        embed = discord.Embed(
            title=title[:256],
            description=description[:4000],
            color=color or style.NEUTRAL,
        )
        await channel.send(embed=embed)
    except Exception:  # audit must never break the bot
        log.exception("mod-log audit failed")
