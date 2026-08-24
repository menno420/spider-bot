"""Shared test doubles: no network, no Discord connection, no real secrets.

Every fake here mimics only the attributes the code under test actually
touches. Async paths run via asyncio.run() inside plain sync tests, so no
pytest async plugin is needed.
"""

from __future__ import annotations

import pytest

from spiderbot import audit
from spiderbot.ai.gateway import AIResult
from spiderbot.config import Config


def make_cfg(**overrides) -> Config:
    base = dict(
        discord_token="x" * 60,  # structurally a token, obviously not one
        guild_id=1541447750628147351,
        anthropic_api_key=None,
        ai_enabled=False,
        ai_model="claude-opus-5",
        ai_effort="low",
        ai_max_response_tokens=1000,
        ai_memory_turns=20,
        initiative_channels=("general",),
        initiative_cooldown_s=120,
        initiative_hourly_cap=10,
        log_level="INFO",
    )
    base.update(overrides)
    return Config(**base)


class FakeUser:
    def __init__(self, id: int, name: str = "user", bot: bool = False) -> None:
        self.id = id
        self.display_name = name
        self.bot = bot
        self.roles: list = []

    def __str__(self) -> str:
        return self.display_name


class _NullTyping:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *args):
        return False


class FakeChannel:
    def __init__(self, id: int = 100, name: str = "general") -> None:
        self.id = id
        self.name = name
        self.sent: list = []

    def typing(self) -> _NullTyping:
        return _NullTyping()

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))


class FakeMessage:
    def __init__(
        self,
        content: str,
        author: FakeUser,
        channel: FakeChannel,
        mentions: tuple = (),
        guild: object | None = None,
    ) -> None:
        self.content = content
        self.author = author
        self.channel = channel
        self.mentions = list(mentions)
        self.guild = guild if guild is not None else object()
        self.replies: list = []
        self.reactions_added: list = []

    async def reply(self, text: str, **kwargs) -> None:
        self.replies.append((text, kwargs))

    async def add_reaction(self, emoji: str) -> None:
        self.reactions_added.append(emoji)


class FakeAI:
    """Gateway stand-in that records calls and returns a canned AIResult."""

    def __init__(self, result: AIResult | None = None, enabled: bool = True) -> None:
        self.enabled = enabled
        self.result = result if result is not None else AIResult(None, "pass")
        self.calls: list = []

    async def reply(self, payload_text: str, *, mode: str, timeout_s: float = 45.0) -> AIResult:
        self.calls.append((payload_text, mode))
        return self.result


class FakeBot:
    def __init__(self, cfg: Config, ai: FakeAI) -> None:
        self.cfg = cfg
        self.ai = ai
        self.user = FakeUser(999, name="Spider Bot", bot=True)
        self.channels: dict = {}


@pytest.fixture
def audit_events(monkeypatch):
    """Capture audit.stdout_event calls (the exactly-one-audit-event rule)."""
    events: list[dict] = []

    def capture(kind: str, **fields):
        events.append({"kind": kind, **fields})

    monkeypatch.setattr(audit, "stdout_event", capture)
    return events
