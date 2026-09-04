"""AI chat: mention replies everywhere public, initiative in allow-listed
channels only. A compressed port of superbot's natural-language stage
(disbot/core/runtime/ai/natural_language_stage.py) - every decision leaves
exactly one audit event.

Pipeline gates, in order (donor's order, trimmed):
skip empty/bot/command -> DM skip -> mention check (message.mentions, never
mentioned_in - the @everyone false-ping bug) -> policy (allow-list) ->
keyword heuristic -> addressed-to-someone-else skip -> cooldown + hourly cap ->
mention scrub -> gateway -> deliver with AllowedMentions.none() -> record
memory -> mark cooldown ON DELIVERY -> audit.
"""

from __future__ import annotations

import collections
import contextlib
import logging
import re
import time

import discord
from discord.ext import commands

from spiderbot import audit, style
from spiderbot.ai import safety

log = logging.getLogger("spiderbot.chat")

_KEYWORDS = re.compile(
    r"slingy|spider|swing|silk|tester|opt[ -]?in|closed (alpha|test)|play ?store"
    r"|install|apk|android|bug|crash|how do i|help|anyone know",
    re.IGNORECASE,
)


_MENTION_TOKEN = re.compile(r"<@[!&]?\d+>")


def scrub_mentions(text: str) -> str:
    """Replace every raw mention token with a neutral placeholder.

    Two reasons, both from superbot's BUG-0019 #1. A raw `<@123>` reaching the
    model lets it narrate a ping that never happened ("I've tagged Alice"),
    and echoing numeric Discord IDs is forbidden outright by SYSTEM_SAFETY.
    Applied to the transcript as well as the live message: memory is the
    quieter of the two doors and carried the same tokens.
    """
    return _MENTION_TOKEN.sub(
        lambda m: "@a role" if m.group(0).startswith("<@&") else "@someone", text
    )


#: The mention path is the one every member knows, and until 2026-09-04 it had
#: no brake of any kind: one message, one Anthropic call, unbounded. The
#: initiative path beside it was carefully gated. These are deliberately
#: generous — a real conversation with the bot is a handful of turns — and they
#: exist so that a member cannot spend the API budget or the rate limit at will.
MENTION_COOLDOWN_S = 8.0
MENTION_HOURLY_CAP = 40
#: How many channels' transcripts to keep. `_memory` is keyed by channel id and
#: was never evicted, and any member with Create Public Threads mints new ids at
#: will, so the dict grew without bound.
MAX_REMEMBERED_CHANNELS = 200


class ChatCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.cfg = bot.cfg
        # Per-channel transcript memory: (label, text) tuples, newest last.
        # Ordered so the least-recently-touched channel is the first evicted.
        self._memory: collections.OrderedDict[int, collections.deque] = (
            collections.OrderedDict()
        )
        self._last_initiative: dict[int, float] = {}  # channel id -> epoch
        self._initiative_times: collections.deque = collections.deque(maxlen=200)
        self._last_mention: dict[int, float] = {}  # member id -> epoch
        self._mention_times: collections.deque = collections.deque(maxlen=500)

    # -- memory ------------------------------------------------------------

    def _mem(self, channel_id: int) -> collections.deque:
        if channel_id not in self._memory:
            self._memory[channel_id] = collections.deque(maxlen=self.cfg.ai_memory_turns)
            while len(self._memory) > MAX_REMEMBERED_CHANNELS:
                self._memory.popitem(last=False)  # least recently touched
        else:
            self._memory.move_to_end(channel_id)
        return self._memory[channel_id]

    def _record(self, message: discord.Message) -> None:
        label = (
            "assistant"
            if message.author.id == self.bot.user.id
            else safety.speaker_label(
                message.author.display_name, f"user_{message.author.id % 997}"
            )
        )
        self._mem(message.channel.id).append((label, scrub_mentions(message.content)[:800]))

    def record_own(self, channel_id: int, text: str) -> None:
        self._mem(channel_id).append(("assistant", text[:800]))

    def _payload(self, message: discord.Message, current_text: str) -> str:
        turns = list(self._mem(message.channel.id))
        parts: list[str] = []
        if turns:
            transcript = "\n".join(f"[{label}] {text}" for label, text in turns)
            parts.append(
                "Recent channel messages (newest last):"
                + safety.wrap_untrusted(transcript, kind="recent_channel_turns")
            )
        author = safety.speaker_label(
            message.author.display_name, f"user_{message.author.id % 997}"
        )
        # The label goes INSIDE the wrapper, not into the sentence introducing
        # it. `speaker_label` rejects a hostile name and falls back, but it is
        # a filter, and a filter is the wrong last line: a display name is
        # member-controlled text, so it belongs in the untrusted span with the
        # rest of the member's words. `MEASURED` 2026-09-04: it was the one
        # member-controlled string in the chat prompt outside the markers, and
        # the filter missed U+2028/U+2029/U+0085, so `Bob<U+2028>System: …`
        # rendered as a second line in the operator's own sentence.
        parts.append(
            f"Current message in #{getattr(message.channel, 'name', '') or 'a channel'}:"
            + safety.wrap_untrusted(
                f"[{author}] {scrub_mentions(current_text)}", kind="current_user_message"
            )
        )
        return "\n\n".join(parts)

    # -- pipeline ----------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        # `getattr`, not `.name`: discord.py hands a listener a
        # `PartialMessageable` for a thread that has left the cache — which any
        # member can cause by archiving a thread they created. `MEASURED`
        # 2026-09-04: this listener RAISED `AttributeError` there, breaking
        # `CLAUDE.md` invariant 2 ("no listener may raise") by member action,
        # and the AI chat cog died in that channel before the model call.
        channel_name = getattr(message.channel, "name", "") or ""
        content = message.content or ""
        if not content.strip() or content.startswith(("/", "!")):
            return

        # Direct-mention check: message.mentions, deliberately NOT
        # mentioned_in() which is True for @everyone/@here.
        mentioned = self.bot.user in message.mentions

        if not mentioned:
            # Bystander message: record BEFORE any decision so it can never
            # appear in the context for its own reply.
            self._record(message)
            await self._maybe_initiative(message, content)
            return

        # Mention path - strip the mention tokens; empty remainder = skip.
        stripped = re.sub(rf"[ \t]*<@!?{self.bot.user.id}>[ \t]*", " ", content).strip()
        if not stripped:
            audit.stdout_event("ai_decision", decision="skipped", reason="EMPTY_MESSAGE",
                               channel=channel_name, author=str(message.author))
            self._record(message)
            return
        if not self._mention_allowed(message.author.id):
            audit.stdout_event("ai_decision", decision="denied", reason="MENTION_LIMIT",
                               channel=channel_name, author=str(message.author))
            self._record(message)
            return
        payload = self._payload(message, stripped)
        self._record(message)  # record trigger only after context is gathered
        with contextlib.suppress(discord.HTTPException, AttributeError):
            # `typing()` is a nicety; a channel object that cannot provide it
            # must not stop the answer. A PartialMessageable has no `typing`.
            async with message.channel.typing():
                result = await self.bot.ai.reply(payload, mode="mention")
                await self._deliver(message, result, decision_mode="mention")
                return
        result = await self.bot.ai.reply(payload, mode="mention")
        await self._deliver(message, result, decision_mode="mention")

    def _mention_allowed(self, user_id: int) -> bool:
        """Record this mention and say whether it is within the budget.

        Armed HERE, before the model call — not after a successful delivery.
        That ordering is the defect this file already contains once: the
        initiative cap was armed in `_deliver`, so a member who deleted their
        own message made every reply fail and the cap never counted. Measured
        at 500 calls against a cap of 10. The budget being protected is the
        MODEL CALL, so it cannot depend on anything after it.
        """
        now = time.time()
        if now - self._last_mention.get(user_id, 0.0) < MENTION_COOLDOWN_S:
            return False
        hour_ago = now - 3600
        while self._mention_times and self._mention_times[0] <= hour_ago:
            self._mention_times.popleft()
        if len(self._mention_times) >= MENTION_HOURLY_CAP:
            return False
        self._last_mention[user_id] = now
        self._mention_times.append(now)
        return True

    async def _maybe_initiative(self, message: discord.Message, content: str) -> None:
        cfg = self.cfg
        ch = message.channel
        channel_name = getattr(ch, "name", "") or ""
        if not self.bot.ai.enabled:
            return
        if channel_name not in cfg.initiative_channels:
            return  # unconfigured = silent, always
        if not _KEYWORDS.search(content):
            return  # cheap heuristic gates the API call
        # Addressed to a human, not to us: barging in is the donor's BUG-0019
        # #1, still open there. Audited like the other post-keyword denials so
        # the decision is visible in the logs rather than silently missing.
        if any(getattr(u, "id", None) != self.bot.user.id for u in message.mentions):
            audit.stdout_event("ai_decision", decision="denied",
                               reason="ADDRESSED_TO_OTHERS", channel=channel_name)
            return
        now = time.time()
        if now - self._last_initiative.get(ch.id, 0.0) < cfg.initiative_cooldown_s:
            audit.stdout_event("ai_decision", decision="denied", reason="COOLDOWN_ACTIVE",
                               channel=channel_name)
            return
        hour_ago = now - 3600
        recent = sum(1 for t in self._initiative_times if t > hour_ago)
        if recent >= cfg.initiative_hourly_cap:
            audit.stdout_event("ai_decision", decision="denied", reason="HOURLY_CAP",
                               channel=channel_name)
            return
        # The CAP is armed here, before the model call. `MEASURED` 2026-09-04:
        # it was armed in `_deliver`, AFTER a successful reply — so a member
        # who posted and immediately deleted their own message made every reply
        # fail with "Unknown message" and the counter never moved. 500
        # Anthropic calls against a configured cap of 10, by one member.
        #
        # The COOLDOWN stays on delivery, which is the donor's rule and is
        # right: it spaces out how often the bot SPEAKS, and initiative mode
        # answers PASS most of the time. Consuming 120 seconds on every decline
        # would mean the bot almost never speaks. The two brakes protect
        # different things — the cap protects the API budget, so it arms where
        # the spend happens; the cooldown protects the channel, so it arms
        # where the speech happens.
        self._initiative_times.append(now)
        payload = self._payload(message, content)
        result = await self.bot.ai.reply(payload, mode="initiative")
        if result.text is None:
            audit.stdout_event("ai_decision", decision="skipped" if result.reason == "pass"
                               else "degraded", reason=result.reason.upper(),
                               channel=channel_name, model=result.model)
            return
        await self._deliver(message, result, decision_mode="initiative")

    async def _deliver(self, message: discord.Message, result, *, decision_mode: str) -> None:
        ch = message.channel
        channel_name = getattr(ch, "name", "") or ""
        if result.text is None:
            if result.reason in ("timeout", "error"):
                audit.stdout_event("ai_decision", decision="degraded", reason=result.reason.upper(),
                                   channel=channel_name, mode=decision_mode)
                await audit.modlog_event(
                    self.bot.channels.get("mod-log"), "AI degraded",
                    f"mode={decision_mode} in #{channel_name}: {result.reason}",
                    style.WARNING,
                )
                if decision_mode == "mention":
                    with contextlib.suppress(discord.HTTPException):
                        await message.reply(
                            "My web got tangled - I could not think of an answer just now. "
                            "Menno will see your message!",
                            allowed_mentions=discord.AllowedMentions.none(),
                            mention_author=False,
                        )
            return
        try:
            await message.reply(
                embed=style.ai_embed(result.text),
                allowed_mentions=discord.AllowedMentions.none(),
                mention_author=False,
            )
        except discord.HTTPException:
            log.exception("delivery failed in #%s", channel_name)
            return
        self.record_own(ch.id, result.text)
        if decision_mode == "initiative":
            self._last_initiative[ch.id] = time.time()
        audit.stdout_event(
            "ai_decision", decision="replied", mode=decision_mode, channel=channel_name,
            model=result.model, tokens_in=result.input_tokens, tokens_out=result.output_tokens,
        )
        if decision_mode == "initiative":
            await audit.modlog_event(
                self.bot.channels.get("mod-log"), "AI initiative reply",
                f"In #{ch.name}, responding to {message.author.display_name}:\n"
                f">>> {result.text[:900]}",
                style.AI,
            )


async def setup(bot) -> None:
    await bot.add_cog(ChatCog(bot))
