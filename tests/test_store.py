"""spiderbot/store.py - the durable-storage seam.

Two halves. The wire format is pure, so it is tested pure: chunking,
reassembly, and every way a channel a human can type in could feed it
something that is not a record. The backends are then tested through the same
contract, so a future Postgres implementation has an executable definition of
what it must do.
"""

from __future__ import annotations

from asyncio import run

import discord
import pytest
from conftest import FakeChannel

from spiderbot import store


class BrokenChannel(FakeChannel):
    """A channel whose writes and reads fail the way Discord fails."""

    def __init__(self, *, fail_after: int | None = None, history_raises: bool = False) -> None:
        super().__init__(id=900, name="bot-state")
        self.fail_after = fail_after   # None = sends always succeed
        self.history_raises = history_raises

    async def send(self, *args, **kwargs):
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise discord.HTTPException(_response(), "nope")
        return await super().send(*args, **kwargs)

    def history(self, limit=100):
        if self.history_raises:
            raise discord.HTTPException(_response(), "nope")
        return super().history(limit=limit)


def _response():
    from types import SimpleNamespace

    return SimpleNamespace(status=503, reason="Service Unavailable")


# -- the wire format ---------------------------------------------------------


def test_a_small_record_is_one_message():
    chunks = store.encode_chunks("reports", "SB-R-1", {"a": 1})
    assert len(chunks) == 1


def test_a_record_larger_than_a_discord_message_spans_several():
    record = {"description": "x" * 4000}
    chunks = store.encode_chunks("reports", "SB-R-1", record)
    assert len(chunks) > 1
    assert all(len(c) < 2000 for c in chunks), "every chunk must fit in one message"
    envelopes = [store.decode_chunk(c) for c in chunks]
    assert store.assemble(envelopes)["SB-R-1"] == record


def test_an_incomplete_write_is_not_readable_as_a_whole_record():
    """A half-written report must read as absent, never as a truncated one."""
    chunks = store.encode_chunks("reports", "SB-R-1", {"description": "y" * 4000})
    envelopes = [store.decode_chunk(c) for c in chunks[:-1]]
    assert store.assemble(envelopes) == {}


def test_a_later_generation_replaces_an_earlier_one():
    first = store.encode_chunks("reports", "K", {"status": "stored"})
    second = store.encode_chunks("reports", "K", {"status": "published"})
    envelopes = [store.decode_chunk(c) for c in first + second]
    assert store.assemble(envelopes)["K"] == {"status": "published"}


def test_chunks_of_two_generations_never_interleave():
    """Without the generation token, chunk 0 of an update and chunk 1 of the
    write it replaces would splice into a record neither write ever made."""
    old = store.encode_chunks("reports", "K", {"body": "a" * 4000})
    new = store.encode_chunks("reports", "K", {"body": "b" * 4000})
    envelopes = [store.decode_chunk(c) for c in old + new]
    assembled = store.assemble(envelopes)["K"]
    assert assembled == {"body": "b" * 4000}


@pytest.mark.parametrize(
    "content",
    [
        "",
        "hello everyone",
        "```json\n{\"nope\": 1}\n```",          # right fence, wrong shape
        "```json\nnot json at all\n```",
        "```json\n[1, 2, 3]\n```",              # a list, not an envelope
        # right shape but a schema version this reader predates
        '```json\n{"v": 1, "c": "r", "k": "K", "g": "a",'
        ' "i": 0, "n": 1, "d": "{}"}\n```',
    ],
)
def test_anything_a_human_typed_in_the_channel_is_ignored(content):
    """The store channel is human-visible; someone will eventually type in it."""
    assert store.decode_chunk(content) is None


def test_a_record_too_large_to_store_is_refused_rather_than_truncated():
    huge = {"d": "y" * (store.CHUNK_CHARS * store.MAX_CHUNKS + 10)}
    assert store.encode_chunks("reports", "K", huge) is None


def test_a_value_python_cannot_serialise_is_stringified_rather_than_lost():
    """`default=str` means an unexpected type degrades to its text form. That
    is deliberate: a report must not be refused because one field held a
    datetime."""
    chunks = store.encode_chunks("reports", "K", {"when": {1, 2}})
    assert chunks is not None
    assembled = store.assemble([store.decode_chunk(c) for c in chunks])
    assert isinstance(assembled["K"]["when"], str)


def test_a_value_whose_own_str_raises_is_refused_rather_than_crashing_the_caller():
    class Hostile:
        def __str__(self):
            raise RuntimeError("no")
        __repr__ = __str__

    assert store.encode_chunks("reports", "K", {"x": Hostile()}) is None


# -- the backends, against the same contract ---------------------------------


@pytest.fixture(params=["memory", "discord"])
def backend(request):
    if request.param == "memory":
        return store.InMemoryStore()
    return store.DiscordChannelStore(FakeChannel(id=900, name="bot-state"))


def test_a_written_record_reads_back(backend):
    assert run(backend.append(store.REPORTS, "SB-R-1", {"title": "frozen"}))
    assert run(backend.get(store.REPORTS, "SB-R-1")) == {"title": "frozen"}


def test_collections_do_not_see_each_other(backend):
    run(backend.append(store.REPORTS, "K", {"kind": "report"}))
    run(backend.append(store.CASES, "K", {"kind": "case"}))
    assert run(backend.get(store.REPORTS, "K")) == {"kind": "report"}
    assert run(backend.get(store.CASES, "K")) == {"kind": "case"}
    assert list(run(backend.load(store.REPORTS))) == ["K"]


def test_load_returns_the_latest_of_every_key(backend):
    run(backend.append(store.REPORTS, "A", {"n": 1}))
    run(backend.append(store.REPORTS, "B", {"n": 2}))
    run(backend.append(store.REPORTS, "A", {"n": 3}))
    assert run(backend.load(store.REPORTS)) == {"A": {"n": 3}, "B": {"n": 2}}


def test_a_missing_key_is_none_not_an_error(backend):
    assert run(backend.get(store.REPORTS, "SB-R-nope")) is None


def test_a_large_record_survives_the_round_trip(backend):
    record = {"description": "x" * 3800, "id": "SB-R-1"}
    assert run(backend.append(store.REPORTS, "SB-R-1", record))
    assert run(backend.get(store.REPORTS, "SB-R-1")) == record


# -- failure, reported rather than swallowed ---------------------------------


def test_an_unconfigured_store_fails_the_write_instead_of_pretending():
    """`unconfigured = silent` means the feature does not announce itself. It
    never means telling someone their report was saved when it was not."""
    null = store.NullStore()
    assert null.available is False
    assert run(null.append(store.REPORTS, "K", {"a": 1})) is False
    assert run(null.load(store.REPORTS)) == {}


def test_a_store_with_no_channel_is_unavailable():
    assert store.DiscordChannelStore(None).available is False
    assert run(store.DiscordChannelStore(None).append(store.REPORTS, "K", {})) is False


def test_a_write_that_fails_part_way_reports_failure():
    channel = BrokenChannel(fail_after=1)
    backing = store.DiscordChannelStore(channel)
    assert run(backing.append(store.REPORTS, "K", {"d": "x" * 4000})) is False


def test_a_write_that_fails_part_way_leaves_nothing_readable():
    channel = BrokenChannel(fail_after=1)
    backing = store.DiscordChannelStore(channel)
    run(backing.append(store.REPORTS, "K", {"d": "x" * 4000}))
    fresh = store.DiscordChannelStore(channel)  # cold, reads the real history
    assert run(fresh.load(store.REPORTS)) == {}


def test_a_failed_history_read_is_not_cached_as_an_empty_store():
    """"No reports" and "could not read reports" must not look the same: one
    is a quiet panel, the other is a lost report."""
    channel = BrokenChannel(history_raises=True)
    backing = store.DiscordChannelStore(channel)
    assert run(backing.load(store.REPORTS)) == {}
    assert backing._index is None, "a failed read must leave the index cold"
    channel.history_raises = False
    run(channel.send(store.encode_chunks(store.REPORTS, "K", {"a": 1})[0]))
    assert run(backing.load(store.REPORTS)) == {"K": {"a": 1}}


def test_a_cold_store_folds_what_a_previous_deploy_wrote():
    channel = FakeChannel(id=900, name="bot-state")
    first = store.DiscordChannelStore(channel)
    run(first.append(store.REPORTS, "SB-R-1", {"status": "stored"}))
    run(first.append(store.REPORTS, "SB-R-1", {"status": "published"}))
    after_deploy = store.DiscordChannelStore(channel)
    assert run(after_deploy.get(store.REPORTS, "SB-R-1")) == {"status": "published"}


def test_writes_never_ping_anyone():
    channel = FakeChannel(id=900, name="bot-state")
    run(store.DiscordChannelStore(channel).append(store.REPORTS, "K", {"a": "@everyone"}))
    _args, kwargs = channel.sent[0]
    assert kwargs["allowed_mentions"].everyone is False


# -- the fence bug, found by the design pilot reading committed code ----------

TICKS = "`" * 3


def test_a_record_containing_a_code_fence_survives_the_round_trip():
    """MEASURED 2026-09-04, and it was silent data loss: the envelope rides in
    a ```json fence and `decode_chunk` split on the next fence, so a bug report
    quoting a crash log — exactly what a tester pastes — decoded to nothing."""
    record = {
        "id": "SB-R-1",
        "description": f"my log said:\n{TICKS}\nE/Godot: crash\n{TICKS}\nthen it froze",
    }
    chunks = store.encode_chunks("reports", "SB-R-1", record)
    assert store.decode_chunk(chunks[0]) is not None
    assert store.assemble([store.decode_chunk(c) for c in chunks])["SB-R-1"] == record


def test_the_wire_text_contains_no_backtick_at_all_inside_the_fence():
    """The property that makes the fix hold for any content, not just fences."""
    chunks = store.encode_chunks("reports", "K", {"d": f"{TICKS} ` `` {TICKS}"})
    for chunk in chunks:
        body = chunk.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
        assert "`" not in body


def test_a_backtick_heavy_record_still_round_trips_through_a_backend():
    channel = FakeChannel(id=900, name="bot-state")
    backing = store.DiscordChannelStore(channel)
    record = {"log": TICKS + "x" * 3000 + TICKS}
    assert run(backing.append(store.REPORTS, "K", record))
    assert run(store.DiscordChannelStore(channel).get(store.REPORTS, "K")) == record


# -- what an adversarial review executed against the committed code -----------


def test_two_concurrent_cold_reads_do_not_lose_a_record():
    """`MEASURED` 2026-09-04, the CRITICAL of that review.

    Two members filed at the same moment. Both writes started a cold scan; the
    first scan finished after the second write had landed and REPLACED the
    index with a snapshot taken before it. The record was durably in the
    channel and absent from every read path for the life of the process — "My
    reports" showed nothing, `/publish <id>` said no such report — while its
    reporter had been told "Saved. Your reference is …".
    """
    import asyncio as _asyncio

    channel = SlowHistoryChannel()
    backing = store.DiscordChannelStore(channel)

    async def scenario():
        # Two writers racing, exactly as two members pressing at once.
        await _asyncio.gather(
            backing.append(store.REPORTS, "SB-R-ALICE", {"id": "SB-R-ALICE"}),
            backing.append(store.REPORTS, "SB-R-BOB", {"id": "SB-R-BOB"}),
        )
        return (
            await backing.get(store.REPORTS, "SB-R-ALICE"),
            await backing.get(store.REPORTS, "SB-R-BOB"),
        )

    alice, bob = run(scenario())
    assert alice is not None, "alice was told 'Saved' and must be readable"
    assert bob is not None
    assert channel.scans == 1, "one cold scan, reused by everyone who queued"


class SlowHistoryChannel(FakeChannel):
    """A channel whose history scan yields control, so a write can land inside
    it — which is the whole race. A scan that never awaits cannot reproduce it."""

    def __init__(self) -> None:
        super().__init__(id=900, name="intake-state")
        self.scans = 0
        self.guild = None

    def history(self, *, limit: int = 100):
        import asyncio as _asyncio

        self.scans += 1
        contents = [a[0] for a, _kw in self.sent if a]
        snapshot = list(reversed(contents))[:limit]

        class _Slow:
            def __aiter__(inner):
                inner._i = 0
                return inner

            async def __anext__(inner):
                await _asyncio.sleep(0)  # the yield point the race needs
                if inner._i >= len(snapshot):
                    raise StopAsyncIteration
                item = snapshot[inner._i]
                inner._i += 1
                from types import SimpleNamespace

                return SimpleNamespace(content=item, author=SimpleNamespace(id=1))

        return _Slow()


def test_only_the_bots_own_messages_are_read_as_records():
    """`decode_chunk` used to accept ANY message in the channel, so anyone who
    could post there could mint a record — including one marked
    `sensitivity: public_safe` that the next retry would publish. The channel is
    staff-private by design, so this is depth, not a member-reachable hole."""
    from types import SimpleNamespace

    class GuildedChannel(FakeChannel):
        def __init__(self) -> None:
            super().__init__(id=901, name="intake-state")
            self.guild = SimpleNamespace(me=SimpleNamespace(id=999))
            self.rows: list = []

        async def send(self, *args, **kwargs):
            self.rows.append((args[0], 999))

        def history(self, *, limit: int = 100):
            rows = list(reversed(self.rows))[:limit]

            class _It:
                def __aiter__(inner):
                    inner._i = 0
                    return inner

                async def __anext__(inner):
                    if inner._i >= len(rows):
                        raise StopAsyncIteration
                    content, author_id = rows[inner._i]
                    inner._i += 1
                    return SimpleNamespace(
                        content=content, author=SimpleNamespace(id=author_id)
                    )

            return _It()

    channel = GuildedChannel()
    backing = store.DiscordChannelStore(channel)
    run(backing.append(store.REPORTS, "SB-R-REAL", {"id": "SB-R-REAL"}))

    # An impostor row written by somebody else, in the same wire format.
    forged = store.encode_chunks(store.REPORTS, "SB-R-FORGED", {"id": "SB-R-FORGED"})
    channel.rows.append((forged[0], 12345))

    fresh = store.DiscordChannelStore(channel)
    assert run(fresh.get(store.REPORTS, "SB-R-FORGED")) is None
    # Positive control: the bot's own row is still read, so the filter is about
    # authorship and not about the decoder having stopped working.
    assert run(fresh.get(store.REPORTS, "SB-R-REAL")) is not None
