"""spiderbot/cogs/tester.py - roster commands, the clock read, the streak alarm.

Two behaviours matter beyond the arithmetic (covered in test_cohort.py):
the audit-log read must degrade instead of refusing when the bot cannot see
the log, and a tester losing the role must reach the owner *without* being
asked - except when a mod removed it deliberately.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from conftest import (
    FakeAI,
    FakeAuditEntry,
    FakeBot,
    FakeChannel,
    FakeGuild,
    FakeInteraction,
    FakeMember,
    FakeRole,
    forbidden,
    make_cfg,
)

from spiderbot.cogs.tester import RosterCog, cohort_status, grant_dates

TESTER = FakeRole(1, "Slingy Tester")
OTHER = FakeRole(2, "Server Booster")
NOW = datetime.now(UTC)


def build(members=(), audit_entries=(), audit_error=None, roles=(TESTER, OTHER)):
    """A cog wired to a fake guild; returns (cog, group, bot, guild)."""
    bot = FakeBot(make_cfg(), FakeAI())
    bot.channels["mod-log"] = FakeChannel(name="mod-log")
    bot.channels["general"] = FakeChannel(name="general")
    cog = RosterCog(bot)
    group = bot.tree.added[0][0]
    guild = FakeGuild(
        id=bot.cfg.guild_id,
        members=members,
        roles=roles,
        audit_entries=audit_entries,
        audit_error=audit_error,
    )
    for m in members:
        m.guild = guild
    return cog, group, bot, guild


def modlog(bot) -> str:
    """All mod-log embed text, flattened."""
    out = []
    for _args, kwargs in bot.channels["mod-log"].sent:
        embed = kwargs.get("embed")
        if embed is not None:
            out.append(f"{embed.title}\n{embed.description}")
    return "\n".join(out)


# -- reading grant dates out of the audit log -------------------------------


def test_grant_dates_reads_the_role_grant():
    m = FakeMember(7, "Alice", roles=[TESTER])
    guild = FakeGuild(audit_entries=[FakeAuditEntry(m, (TESTER,), NOW - timedelta(days=3))])
    dates, readable = asyncio.run(grant_dates(guild, TESTER))
    assert readable is True
    assert dates[7] == NOW - timedelta(days=3)


def test_grant_dates_takes_the_most_recent_grant():
    # A re-grant restarts the streak: newest entry wins, oldest is ignored.
    m = FakeMember(7, "Alice", roles=[TESTER])
    guild = FakeGuild(
        audit_entries=[  # Discord returns newest first
            FakeAuditEntry(m, (TESTER,), NOW - timedelta(days=2)),
            FakeAuditEntry(m, (TESTER,), NOW - timedelta(days=30)),
        ]
    )
    dates, _ = asyncio.run(grant_dates(guild, TESTER))
    assert dates[7] == NOW - timedelta(days=2)


def test_grant_dates_ignores_other_roles():
    m = FakeMember(7, "Alice")
    guild = FakeGuild(audit_entries=[FakeAuditEntry(m, (OTHER,), NOW)])
    dates, readable = asyncio.run(grant_dates(guild, TESTER))
    assert dates == {} and readable is True


def test_grant_dates_degrades_when_the_log_is_forbidden():
    guild = FakeGuild(audit_error=forbidden())
    dates, readable = asyncio.run(grant_dates(guild, TESTER))
    assert dates == {}
    assert readable is False, "no permission must degrade, not raise (invariant 2)"


def test_grant_dates_scans_a_bounded_window():
    guild = FakeGuild(audit_entries=[])
    asyncio.run(grant_dates(guild, TESTER))
    limit, _action = guild.audit_calls[0]
    assert isinstance(limit, int) and limit > 0


# -- combining the roster with the dates ------------------------------------


def test_cohort_status_pairs_holders_with_their_grant_dates():
    alice = FakeMember(7, "Alice", roles=[TESTER])
    bob = FakeMember(8, "Bob", roles=[TESTER])
    bystander = FakeMember(9, "Nobody", roles=[OTHER])
    guild = FakeGuild(
        members=[alice, bob, bystander],
        audit_entries=[FakeAuditEntry(alice, (TESTER,), NOW - timedelta(days=20))],
    )
    status, readable = asyncio.run(cohort_status(guild, TESTER))
    assert readable is True
    assert status.roster == 2, "only role holders count"
    names = {s.name: s for s in status.standings}
    assert names["Alice"].qualified is True
    assert names["Bob"].days is None, "no audit row means unknown, never assumed"
    assert status.unknown_dates == 1


# -- /tester count -----------------------------------------------------------


def _run_count(cog, group, guild, user=None):
    interaction = FakeInteraction(guild, user)
    asyncio.run(group.count.callback(group, interaction))
    return interaction


def test_count_defers_before_the_network_read():
    cog, group, bot, guild = build()
    interaction = _run_count(cog, group, guild)
    assert interaction.response.deferred is True


def test_count_reports_the_standing(audit_events):
    alice = FakeMember(7, "Alice", roles=[TESTER])
    cog, group, bot, guild = build(
        members=[alice],
        audit_entries=[FakeAuditEntry(alice, (TESTER,), NOW - timedelta(days=5))],
    )
    interaction = _run_count(cog, group, guild)
    text = "\n".join(interaction.replies)
    assert "Alice - day 5, 9 to go" in text
    assert "1 verified tester" in text
    assert [e["kind"] for e in audit_events] == ["cohort_reported"]


def test_count_explains_a_missing_audit_permission():
    cog, group, bot, guild = build(audit_error=forbidden())
    text = "\n".join(_run_count(cog, group, guild).replies)
    assert "View Audit Log" in text


def test_count_without_the_role_says_so_and_does_not_defer():
    cog, group, bot, guild = build(roles=())
    interaction = _run_count(cog, group, guild)
    assert interaction.response.deferred is False
    assert "not found" in "\n".join(interaction.replies)


def test_count_on_an_empty_roster_is_still_useful():
    cog, group, bot, guild = build()
    text = "\n".join(_run_count(cog, group, guild).replies)
    assert "No verified testers yet" in text


# -- /tester add -------------------------------------------------------------


def test_add_grants_announces_and_audits(audit_events):
    newbie = FakeMember(7, "Newbie")
    cog, group, bot, guild = build(members=[newbie])
    interaction = FakeInteraction(guild)
    asyncio.run(group.add.callback(group, interaction, newbie))
    assert TESTER in newbie.roles
    assert "11 more to reach 12" in "\n".join(interaction.replies)
    assert bot.channels["general"].sent, "the roster milestone is celebrated publicly"
    assert [e["kind"] for e in audit_events] == ["tester_granted"]


# -- the streak alarm --------------------------------------------------------


def test_role_loss_alerts_the_owner(audit_events):
    cog, group, bot, guild = build()
    before = FakeMember(7, "Alice", guild=guild, roles=[TESTER])
    after = FakeMember(7, "Alice", guild=guild, roles=[])
    asyncio.run(cog.on_member_update(before, after))
    assert "Tester streak broken" in modlog(bot)
    assert [e["kind"] for e in audit_events] == ["tester_streak_broken"]


def test_deliberate_removal_does_not_double_alert(audit_events):
    cog, group, bot, guild = build()
    holder = FakeMember(7, "Alice", guild=guild, roles=[TESTER])
    interaction = FakeInteraction(guild)
    asyncio.run(group.remove.callback(group, interaction, holder))
    # /tester remove logs its own event; the alarm must stay quiet.
    after = FakeMember(7, "Alice", guild=guild, roles=[])
    asyncio.run(cog.on_member_update(FakeMember(7, "Alice", guild=guild, roles=[TESTER]), after))
    assert "streak broken" not in modlog(bot).lower()
    assert [e["kind"] for e in audit_events] == ["tester_removed"]


def test_suppression_applies_only_once(audit_events):
    cog, group, bot, guild = build()
    cog.expect_removal(7)
    held = FakeMember(7, "Alice", guild=guild, roles=[TESTER])
    lost = FakeMember(7, "Alice", guild=guild, roles=[])
    asyncio.run(cog.on_member_update(held, lost))  # suppressed
    asyncio.run(cog.on_member_update(held, lost))  # a real loss now
    assert [e["kind"] for e in audit_events] == ["tester_streak_broken"]


def test_non_tester_role_changes_are_ignored(audit_events):
    cog, group, bot, guild = build()
    before = FakeMember(7, "Alice", guild=guild, roles=[OTHER])
    after = FakeMember(7, "Alice", guild=guild, roles=[])
    asyncio.run(cog.on_member_update(before, after))
    assert audit_events == []


def test_tester_leaving_the_server_alerts(audit_events):
    cog, group, bot, guild = build()
    leaver = FakeMember(7, "Alice", guild=guild, roles=[TESTER])
    asyncio.run(cog.on_member_remove(leaver))
    assert "left the server" in modlog(bot)
    assert [e["kind"] for e in audit_events] == ["tester_streak_broken"]


def test_non_tester_leaving_is_not_an_alarm(audit_events):
    cog, group, bot, guild = build()
    asyncio.run(cog.on_member_remove(FakeMember(7, "Rando", guild=guild, roles=[])))
    assert audit_events == []


def test_other_guilds_are_ignored(audit_events):
    cog, group, bot, guild = build()
    elsewhere = FakeGuild(id=999)
    asyncio.run(cog.on_member_remove(FakeMember(7, "Alice", guild=elsewhere, roles=[TESTER])))
    assert audit_events == []
