"""spiderbot/cogs/chat.py - the AI decision pipeline.

Covers the invariants that live here: mention detection via message.mentions
(the @everyone false-ping class), unconfigured = silent, cooldown marked only
on delivery, AllowedMentions.none() on every send, and exactly one audit
event per decision.
"""

from __future__ import annotations

import asyncio
import time

import discord
from conftest import FakeAI, FakeBot, FakeChannel, FakeMessage, FakeUser, make_cfg

from spiderbot.ai.gateway import AIResult
from spiderbot.cogs.chat import ChatCog

REPLY = AIResult("Try /jointest!", "ok", model="claude-opus-5", input_tokens=9, output_tokens=3)
KEYWORDED = "anyone know how do i install the slingy build?"
SMALLTALK = "nice weather today, went for a walk"


def build(cfg=None, ai=None):
    ai = ai if ai is not None else FakeAI(REPLY)
    bot = FakeBot(cfg or make_cfg(), ai)
    return ChatCog(bot), bot, ai


def msg(content, *, channel=None, mentions=(), author=None):
    return FakeMessage(
        content,
        author or FakeUser(1, name="Menno"),
        channel or FakeChannel(),
        mentions=mentions,
    )


def run(cog, message):
    asyncio.run(cog.on_message(message))


# -- skip gates -------------------------------------------------------------


def test_bot_authors_are_ignored():
    cog, bot, ai = build()
    m = msg(KEYWORDED, author=FakeUser(2, bot=True))
    run(cog, m)
    assert ai.calls == []
    assert cog._memory == {}


def test_command_shaped_messages_are_ignored():
    cog, bot, ai = build()
    for content in ("/status", "!help"):
        run(cog, msg(content))
    assert ai.calls == []
    assert cog._memory == {}


def test_dms_are_ignored():
    cog, bot, ai = build()
    m = msg(KEYWORDED)
    m.guild = None
    run(cog, m)
    assert ai.calls == []


# -- initiative policy ------------------------------------------------------


def test_bystander_message_is_recorded_but_silent_off_allowlist(audit_events):
    cog, bot, ai = build(make_cfg(initiative_channels=("general",)))
    run(cog, msg(KEYWORDED, channel=FakeChannel(name="feedback")))
    assert ai.calls == []
    assert len(cog._mem(100)) == 1  # memory keeps flowing for later context


def test_unconfigured_allowlist_means_silent_everywhere():
    # Invariant 4: no allow-list entry, no initiative - ever.
    cog, bot, ai = build(make_cfg(initiative_channels=()))
    run(cog, msg(KEYWORDED, channel=FakeChannel(name="general")))
    assert ai.calls == []


def test_ai_disabled_means_no_initiative():
    cog, bot, ai = build(ai=FakeAI(REPLY, enabled=False))
    run(cog, msg(KEYWORDED))
    assert ai.calls == []


def test_no_keyword_no_api_call():
    cog, bot, ai = build()
    run(cog, msg(SMALLTALK))
    assert ai.calls == []


def test_initiative_happy_path_delivers_and_audits(audit_events):
    cog, bot, ai = build()
    m = msg(KEYWORDED)
    run(cog, m)
    assert [mode for _, mode in ai.calls] == ["initiative"]
    [(text, kwargs)] = m.replies
    assert text == "Try /jointest!"
    replied = [e for e in audit_events if e["kind"] == "ai_decision"]
    assert len(replied) == 1  # exactly one audit event for the decision
    assert replied[0]["decision"] == "replied"
    assert replied[0]["mode"] == "initiative"


def test_initiative_pass_leaves_cooldown_unmarked(audit_events):
    # Donor rule: cooldown is marked ON DELIVERY, not on attempt.
    cog, bot, ai = build(ai=FakeAI(AIResult(None, "pass")))
    ch = FakeChannel()
    run(cog, msg(KEYWORDED, channel=ch))
    run(cog, msg(KEYWORDED, channel=ch))
    assert len(ai.calls) == 2  # second attempt not blocked by cooldown
    assert all(e["decision"] == "skipped" for e in audit_events)


def test_cooldown_blocks_after_delivery(audit_events):
    cog, bot, ai = build()
    ch = FakeChannel()
    run(cog, msg(KEYWORDED, channel=ch))
    run(cog, msg(KEYWORDED, channel=ch))
    assert len(ai.calls) == 1
    assert audit_events[-1]["decision"] == "denied"
    assert audit_events[-1]["reason"] == "COOLDOWN_ACTIVE"


def test_hourly_cap_blocks(audit_events):
    cog, bot, ai = build()
    now = time.time()
    for _ in range(10):
        cog._initiative_times.append(now)
    run(cog, msg(KEYWORDED, channel=FakeChannel(id=7, name="general")))
    assert ai.calls == []
    assert audit_events[-1]["reason"] == "HOURLY_CAP"


# -- mention path -----------------------------------------------------------


def test_mention_replies_with_mentions_disarmed(audit_events):
    cog, bot, ai = build()
    m = msg(f"<@{bot.user.id}> how do I join?", mentions=(bot.user,))
    run(cog, m)
    assert [mode for _, mode in ai.calls] == ["mention"]
    [(text, kwargs)] = m.replies
    assert kwargs["mention_author"] is False
    am = kwargs["allowed_mentions"]
    assert isinstance(am, discord.AllowedMentions)
    # Invariant 8: AllowedMentions.none() - everything off.
    assert not am.everyone and not am.users and not am.roles and not am.replied_user


def test_mention_strip_handles_nickname_form():
    cog, bot, ai = build()
    m = msg(f"<@!{bot.user.id}> hello", mentions=(bot.user,))
    run(cog, m)
    payload, mode = ai.calls[0]
    assert "hello" in payload
    assert f"<@!{bot.user.id}>" not in payload


def test_bare_mention_is_skipped(audit_events):
    cog, bot, ai = build()
    m = msg(f"<@{bot.user.id}>", mentions=(bot.user,))
    run(cog, m)
    assert ai.calls == []
    assert m.replies == []
    assert audit_events[-1]["reason"] == "EMPTY_MESSAGE"


def test_everyone_ping_is_not_a_mention():
    # Invariant 11 / BUG-0019: @everyone must not read as addressing the bot.
    cog, bot, ai = build(make_cfg(initiative_channels=()))
    run(cog, msg("hey @everyone check the slingy build", mentions=()))
    assert ai.calls == []  # bystander path (and allow-list empty -> silent)


def test_mention_error_sends_apology_and_audits_degraded(audit_events):
    cog, bot, ai = build(ai=FakeAI(AIResult(None, "error")))
    m = msg(f"<@{bot.user.id}> help", mentions=(bot.user,))
    run(cog, m)
    [(text, kwargs)] = m.replies
    assert "tangled" in text  # the apology, not silence
    assert audit_events[-1]["decision"] == "degraded"


def test_initiative_error_stays_silent(audit_events):
    cog, bot, ai = build(ai=FakeAI(AIResult(None, "error")))
    m = msg(KEYWORDED)
    run(cog, m)
    assert m.replies == []
    assert audit_events[-1]["decision"] == "degraded"


# -- payload and memory -----------------------------------------------------


def test_payload_wraps_untrusted_and_names_channel():
    cog, bot, ai = build()
    run(cog, msg(KEYWORDED))
    payload, _ = ai.calls[0]
    assert "<<<UNTRUSTED_DATA__current_user_message__BEGIN>>>" in payload
    assert "#general" in payload


def test_transcript_context_rides_along_wrapped():
    cog, bot, ai = build()
    ch = FakeChannel()
    run(cog, msg(SMALLTALK, channel=ch))  # recorded, no AI call
    run(cog, msg(KEYWORDED, channel=ch))
    payload, _ = ai.calls[0]
    assert "<<<UNTRUSTED_DATA__recent_channel_turns__BEGIN>>>" in payload
    assert "walk" in payload


def test_memory_is_bounded_per_channel():
    cog, bot, ai = build(make_cfg(ai_memory_turns=5, initiative_channels=()))
    ch = FakeChannel()
    for i in range(9):
        run(cog, msg(f"message number {i}", channel=ch))
    assert len(cog._mem(ch.id)) == 5


def test_own_reply_recorded_as_assistant():
    cog, bot, ai = build()
    m = msg(KEYWORDED)
    run(cog, m)
    label, text = list(cog._mem(m.channel.id))[-1]
    assert label == "assistant"
    assert text == "Try /jointest!"
