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
from spiderbot.cogs.chat import ChatCog, scrub_mentions

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


def said(message):
    """What the bot actually put on screen, whichever shape it used.

    An AI answer ships as a purple embed (plan §5: the AI never speaks without
    the accent and the balloon); the bot's own operational lines stay plain
    text. Tests care about the words, not the wrapper.
    """
    out = []
    for text, kwargs in message.replies:
        if text:
            out.append(text)
        embed = kwargs.get("embed")
        if embed is not None:
            out.append(embed.description or "")
    return "\n".join(out)


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
    assert "Try /jointest!" in said(m)
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
    [(_text, kwargs)] = m.replies
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
    # The apology is the bot speaking, not the AI - plain text, no purple.
    assert "tangled" in said(m)
    assert m.replies[0][1].get("embed") is None
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


# -- not barging into someone else's conversation ---------------------------
# superbot's BUG-0019 #1, still open there and the single item blocking their
# AI feature from certification. Two doors: the live message, and the stored
# transcript that feeds every later reply.


def test_scrub_mentions_neutralises_users_roles_and_nicknames():
    assert scrub_mentions("<@1> and <@!2> and <@&3>") == "@someone and @someone and @a role"


def test_scrub_mentions_leaves_ordinary_text_alone():
    assert scrub_mentions("no mentions here, just <not> a token") == (
        "no mentions here, just <not> a token"
    )


def test_initiative_stays_out_when_someone_else_was_addressed(audit_events):
    cog, bot, ai = build()
    alice = FakeUser(42, name="Alice")
    run(cog, msg(f"<@{alice.id}> {KEYWORDED}", mentions=(alice,)))
    assert ai.calls == [], "the question was aimed at a human"
    denied = [e for e in audit_events if e.get("reason") == "ADDRESSED_TO_OTHERS"]
    assert len(denied) == 1
    assert denied[0]["decision"] == "denied"


def test_initiative_still_fires_on_an_unaddressed_question(audit_events):
    # The guard must not silence the case initiative exists for.
    cog, bot, ai = build()
    m = msg(KEYWORDED)
    run(cog, m)
    assert [mode for _, mode in ai.calls] == ["initiative"]
    assert m.replies


def test_no_raw_mention_token_reaches_the_model():
    cog, bot, ai = build()
    alice = FakeUser(42, name="Alice")
    m = msg(f"<@{bot.user.id}> is <@{alice.id}> right about the apk?",
            mentions=(bot.user, alice))
    run(cog, m)
    [(payload, _mode)] = ai.calls
    assert f"<@{alice.id}>" not in payload, "a raw ID lets the model narrate a ping"
    assert "@someone" in payload


def test_the_transcript_is_scrubbed_too():
    # A bystander line is recorded now and replayed into every later payload,
    # so scrubbing only the live message would leave the tokens flowing.
    cog, bot, ai = build()
    alice = FakeUser(42, name="Alice")
    ch = FakeChannel()
    run(cog, msg(f"<@{alice.id}> nice weather", channel=ch, mentions=(alice,)))
    [(_label, text)] = list(cog._mem(ch.id))
    assert f"<@{alice.id}>" not in text
    assert "@someone" in text


def test_a_scrubbed_transcript_reaches_the_model_clean():
    cog, bot, ai = build()
    alice = FakeUser(42, name="Alice")
    ch = FakeChannel()
    run(cog, msg(f"<@{alice.id}> nice weather", channel=ch, mentions=(alice,)))
    run(cog, msg(KEYWORDED, channel=ch))
    [(payload, _mode)] = ai.calls
    assert f"<@{alice.id}>" not in payload


# -- what an adversarial review executed against the committed code -----------


def test_a_channel_object_with_no_name_does_not_break_the_listener():
    """`CLAUDE.md` invariant 2 is "no listener may raise", and any member can
    archive a thread they created — after which discord.py hands the listener a
    `PartialMessageable`, which has no `.name`. `MEASURED` 2026-09-04: this
    listener raised `AttributeError` there, so the AI chat cog died in that
    channel before it could answer."""
    from types import SimpleNamespace

    cog, bot, ai = build()
    partial = SimpleNamespace(id=20, send=None)  # no .name, no .typing
    message = FakeMessage(
        f"<@{bot.user.id}> how do i install the build?",
        FakeUser(1, name="Menno"),
        partial,
        mentions=(bot.user,),
    )
    replies = []

    async def reply(*_a, **kw):
        replies.append(kw)

    message.reply = reply
    run(cog, message)  # must not raise
    assert len(ai.calls) == 1, "and it still answers"


def test_the_mention_path_is_rate_limited():
    """The path every member knows had no brake of any kind: one message, one
    Anthropic call, unbounded — while the initiative path beside it was
    carefully gated."""
    cog, bot, ai = build()
    author = FakeUser(7, name="flooder")
    for _ in range(50):
        run(cog, msg(f"<@{bot.user.id}> hello", mentions=(bot.user,), author=author))
    assert len(ai.calls) == 1

    # Positive control: a different member is not blocked by the first one's
    # cooldown — the brake is per-member, not a tap anyone can close.
    run(cog, msg(f"<@{bot.user.id}> hello", mentions=(bot.user,), author=FakeUser(8, "other")))
    assert len(ai.calls) == 2


def test_the_hourly_cap_counts_calls_not_deliveries(audit_events):
    """`MEASURED` 2026-09-04: the cap was armed in `_deliver`, after a
    successful reply — so a member who posted and immediately deleted their own
    message made every reply fail with "Unknown message" and the counter never
    moved: 500 Anthropic calls against a configured cap of 10."""
    cog, bot, ai = build(cfg=make_cfg(initiative_hourly_cap=3, initiative_cooldown_s=0))
    ch = FakeChannel()

    async def always_fails(*_a, **_kw):
        raise discord.HTTPException(_response(), "Unknown message")

    for _ in range(50):
        message = msg(KEYWORDED, channel=ch)
        message.reply = always_fails
        run(cog, message)
    assert len(ai.calls) == 3, "the cap bounds MODEL CALLS, not deliveries"

    # Positive control for the split: the COOLDOWN is still armed on delivery,
    # which is the donor's rule and the reason initiative mode is usable at all
    # — it answers PASS most of the time, and consuming the cooldown on every
    # decline would mean the bot almost never speaks.
    assert cog._last_initiative == {}


def _response():
    from types import SimpleNamespace

    return SimpleNamespace(status=400, reason="Bad Request")


def test_the_transcript_memory_is_bounded():
    """`_memory` is keyed by channel id and was never evicted, and any member
    with Create Public Threads mints new ids at will."""
    from spiderbot.cogs.chat import MAX_REMEMBERED_CHANNELS

    cog, bot, ai = build()
    for channel_id in range(MAX_REMEMBERED_CHANNELS + 50):
        run(cog, msg("just chatting", channel=FakeChannel(id=channel_id, name="general")))
    assert len(cog._memory) == MAX_REMEMBERED_CHANNELS
