"""spiderbot/cogs/serverlog.py - what makes a moderation decision legible later.

The interesting assertions here are about what is NOT logged (bots, other
guilds, unchanged roles) and about member-controlled text never acquiring
authority inside the bot's own embed.
"""

from __future__ import annotations

import types
from asyncio import run

from conftest import FakeChannel, FakeMember, FakeRole, make_cfg

from spiderbot.cogs.serverlog import ServerLogCog

CFG = make_cfg()


def a_bot():
    bot = types.SimpleNamespace(cfg=CFG, channels={"mod-log": FakeChannel(id=3, name="mod-log")})
    bot.get_channel = lambda cid: FakeChannel(id=cid, name="general")
    return bot


def member(name="rin", *, roles=(), bot=False, guild_id=CFG.guild_id):
    m = FakeMember(5, name)
    m.roles = list(roles)
    m.bot = bot
    m.guild = types.SimpleNamespace(id=guild_id)
    return m


def posted(bot) -> str:
    sent = bot.channels["mod-log"].sent
    if not sent:
        return ""
    _args, kwargs = sent[-1]
    embed = kwargs.get("embed")
    return f"{embed.title} {embed.description}"


def test_a_departure_is_logged_with_what_they_held():
    bot = a_bot()
    cog = ServerLogCog(bot)
    run(cog.on_member_remove(member(roles=[FakeRole(id=1, name="Slingy Tester")])))
    assert "left the server" in posted(bot)
    assert "Slingy Tester" in posted(bot)


def test_a_bot_leaving_is_not_logged():
    bot = a_bot()
    run(ServerLogCog(bot).on_member_remove(member(bot=True)))
    assert bot.channels["mod-log"].sent == []


def test_another_guild_is_ignored():
    bot = a_bot()
    run(ServerLogCog(bot).on_member_remove(member(guild_id=999)))
    assert bot.channels["mod-log"].sent == []


def test_role_changes_are_diffed_not_dumped():
    bot = a_bot()
    tester = FakeRole(id=1, name="Slingy Tester")
    other = FakeRole(id=2, name="Regular")
    before = member(roles=[other])
    after = member(roles=[other, tester])
    run(ServerLogCog(bot).on_member_update(before, after))
    text = posted(bot)
    assert "gained" in text and "Slingy Tester" in text
    assert "Regular" not in text, "an unchanged role is noise"


def test_an_unchanged_member_produces_no_log_line():
    """The positive control for the diff: without it this cog is a firehose."""
    bot = a_bot()
    roles = [FakeRole(id=1, name="Slingy Tester")]
    run(ServerLogCog(bot).on_member_update(member(roles=roles), member(roles=roles)))
    assert bot.channels["mod-log"].sent == []


def test_a_deletion_of_an_uncached_message_still_logs_and_says_so():
    """`on_message_delete` misses exactly the old message a moderator asks
    about, so this uses the raw event and states what it cannot show."""
    bot = a_bot()
    payload = types.SimpleNamespace(guild_id=CFG.guild_id, channel_id=1, cached_message=None)
    run(ServerLogCog(bot).on_raw_message_delete(payload))
    text = posted(bot)
    assert "deleted" in text.lower()
    assert "not available" in text


def test_a_cached_deletion_shows_the_content():
    bot = a_bot()
    author = member()
    cached = types.SimpleNamespace(author=author, content="the bird clipped a wall")
    payload = types.SimpleNamespace(guild_id=CFG.guild_id, channel_id=1, cached_message=cached)
    run(ServerLogCog(bot).on_raw_message_delete(payload))
    assert "the bird clipped a wall" in posted(bot)


def test_a_deleted_bot_message_is_not_logged():
    bot = a_bot()
    cached = types.SimpleNamespace(author=member(bot=True), content="an embed")
    payload = types.SimpleNamespace(guild_id=CFG.guild_id, channel_id=1, cached_message=cached)
    run(ServerLogCog(bot).on_raw_message_delete(payload))
    assert bot.channels["mod-log"].sent == []


def test_member_controlled_text_cannot_restructure_the_log_embed():
    bot = a_bot()
    author = member(name="@everyone **mod**")
    cached = types.SimpleNamespace(author=author, content="# HEADING @everyone")
    payload = types.SimpleNamespace(guild_id=CFG.guild_id, channel_id=1, cached_message=cached)
    run(ServerLogCog(bot).on_raw_message_delete(payload))
    text = posted(bot)
    assert "@everyone" not in text
    assert "\\#" in text and "\\*\\*" in text


def test_nothing_is_logged_without_a_mod_log_channel():
    """Unconfigured = silent (invariant 4)."""
    bot = types.SimpleNamespace(cfg=CFG, channels={}, get_channel=lambda c: None)
    run(ServerLogCog(bot).on_member_remove(member()))  # must not raise


def test_a_ban_is_logged_as_an_alarm():
    bot = a_bot()
    guild = types.SimpleNamespace(id=CFG.guild_id)
    run(ServerLogCog(bot).on_member_ban(guild, member()))
    assert "banned" in posted(bot).lower()


def test_a_deletion_flood_cannot_bury_the_mod_log():
    """Codex, spider-bot#3, 2026-09-04: deletion is the one mod-log event an
    ordinary member drives directly — post, delete, repeat — and it had no
    cooldown, no batching and no cap, so a flood could bury moderation cases
    and private-report alerts while filling the bot's HTTP queue."""
    from types import SimpleNamespace

    from spiderbot.cogs.serverlog import DELETION_LOG_CAP

    bot = a_bot()
    cog = ServerLogCog(bot)
    log_channel = bot.channels["mod-log"]

    for n in range(DELETION_LOG_CAP * 5):
        payload = SimpleNamespace(
            guild_id=cog.cfg.guild_id, channel_id=1, cached_message=None, message_id=n
        )
        run(cog.on_raw_message_delete(payload))

    posted = len(log_channel.sent)
    assert posted <= DELETION_LOG_CAP + 1, f"{posted} embeds for a flood"
    # And the flood is VISIBLE as a flood rather than silently dropped.
    titles = " ".join(kw["embed"].title for _a, kw in log_channel.sent if kw.get("embed"))
    assert "too fast" in titles
