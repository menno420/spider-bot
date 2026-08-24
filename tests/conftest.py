"""Shared test doubles: no network, no Discord connection, no real secrets.

Every fake here mimics only the attributes the code under test actually
touches. Async paths run via asyncio.run() inside plain sync tests, so no
pytest async plugin is needed.
"""

from __future__ import annotations

from types import SimpleNamespace

import discord
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

    async def reply(self, text: str | None = None, **kwargs) -> None:
        # The AI answers in a purple embed; the bot's own operational lines
        # stay plain text. Both routes land here.
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


class FakeTree:
    """Just enough command tree for a cog to register a group at construction."""

    def __init__(self) -> None:
        self.added: list = []

    def add_command(self, command, **kwargs) -> None:
        self.added.append((command, kwargs))


class FakeBot:
    def __init__(self, cfg: Config, ai: FakeAI) -> None:
        self.cfg = cfg
        self.ai = ai
        self.user = FakeUser(999, name="Spider Bot", bot=True)
        self.channels: dict = {}
        self.tree = FakeTree()


# -- guild-shaped fakes (roles, members, audit log, interactions) -----------


class FakeRole:
    """Compares by id so `role in member.roles` behaves like Discord's."""

    def __init__(self, id: int = 1, name: str = "Slingy Tester") -> None:
        self.id = id
        self.name = name

    @property
    def mention(self) -> str:
        return f"<@&{self.id}>"

    def __eq__(self, other) -> bool:
        return isinstance(other, FakeRole) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)


class FakeMember(FakeUser):
    def __init__(
        self, id: int, name: str, guild=None, roles: tuple = (), mod: bool = False
    ) -> None:
        super().__init__(id, name)
        self.guild = guild
        self.roles = list(roles)
        self.role_reasons: list = []
        self.guild_permissions = FakePermissions(manage_guild=mod)

    async def add_roles(self, role, reason: str | None = None) -> None:
        self.roles.append(role)
        self.role_reasons.append(("add", reason))

    async def remove_roles(self, role, reason: str | None = None) -> None:
        self.roles = [r for r in self.roles if r != role]
        self.role_reasons.append(("remove", reason))


class FakeAuditEntry:
    """One audit-log row: who was touched, which roles were added, when."""

    def __init__(self, target, added_roles: tuple = (), created_at=None) -> None:
        self.target = target
        self.created_at = created_at
        self.after = SimpleNamespace(roles=list(added_roles))


class _FakeAuditIterator:
    def __init__(self, entries, error) -> None:
        self._entries = list(entries)
        self._error = error
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._error is not None:
            raise self._error
        if self._i >= len(self._entries):
            raise StopAsyncIteration
        entry = self._entries[self._i]
        self._i += 1
        return entry


def forbidden() -> discord.Forbidden:
    """A real discord.Forbidden without needing an HTTP response."""
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Access")


class FakeGuild:
    def __init__(
        self,
        id: int = 1541447750628147351,
        members: tuple = (),
        roles: tuple = (),
        audit_entries: tuple = (),
        audit_error: Exception | None = None,
    ) -> None:
        self.id = id
        self.members = list(members)
        self.roles = list(roles)
        self.audit_calls: list = []
        self._audit_entries = list(audit_entries)
        self._audit_error = audit_error

    def audit_logs(self, *, limit=None, action=None):
        self.audit_calls.append((limit, action))
        return _FakeAuditIterator(self._audit_entries, self._audit_error)


class _FakeResponse:
    def __init__(self) -> None:
        self.messages: list = []
        self.modals: list = []
        self.edits: list = []
        self.deferred = False

    async def send_message(self, content=None, **kwargs) -> None:
        self.messages.append((content, kwargs))

    async def defer(self, **kwargs) -> None:
        self.deferred = True

    async def send_modal(self, modal) -> None:
        self.modals.append(modal)

    async def edit_message(self, **kwargs) -> None:
        self.edits.append(kwargs)

    def is_done(self) -> bool:
        return bool(self.messages or self.modals or self.edits or self.deferred)


class _FakeFollowup:
    def __init__(self) -> None:
        self.messages: list = []

    async def send(self, content=None, **kwargs) -> None:
        self.messages.append((content, kwargs))


class FakePermissions:
    def __init__(self, manage_guild: bool = False, administrator: bool = False) -> None:
        self.manage_guild = manage_guild
        self.administrator = administrator
        self.manage_roles = manage_guild


class FakeMessageHandle:
    """What `original_response()` returns: the handle a panel edits on timeout.

    Ephemeral messages can only be edited through the interaction token, so a
    panel that never obtains this cannot grey its own buttons out.
    """

    def __init__(self, id: int = 555) -> None:
        self.id = id
        self.edits: list = []

    async def edit(self, **kwargs) -> None:
        self.edits.append(kwargs)


class FakeInteraction:
    def __init__(self, guild, user=None, channel=None) -> None:
        self.guild = guild
        self.user = user if user is not None else FakeUser(1, "Menno420")
        self.channel = channel if channel is not None else FakeChannel(name="general")
        self.channel_id = self.channel.id
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()
        self.message = None
        self.original = FakeMessageHandle()

    async def original_response(self) -> FakeMessageHandle:
        return self.original

    async def edit_original_response(self, **kwargs) -> None:
        # Same sink as response.edit_message: both mean "the panel changed",
        # so the `embeds`/`replies` helpers below see either route.
        self.response.edits.append(kwargs)

    @property
    def embeds(self) -> list:
        """Every embed this interaction produced, whichever route it used."""
        out = []
        for _c, kw in [*self.response.messages, *self.followup.messages]:
            if kw.get("embed") is not None:
                out.append(kw["embed"])
        for kw in self.response.edits:
            if kw.get("embed") is not None:
                out.append(kw["embed"])
        return out

    @property
    def replies(self) -> list:
        """Everything the command said back, whichever route it used."""
        return [c for c, _ in self.response.messages] + [c for c, _ in self.followup.messages]


@pytest.fixture
def audit_events(monkeypatch):
    """Capture audit.stdout_event calls (the exactly-one-audit-event rule)."""
    events: list[dict] = []

    def capture(kind: str, **fields):
        events.append({"kind": kind, **fields})

    monkeypatch.setattr(audit, "stdout_event", capture)
    return events
