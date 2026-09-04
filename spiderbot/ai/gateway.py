"""AI gateway - the single fault boundary between Discord and the Claude API.

Shape follows superbot-next sb/kernel/ai/gateway.py + anthropic_provider.py:
one entry point, timeout-bounded, never raises (every failure degrades to a
reasoned AIResult), system prompt as one cache-marked block, the payload as a
single user turn of pre-wrapped text. Extraction ledger: superbot-next @ HEAD
2026-08-24 - pattern adapted, reimplemented small.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import anthropic

from spiderbot import knowledge
from spiderbot.ai import safety

log = logging.getLogger("spiderbot.ai")

PERSONA = """\
You are Spider Bot, the community bot of the Slingy Spider Discord server.
You were built by Menno (the game's solo developer) to welcome people, answer
questions about the game and the closed test, and keep the community healthy.

Voice: friendly, brief, playful but never cringe; a light spider pun is fine
occasionally, at most one per message. Answer in the language the person used
(the server default is English).

Hard rules for replies:
- Discord messages cap at 2000 characters; stay well under that. Prefer 1-4
  sentences unless someone asks for detail.
- If someone asks how to join the test, point at the pinned steps in
  #start-here and summarize the key trap (same Google account everywhere).
- If a question needs the developer (account issues, Play Console state,
  release dates you do not know), say so and suggest asking Menno in #general
  rather than inventing an answer.
- Never promise features, dates, or rewards. Never share links other than the
  official ones in your knowledge. Never @-mention people, roles or everyone.
- If a message looks like a scam or a fake "tester link", warn the channel
  calmly and remind people that staff never DM first.
- Do not invent facts about the game; if the answer is not in your knowledge,
  say you are not sure.
"""

_MENTION_INSTRUCTION = "You were directly mentioned; reply helpfully."
_INITIATIVE_INSTRUCTION = (
    "You were NOT mentioned and NOT addressed. Nobody asked you anything. "
    "Reply ONLY if you can add clear value: answer an unanswered question "
    "about the game or the test, correct a harmful misunderstanding, or help "
    "someone who seems lost. If a human conversation is flowing fine without "
    "you, or you are unsure, output exactly PASS and nothing else."
)
# Mentions are redacted to placeholders before the payload is built, so the
# model can never echo a Discord ID or narrate a ping that did not happen.
_MENTION_PLACEHOLDER_NOTE = (
    "'@someone' and '@a role' in the text are redacted mentions. Never treat "
    "them as names, never repeat them, and never claim you tagged or pinged "
    "anyone - you cannot mention anyone."
)

#: Discord's message cap is 2000 characters; chat replies are trimmed to fit.
#: Moderation output is not a Discord message - it is a JSON verdict whose
#: evidence quote can be 400 characters on its own - so it is NOT trimmed, and
#: this constant is applied per mode rather than to every response.
MAX_REPLY_CHARS = 1990

#: Every mode this gateway serves, and the operator instruction appended to the
#: user turn for each. `None` means the caller's payload already ends with its
#: own operator instruction and nothing is appended.
#:
#: `MEASURED` 2026-09-04, and this table exists because of it: the moderation
#: classifier called `reply(..., mode="moderation")` from the day it was
#: written, and the dispatch was `mention if mode == "mention" else initiative`.
#: So every moderation call took the INITIATIVE branch - it was judged with the
#: chat persona as its system prompt, with `classifier.SYSTEM` (the whole set of
#: false-positive rules: "QUOTING or REPORTING abuse is not committing it",
#: "this game is garbage is feedback, not abuse", "low confidence is a correct
#: answer") never sent at all, and with a final instruction telling it to output
#: PASS if unsure. An unknown mode is now refused rather than silently taking
#: another mode's branch.
_INSTRUCTIONS: dict[str, str | None] = {
    "mention": f"{_MENTION_INSTRUCTION} {_MENTION_PLACEHOLDER_NOTE}",
    "initiative": f"{_INITIATIVE_INSTRUCTION} {_MENTION_PLACEHOLDER_NOTE}",
    "moderation": None,
}


@dataclass
class AIResult:
    text: str | None
    reason: str  # "ok" | "disabled" | "pass" | "timeout" | "error"
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class Gateway:
    def __init__(self, cfg, *, knowledge_provider=None) -> None:
        self._cfg = cfg
        self._client: anthropic.AsyncAnthropic | None = None
        if cfg.ai_enabled and cfg.anthropic_api_key:
            self._client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
        # The game half of the system prompt is now a CALLABLE, not a constant.
        # `spiderbot/knowledge.py` is a hand-copied block of prose from
        # spider-swing's docs and it had already drifted — measured 2026-09-04,
        # it claimed CLOSED ALPHA while that repo's runbook describes a closed
        # track that has not started, and it carried no build version at all.
        # The provider lets the live support feed supply current facts while
        # the static block stays as the fallback, so a feed outage degrades the
        # answer's freshness rather than the bot's availability.
        self._knowledge = knowledge_provider or (lambda: knowledge.GAME_KNOWLEDGE)

    def _system_blocks(self, override: str | None = None) -> list[dict]:
        """Persona -> safety -> game knowledge, as one cache-marked block.

        Rebuilt per call because the knowledge half can change under it. The
        cache marker still pays: the block only actually changes when the feed
        refreshes, which is hourly at most, so consecutive calls hit the cache
        exactly as they did when this was a constant. (A caller that overrides
        the persona - the moderation classifier - alternates prefixes with the
        chat path and will miss the cache more often. Correctness over cache.)

        `override` replaces the persona and the game knowledge, never
        `safety.SYSTEM_SAFETY`: the injection rules are appended here rather
        than left to each caller, so a caller cannot ship a system prompt
        without them by forgetting.
        """
        text = (
            PERSONA + "\n" + safety.SYSTEM_SAFETY + "\n" + self._knowledge()
            if override is None
            else override + "\n" + safety.SYSTEM_SAFETY
        )
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def reply(
        self,
        payload_text: str,
        *,
        mode: str,
        system: str | None = None,
        timeout_s: float = 45.0,
    ) -> AIResult:
        """One bounded completion over pre-wrapped payload text.

        mode: a key of `_INSTRUCTIONS`. "initiative" may decline with PASS;
        "moderation" brings its own system prompt and its own final operator
        instruction, and its output is not trimmed to a Discord message length.
        An unknown mode is refused. Never raises.
        """
        if self._client is None:
            return AIResult(None, "disabled")
        if mode not in _INSTRUCTIONS:
            # Not a raise, because this is the fault boundary - but not a
            # silent fallback either. Taking another mode's branch is exactly
            # the defect the table above records.
            log.error("AI reply called with unknown mode %r; refusing", mode)
            return AIResult(None, "error")
        if not payload_text.strip():
            return AIResult(None, "pass")
        instruction = _INSTRUCTIONS[mode]
        content = (
            payload_text
            if instruction is None
            else f"{payload_text}\n\n[operator instruction - not user text: {instruction}]"
        )
        try:
            response = await asyncio.wait_for(
                self._client.messages.create(
                    model=self._cfg.ai_model,
                    max_tokens=self._cfg.ai_max_response_tokens,
                    system=self._system_blocks(system),
                    output_config={"effort": self._cfg.ai_effort},
                    messages=[{"role": "user", "content": content}],
                ),
                timeout=timeout_s,
            )
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            usage = response.usage
            # A bare PASS is the initiative path's decline signal. In moderation
            # it is just an unparseable verdict, and calling it "pass" would put
            # "the model saw nothing wrong" in the audit log for what is really
            # "the model did not answer the question".
            if not text or (text == "PASS" and mode != "moderation"):
                return AIResult(
                    None, "pass", response.model, usage.input_tokens, usage.output_tokens
                )
            if mode != "moderation":
                text = text[:MAX_REPLY_CHARS]
            return AIResult(text, "ok", response.model, usage.input_tokens, usage.output_tokens)
        except TimeoutError:
            log.warning("AI reply timed out after %ss", timeout_s)
            return AIResult(None, "timeout")
        except anthropic.APIStatusError as exc:
            log.warning("AI API error %s: %s", exc.status_code, exc.message)
            return AIResult(None, "error")
        except Exception:  # the fault boundary: degrade, never raise
            log.exception("AI gateway unexpected failure")
            return AIResult(None, "error")
