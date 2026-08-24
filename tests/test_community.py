"""spiderbot/cogs/community.py - deterministic funnel pieces.

The opted-in watcher surfaces claims to humans and never grants anything
itself (the roster mirrors the real Play cohort - invariant 5 territory).
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import FakeAI, FakeBot, FakeChannel, FakeMessage, FakeUser, make_cfg

from spiderbot.cogs.community import _OPTED_IN, CommunityCog, _steps_embed


@pytest.mark.parametrize(
    "text", ["opted in", "I opted-in!", "just opt in already", "OPT-IN done", "optin"]
)
def test_opted_in_regex_matches(text):
    assert _OPTED_IN.search(text), text


@pytest.mark.parametrize("text", ["options menu", "adopting a cat", "co-opting", "opt out"])
def test_opted_in_regex_rejects(text):
    assert not _OPTED_IN.search(text), text


def test_steps_embed_carries_official_links():
    cfg = make_cfg()
    embed = _steps_embed(cfg)
    assert cfg.group_url in embed.description
    assert cfg.optin_url in embed.description
    assert "tester" in embed.title.lower()


def _cog():
    bot = FakeBot(make_cfg(), FakeAI())
    return CommunityCog(bot), bot


def _claim_message(channel_name="general", roles=()):
    author = FakeUser(5, name="NewTester")
    author.roles = list(roles)
    return FakeMessage("I just opted in!", author, FakeChannel(name=channel_name))


def test_watcher_reacts_and_audits_new_claim(audit_events):
    cog, bot = _cog()
    m = _claim_message()
    asyncio.run(cog.on_message(m))
    assert m.reactions_added  # the spider react
    assert [e["kind"] for e in audit_events] == ["opted_in_claim"]


def test_watcher_never_grants_roles(audit_events):
    # It may react and notify - the role itself moves only via /tester add.
    cog, bot = _cog()
    m = _claim_message()
    asyncio.run(cog.on_message(m))
    assert m.author.roles == []


def test_watcher_ignores_existing_testers(audit_events):
    class _Role:
        name = "Slingy Tester"

    cog, bot = _cog()
    m = _claim_message(roles=(_Role(),))
    asyncio.run(cog.on_message(m))
    assert m.reactions_added == []
    assert audit_events == []


def test_watcher_only_watches_general(audit_events):
    cog, bot = _cog()
    m = _claim_message(channel_name="feedback")
    asyncio.run(cog.on_message(m))
    assert m.reactions_added == []
    assert audit_events == []
