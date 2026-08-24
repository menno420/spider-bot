"""AI chat: mention replies everywhere public, initiative in allow-listed
channels only. A compressed port of superbot's natural-language stage
(disbot/core/runtime/ai/natural_language_stage.py) - every decision leaves
exactly one audit event.

Pipeline gates, in order (donor's order, trimmed):
skip empty/bot/command -> DM skip -> mention check (message.mentions, never
mentioned_in - the @everyone false-ping bug) -> policy (allow-list) ->
cooldown + hourly cap -> bare-mention strip -> gateway -> deliver with
AllowedMentions.none() -> record memory -> mark cooldown ON DELIVERY -> audit.
"""

from __future__ import annotations

import collections
import logging
import re
import time

import discord
from discord.ext import commands

from spiderbot import audit
from spiderbot.ai import safety

log = logging.getLogger("spiderbot.chat")

_KEYWORDS = re.compile(
    r"slingy|spider|swing|silk|tester|opt[ -]?in|closed (alpha|test)|play ?store"
    r"|install|apk|android|bug|crash|how do i|help|anyone know",
    re.IGNORECASE,
)


class ChatCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.cfg = bot.cfg
        # Per-channel transcript memory: (label, text) tuples, newest last.
        self._memory: dict[int, collections.deque] = {}
        self._last_initiative: dict[int, float] = {}  # channel id -> epoch
        self._initiative_times: collections.deque = collections.deque(maxlen=200)

    # -- memory ------------------------------------------------------------

    def _mem(self, channel_id: int) -> collections.deque:
        if channel_id not in self._memory:
            self._memory[channel_id] = collections.deque(maxlen=self.cfg.ai_memory_turns)
        return self._memory[channel_id]

    def _record(self, message: discord.Message) -> None:
        label = (
            "assistant"
            if message.author.id == self.bot.user.id
            else safety.speaker_label(
                message.author.display_name, f"user_{message.author.id % 997}"
            )
        )
        self._mem(message.channel.id).append((label, message.content[:800]))

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
        parts.append(
            f"Current message in #{message.channel.name} from [{author}]:"
            + safety.wrap_untrusted(current_text, kind="current_user_message")
        )
        return "\n\n".join(parts)

    # -- pipeline ----------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
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
                               channel=message.channel.name, author=str(message.author))
            self._record(message)
            return
        payload = self._payload(message, stripped)
        self._record(message)  # record trigger only after context is gathered
        async with message.channel.typing():
            result = await self.bot.ai.reply(payload, mode="mention")
        await self._deliver(message, result, decision_mode="mention")

    async def _maybe_initiative(self, message: discord.Message, content: str) -> None:
        cfg = self.cfg
        ch = message.channel
        if not self.bot.ai.enabled:
            return
        if ch.name not in cfg.initiative_channels:
            return  # unconfigured = silent, always
        if not _KEYWORDS.search(content):
            return  # cheap heuristic gates the API call
        now = time.time()
        if now - self._last_initiative.get(ch.id, 0.0) < cfg.initiative_cooldown_s:
            audit.stdout_event("ai_decision", decision="denied", reason="COOLDOWN_ACTIVE",
                               channel=ch.name)
            return
        hour_ago = now - 3600
        recent = sum(1 for t in self._initiative_times if t > hour_ago)
        if recent >= cfg.initiative_hourly_cap:
            audit.stdout_event("ai_decision", decision="denied", reason="HOURLY_CAP",
                               channel=ch.name)
            return
        payload = self._payload(message, content)
        result = await self.bot.ai.reply(payload, mode="initiative")
        if result.text is None:
            audit.stdout_event("ai_decision", decision="skipped" if result.reason == "pass"
                               else "degraded", reason=result.reason.upper(),
                               channel=ch.name, model=result.model)
            return
        await self._deliver(message, result, decision_mode="initiative")

    async def _deliver(self, message: discord.Message, result, *, decision_mode: str) -> None:
        ch = message.channel
        if result.text is None:
            if result.reason in ("timeout", "error"):
                audit.stdout_event("ai_decision", decision="degraded", reason=result.reason.upper(),
                                   channel=ch.name, mode=decision_mode)
                await audit.modlog_event(
                    self.bot.channels.get("mod-log"), "AI degraded",
                    f"mode={decision_mode} in #{ch.name}: {result.reason}",
                    discord.Color.orange(),
                )
                if decision_mode == "mention":
                    try:
                        await message.reply(
                            "My web got tangled - I could not think of an answer just now. "
                            "Menno will see your message!",
                            allowed_mentions=discord.AllowedMentions.none(),
                            fail_if_not_exists=False,
                        )
                    except discord.HTTPException:
                        pass
            return
        try:
            await message.reply(
                result.text,
                allowed_mentions=discord.AllowedMentions.none(),
                fail_if_not_exists=False,
            )
        except discord.HTTPException:
            log.exception("delivery failed in #%s", ch.name)
            return
        self.record_own(ch.id, result.text)
        if decision_mode == "initiative":
            self._last_initiative[ch.id] = time.time()
            self._initiative_times.append(time.time())
        audit.stdout_event(
            "ai_decision", decision="replied", mode=decision_mode, channel=ch.name,
            model=result.model, tokens_in=result.input_tokens, tokens_out=result.output_tokens,
        )
        if decision_mode == "initiative":
            await audit.modlog_event(
                self.bot.channels.get("mod-log"), "AI initiative reply",
                f"In #{ch.name}, responding to {message.author.display_name}:\n"
                f">>> {result.text[:900]}",
                discord.Color.blurple(),
            )


async def setup(bot) -> None:
    await bot.add_cog(ChatCog(bot))
