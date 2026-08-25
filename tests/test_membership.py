"""spiderbot/cogs/membership.py + memory.py - remembering people between visits.

The contract that matters most here is a negative one: the tester role is a
mirror of the real Play cohort, so the bot must never hand it back on its own,
no matter how confident its memory is. Everything else about a returning member
should be automatic.
"""

from __future__ import annotations

import asyncio

from conftest import FakeAI, FakeBot, FakeChannel, FakeGuild, FakeMember, FakeRole, make_cfg

from spiderbot import memory
from spiderbot.cogs.membership import SNAPSHOT, MembershipCog, restorable_roles

TESTER = FakeRole(1, "Slingy Tester", position=5)
FRIEND = FakeRole(2, "Friend of the Web", position=3)
ARTIST = FakeRole(3, "Artist", position=4)
BOOSTER = FakeRole(4, "Server Booster", position=6, managed=True)
EVERYONE = FakeRole(5, "@everyone", position=0, default=True)
ABOVE_BOT = FakeRole(6, "Admin", position=500)


def build(with_state=True):
    bot = FakeBot(make_cfg(), FakeAI())
    bot.channels["mod-log"] = FakeChannel(id=300, name="mod-log")
    if with_state:
        bot.channels["bot-state"] = FakeChannel(id=301, name="bot-state")
    return MembershipCog(bot), bot


def guild_with(*roles):
    return FakeGuild(roles=[EVERYONE, TESTER, FRIEND, ARTIST, BOOSTER, ABOVE_BOT, *roles])


def member(*roles, id=7, name="Alice"):
    g = guild_with()
    m = FakeMember(id, name, guild=g, roles=(EVERYONE, *roles))
    return m


def run(coro):
    return asyncio.run(coro)


# -- what gets remembered ----------------------------------------------------


def test_only_grantable_roles_are_remembered():
    who = member(FRIEND, ARTIST, BOOSTER, ABOVE_BOT, TESTER)
    keep = [r.name for r in restorable_roles(who, who.guild, "Slingy Tester")]
    assert keep == ["Friend of the Web", "Artist"]
    # each exclusion for its own reason: not grantable, integration-owned,
    # above the bot in the hierarchy, and a human-only decision
    for excluded in ("@everyone", "Server Booster", "Admin", "Slingy Tester"):
        assert excluded not in keep


def test_leaving_writes_a_snapshot(audit_events):
    cog, bot = build()
    run(cog.on_member_remove(member(FRIEND, ARTIST)))
    (args, _kw) = bot.channels["bot-state"].sent[0]
    record = memory.decode(args[0])
    assert record["kind"] == SNAPSHOT
    assert [r["name"] for r in record["roles"]] == ["Friend of the Web", "Artist"]
    assert record["was_tester"] is False
    assert [e["kind"] for e in audit_events] == ["member_remembered"]


def test_a_departing_testers_status_is_recorded_even_though_the_role_is_not():
    cog, bot = build()
    run(cog.on_member_remove(member(FRIEND, TESTER)))
    record = memory.decode(bot.channels["bot-state"].sent[0][0][0])
    assert record["was_tester"] is True
    assert "Slingy Tester" not in [r["name"] for r in record["roles"]]


def test_bots_are_not_remembered():
    cog, bot = build()
    who = member(FRIEND)
    who.bot = True
    run(cog.on_member_remove(who))
    assert bot.channels["bot-state"].sent == []


# -- what comes back ---------------------------------------------------------


def test_a_returning_member_gets_their_ordinary_roles_back(audit_events):
    cog, bot = build()
    run(cog.on_member_remove(member(FRIEND, ARTIST)))
    back = member(id=7)
    run(cog.on_member_join(back))
    assert sorted(r.name for r in back.roles if r is not EVERYONE) == [
        "Artist", "Friend of the Web"
    ]
    assert audit_events[-1]["kind"] == "member_returned"


def test_a_returning_tester_never_gets_the_tester_role_back():
    # The whole point: leaving Discord does not prove they stayed opted in on
    # Play, so re-granting would inflate the roster against reality.
    cog, bot = build()
    run(cog.on_member_remove(member(FRIEND, TESTER)))
    back = member(id=7)
    run(cog.on_member_join(back))
    assert "Slingy Tester" not in [r.name for r in back.roles]


def test_a_returning_tester_is_put_to_the_owner_instead():
    cog, bot = build()
    run(cog.on_member_remove(member(FRIEND, TESTER)))
    run(cog.on_member_join(member(id=7)))
    (_args, kwargs) = bot.channels["mod-log"].sent[0]
    body = kwargs["embed"].description
    assert "/tester add" in body
    assert "not" in body.lower(), "it must say plainly that it did not act"
    assert "Friend of the Web" in body, "and what it did restore"


def test_a_returning_non_tester_does_not_bother_the_owner():
    cog, bot = build()
    run(cog.on_member_remove(member(FRIEND)))
    run(cog.on_member_join(member(id=7)))
    assert bot.channels["mod-log"].sent == []


def test_a_genuinely_new_arrival_is_left_to_the_welcome(audit_events):
    cog, bot = build()
    run(cog.on_member_join(member(id=99, name="Stranger")))
    assert audit_events == []
    assert bot.channels["mod-log"].sent == []


def test_the_newest_snapshot_wins():
    # Someone who left twice gets what they had the last time.
    cog, bot = build()
    run(cog.on_member_remove(member(FRIEND, ARTIST)))
    run(cog.on_member_remove(member(ARTIST)))
    back = member(id=7)
    run(cog.on_member_join(back))
    assert [r.name for r in back.roles if r is not EVERYONE] == ["Artist"]


# -- unconfigured = silent (invariant 4) -------------------------------------


def test_without_a_state_channel_nothing_happens_at_all(audit_events):
    cog, bot = build(with_state=False)
    run(cog.on_member_remove(member(FRIEND, TESTER)))
    run(cog.on_member_join(member(id=7)))
    assert audit_events == []
    assert bot.channels["mod-log"].sent == []


# -- the store itself --------------------------------------------------------


def test_human_chatter_in_the_state_channel_is_ignored():
    assert memory.decode("who is this channel for?") is None
    assert memory.decode("") is None


def test_a_record_from_a_future_schema_is_refused():
    # Better a missing memory than a mis-parsed one.
    forged = memory.encode({"kind": SNAPSHOT, "user": 1}).replace('"v": 1', '"v": 99')
    assert memory.decode(forged) is None


def test_reading_an_unconfigured_store_is_not_an_error():
    assert run(memory.read_latest(None, SNAPSHOT, 1)) is None
    assert run(memory.write(None, {"kind": SNAPSHOT})) is False
