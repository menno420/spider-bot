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
        "```json\n{\"v\": 1, \"c\": \"r\", \"k\": \"K\", \"g\": \"a\", \"i\": 0, \"n\": 1, \"d\": \"{}\"}\n```",
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
