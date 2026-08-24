"""spiderbot/cogs/community.py - deterministic funnel pieces.

The opted-in watcher surfaces claims to humans and never grants anything
itself (the roster mirrors the real Play cohort - invariant 5 territory).
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import (
    FakeAI,
    FakeBot,
    FakeChannel,
    FakeGuild,
    FakeMember,
    FakeMessage,
    FakeUser,
    make_cfg,
)

from spiderbot.cogs.community import _OPTED_IN, CommunityCog
from spiderbot.presets import steps_embed
from spiderbot.ui.home import build_pinned_home, build_welcome_panel
from spiderbot.ui.routes import ROUTES_BY_KEY


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
    embed = steps_embed(cfg)
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


# -- the welcome (Phase 1) ---------------------------------------------------
# Grok's sequence: welcome -> the one button that matters -> tester role. The
# greeting carries the button itself, so it works whether or not /panel has
# been run, and a newcomer never has to learn a command.


def _welcome_cog():
    bot = FakeBot(make_cfg(), FakeAI())
    bot.channels["general"] = FakeChannel(id=100, name="general")
    bot.channels["start-here"] = FakeChannel(id=101, name="start-here")
    return CommunityCog(bot), bot


def _join(cog, bot, joiner=None):
    joiner = joiner or FakeMember(77, "Newcomer", guild=FakeGuild())
    asyncio.run(cog.on_member_join(joiner))
    return joiner, bot.channels["general"].sent


def test_the_welcome_offers_exactly_one_next_step():
    cog, bot = _welcome_cog()
    _joiner, sent = _join(cog, bot)
    (_args, kwargs) = sent[0]
    view = kwargs["view"]
    assert len(view.children) == 1, "a newcomer gets one button, not a menu"
    assert view.children[0].label == ROUTES_BY_KEY["join"].label


def test_the_welcome_never_tells_anyone_to_type_a_command():
    # "App-like: buttons and menus, not typing." The old copy said `/jointest`.
    cog, bot = _welcome_cog()
    _joiner, sent = _join(cog, bot)
    (_args, kwargs) = sent[0]
    text = kwargs["content"] + kwargs["embed"].description
    assert "/jointest" not in text
    assert "/home" not in text


def test_the_welcome_pings_the_newcomer_and_nobody_else():
    # Invariant 8: the greeting is the one deliberate exception to none().
    cog, bot = _welcome_cog()
    joiner, sent = _join(cog, bot)
    (_args, kwargs) = sent[0]
    mentions = kwargs["allowed_mentions"]
    assert mentions.users == [joiner]
    # Spelled out at the call site rather than inherited from the client-wide
    # default: discord.py leaves unset fields as a sentinel that reads as True,
    # so "narrow" has to be stated, not assumed.
    assert mentions.everyone is False
    assert mentions.roles is False
    assert mentions.replied_user is False
    assert joiner.mention in kwargs["content"]


def test_the_welcome_points_at_start_here_for_everything_else():
    cog, bot = _welcome_cog()
    _joiner, sent = _join(cog, bot)
    (_args, kwargs) = sent[0]
    assert bot.channels["start-here"].mention in kwargs["embed"].description


def test_the_welcome_button_survives_a_deploy():
    # Discord matches a button back to a registered persistent view by
    # custom_id; without a stable one, every older greeting goes dead.
    cog, bot = _welcome_cog()
    _joiner, sent = _join(cog, bot)
    view = sent[0][1]["view"]
    assert view.timeout is None
    assert view.public is True
    assert view.children[0].custom_id == "spiderbot:welcome:join"


def test_the_welcome_button_cannot_collide_with_the_pinned_panel():
    # Two persistent views registered at boot: overlapping custom_ids would
    # make one silently shadow the other.
    bot = FakeBot(make_cfg(), FakeAI())
    pinned = {c.custom_id for c in build_pinned_home(bot)[1].children}
    welcome = {c.custom_id for c in build_welcome_panel(bot).children}
    assert pinned and welcome and not (pinned & welcome)


def test_bots_are_not_welcomed():
    cog, bot = _welcome_cog()
    other = FakeMember(88, "SomeBot", guild=FakeGuild())
    other.bot = True
    asyncio.run(cog.on_member_join(other))
    assert bot.channels["general"].sent == []


def test_a_missing_general_channel_degrades_quietly():
    cog, bot = _welcome_cog()
    bot.channels.pop("general")
    asyncio.run(cog.on_member_join(FakeMember(77, "Newcomer", guild=FakeGuild())))
    assert bot.channels["start-here"].sent == []
