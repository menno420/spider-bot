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

    def _system_blocks(self) -> list[dict]:
        """Persona -> safety -> game knowledge, as one cache-marked block.

        Rebuilt per call because the knowledge half can change under it. The
        cache marker still pays: the block only actually changes when the feed
        refreshes, which is hourly at most, so consecutive calls hit the cache
        exactly as they did when this was a constant.
        """
        return [
            {
                "type": "text",
                "text": PERSONA + "\n" + safety.SYSTEM_SAFETY + "\n" + self._knowledge(),
                "cache_control": {"type": "ephemeral"},
            }
        ]

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def reply(self, payload_text: str, *, mode: str, timeout_s: float = 45.0) -> AIResult:
        """One bounded completion over pre-wrapped payload text.

        mode: "mention" | "initiative". Initiative mode may decline with PASS.
        Never raises.
        """
        if self._client is None:
            return AIResult(None, "disabled")
        if not payload_text.strip():
            return AIResult(None, "pass")
        instruction = _MENTION_INSTRUCTION if mode == "mention" else _INITIATIVE_INSTRUCTION
        instruction = f"{instruction} {_MENTION_PLACEHOLDER_NOTE}"
        content = f"{payload_text}\n\n[operator instruction - not user text: {instruction}]"
        try:
            response = await asyncio.wait_for(
                self._client.messages.create(
                    model=self._cfg.ai_model,
                    max_tokens=self._cfg.ai_max_response_tokens,
                    system=self._system_blocks(),
                    output_config={"effort": self._cfg.ai_effort},
                    messages=[{"role": "user", "content": content}],
                ),
                timeout=timeout_s,
            )
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            usage = response.usage
            if not text or text.strip() == "PASS":
                return AIResult(
                    None, "pass", response.model, usage.input_tokens, usage.output_tokens
                )
            return AIResult(
                text[:1990], "ok", response.model, usage.input_tokens, usage.output_tokens
            )
        except TimeoutError:
            log.warning("AI reply timed out after %ss", timeout_s)
            return AIResult(None, "timeout")
        except anthropic.APIStatusError as exc:
            log.warning("AI API error %s: %s", exc.status_code, exc.message)
            return AIResult(None, "error")
        except Exception:  # the fault boundary: degrade, never raise
            log.exception("AI gateway unexpected failure")
            return AIResult(None, "error")
