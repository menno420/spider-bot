"""Server event logging — what makes a moderation decision legible afterwards.

`docs/superbot-reuse-map.md` ranks this second, and the reason it gives is the
right one: *"joins, leaves, role changes, deletes — this is what makes a kick
legible after the fact."* A moderation system that records only its own
decisions can tell you what it did and not what was happening around it, and
the question a moderator actually asks a week later is *"what led to this?"*

**Deliberately not a firehose.** Discord's own audit log already exists and is
better at completeness. What this adds is the events that matter to THIS
server's two jobs — the tester funnel and moderation — in one channel a human
reads, correlated with the cases beside them.

Three design choices worth stating:

- **`on_raw_message_delete` rather than `on_message_delete`.** The non-raw
  event fires only for messages in discord.py's in-memory cache
  (`discord/state.py:692-699`), so a deletion of anything older than the
  current process would silently not be logged — which is precisely the
  deletion a moderator is most likely to be asking about.
- **Role changes are diffed, not dumped.** "gained Slingy Tester" is readable;
  a before/after list of every role is not, and the tester role is the one this
  server is ranked on.
- **Unconfigured is silent.** No `#mod-log`, no logging, no complaint.

Every send is `AllowedMentions.none()` and every member name goes through the
Discord escaper, because a display name is member-controlled text arriving in
the bot's own embed.
"""

from __future__ import annotations

import logging
import time
from collections import deque

import discord
from discord.ext import commands, tasks

from spiderbot import audit, redact, style

log = logging.getLogger("spiderbot.serverlog")


#: How many deletion notices reach the mod log in a window, and how wide the
#: window is. Codex, spider-bot#3, 2026-09-04: every deletion produced one
#: embed with no cooldown or cap, so a member could post-and-delete repeatedly
#: to bury moderation cases and private-report alerts under their own noise —
#: and fill the bot's HTTP queue doing it. Past the cap the notices are counted
#: rather than posted, and one line says how many were withheld, so a flood is
#: VISIBLE as a flood instead of drowning what matters.
DELETION_LOG_CAP = 10
DELETION_LOG_WINDOW_S = 60.0


class ServerLogCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.cfg = bot.cfg
        self._deletion_times: deque[float] = deque(maxlen=DELETION_LOG_CAP * 4)
        self._deletions_withheld = 0

    @property
    def _log_channel(self):
        return self.bot.channels.get(self.cfg.ch_mod_log)

    async def _post(self, title: str, description: str, colour) -> None:
        await audit.modlog_event(self._log_channel, title, description, colour)

    @tasks.loop(seconds=DELETION_LOG_WINDOW_S)
    async def _flush_withheld(self) -> None:
        """Say how many deletion notices were withheld, once the flood stops."""
        if not self._deletions_withheld:
            self._flush_withheld.stop()
            return
        if not self._deletion_budget():
            return  # still flooding; the count keeps rising
        withheld, self._deletions_withheld = self._deletions_withheld, 0
        await self._post(
            f"{style.WARN} Deletion notices resumed",
            f"{withheld} deletion notice(s) were withheld while messages were "
            "being deleted faster than this channel can usefully carry. "
            "Discord's own audit log has every one of them.",
            style.WARNING,
        )
        self._flush_withheld.stop()

    def _deletion_budget(self) -> bool:
        """Whether this deletion notice may be posted.

        Deletion is the one mod-log event an ordinary member drives directly:
        post, delete, repeat. Past the cap the notices are counted instead of
        posted and the next one that fits says how many were withheld — so a
        flood reads AS a flood rather than as the mod log filling with noise.
        """
        now = time.time()
        cutoff = now - DELETION_LOG_WINDOW_S
        while self._deletion_times and self._deletion_times[0] <= cutoff:
            self._deletion_times.popleft()
        if len(self._deletion_times) >= DELETION_LOG_CAP:
            return False
        self._deletion_times.append(now)
        return True

    def _in_guild(self, obj) -> bool:
        guild = getattr(obj, "guild", None)
        return getattr(guild, "id", None) == self.cfg.guild_id

    # -- membership ----------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """A leave. The welcome cog owns joins; a departure has no other home."""
        if not self._in_guild(member) or getattr(member, "bot", False):
            return
        roles = [
            style.escape_name(r.name)
            for r in (getattr(member, "roles", ()) or ())
            if not getattr(r, "is_default", lambda: False)()
        ]
        await self._post(
            f"{style.WEB} Member left",
            f"**{style.escape_name(member.display_name)}** left the server."
            + (f"\nThey held: {', '.join(roles)}." if roles else ""),
            style.NEUTRAL,
        )
        audit.stdout_event("server_member_left", user=str(member), roles=len(roles))

    @commands.Cog.listener()
    async def on_member_update(self, before, after) -> None:
        """Role changes, diffed. The tester role is the one that matters here."""
        if not self._in_guild(after):
            return
        was = {getattr(r, "name", "") for r in (getattr(before, "roles", ()) or ())}
        now = {getattr(r, "name", "") for r in (getattr(after, "roles", ()) or ())}
        gained, lost = now - was, was - now
        if not gained and not lost:
            return
        def named(roles) -> str:
            return ", ".join(f"**{style.escape_name(r)}**" for r in sorted(roles))

        lines = []
        if gained:
            lines.append("gained " + named(gained))
        if lost:
            lines.append("lost " + named(lost))
        await self._post(
            f"{style.GEAR} Roles changed",
            f"**{style.escape_name(after.display_name)}** " + " and ".join(lines) + ".",
            style.NEUTRAL,
        )
        audit.stdout_event(
            "server_roles_changed",
            user=str(after),
            gained=sorted(gained),
            lost=sorted(lost),
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user) -> None:
        if getattr(guild, "id", None) != self.cfg.guild_id:
            return
        await self._post(
            f"{style.SIREN} Member banned",
            f"**{style.escape_name(str(user))}** was banned. Check the audit log "
            "for who did it and why.",
            style.ALARM,
        )
        audit.stdout_event("server_member_banned", user=str(user))

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user) -> None:
        if getattr(guild, "id", None) != self.cfg.guild_id:
            return
        await self._post(
            f"{style.OK} Ban lifted",
            f"**{style.escape_name(str(user))}** was unbanned.",
            style.NEUTRAL,
        )
        audit.stdout_event("server_member_unbanned", user=str(user))

    # -- messages ------------------------------------------------------------

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload) -> None:
        """A deletion, whether or not the message was cached.

        `on_message_delete` fires only for messages discord.py still holds in
        memory, so it misses exactly the old message a moderator is asking
        about. The raw event always fires; the trade is that `cached_message`
        may be None, which is stated in the log line rather than hidden.
        """
        if getattr(payload, "guild_id", None) != self.cfg.guild_id:
            return
        cached = getattr(payload, "cached_message", None)
        if cached is not None and getattr(cached.author, "bot", False):
            return  # our own embeds and other bots' output are noise here
        channel = self.bot.get_channel(getattr(payload, "channel_id", 0))
        where = f"#{getattr(channel, 'name', '?')}"
        if cached is None:
            body = (
                f"A message was deleted in {where}. It was posted before the "
                "bot last restarted, so its content is not available here — "
                "Discord's own audit log has who deleted it."
            )
        else:
            body = (
                f"**{style.escape_name(cached.author.display_name)}**'s message "
                f"in {where} was deleted:\n>>> "
                + redact.for_discord(cached.content or "(no text)", limit=900)
            )
        if not self._deletion_budget():
            self._deletions_withheld += 1
            # Reported when the flood ENDS, not only when the next notice
            # happens to fit. Gemini (free-key review of this fix, 2026-09-04):
            # if the deletions simply stop, the count was never flushed and
            # staff never learned how many they had not been shown.
            if not self._flush_withheld.is_running():
                self._flush_withheld.start()
            if self._deletions_withheld == 1:
                await self._post(
                    f"{style.WARN} Deletions are being logged too fast",
                    "More than "
                    f"{DELETION_LOG_CAP} messages were deleted in "
                    f"{DELETION_LOG_WINDOW_S:.0f} seconds, so I have stopped "
                    "posting one notice each — otherwise a flood buries the "
                    "cases and reports you actually need to see. Discord's own "
                    "audit log has every deletion.",
                    style.WARNING,
                )
            return
        if self._deletions_withheld:
            withheld, self._deletions_withheld = self._deletions_withheld, 0
            body = f"_{withheld} earlier deletion notice(s) were withheld._\n\n" + body
        await self._post(f"{style.WARN} Message deleted", body, style.WARNING)
        audit.stdout_event(
            "server_message_deleted",
            channel=getattr(channel, "name", None),
            had_content=cached is not None,
        )


async def setup(bot) -> None:
    await bot.add_cog(ServerLogCog(bot))
