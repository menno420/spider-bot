"""The durable-storage seam — one interface, three implementations, no database.

`spiderbot/memory.py` already proved the pattern: Discord itself is the store,
each record a JSON message in a private staff channel, readable by a human
without any tooling. That module is deliberately narrow — one kind of record,
newest-wins, `write` and `read_latest`. This module is the general form the new
workload needs, and it is a *seam* before it is an implementation: everything
above it depends on `Store`, so the answer to "is Discord still enough?" is one
file rather than a rewrite.

**Why Discord is still the answer, from the actual numbers rather than taste.**
The workload is reports and moderation cases for one server whose target
population is 12 to 16 testers. A report is a few hundred bytes; the whole corpus
after a year of the closed test is plausibly low hundreds of records. Every
query this system performs is either "one record by its id" or "all records in
one collection", both of which a bounded channel scan answers, and both of
which are then served from an in-memory index. Nothing needs a join, a range
query, an index or a transaction. Postgres would add a Railway addon, a
migration story, a backup story and a second failure mode to a bot whose whole
operational virtue is that it has one. It is the right answer when a query
appears that a scan cannot serve — not before, and the point of this file is
that when that day comes, only this file changes.

**What is different from `memory.py`, and why each difference exists:**

- **Collections.** Reports and moderation cases must not share a namespace with
  membership snapshots, and a reader must be able to fold one collection
  without paying for the others.
- **Keyed, not scanned.** `memory.read_latest` walks history per lookup. Here
  the channel is read once and folded into an index that writes keep current,
  so a Home panel listing open reports is not 500 API reads.
- **Chunking.** A Discord message caps at 2000 characters and `memory.py`
  clamps a record to 1900, which silently truncates. A bug report is up to
  1,200 characters of description plus 800 of repro steps plus a run-evidence
  summary — comfortably over the cap. Truncating a report is losing part of it,
  so records here span as many messages as they need. A write that fails
  half-way leaves an INCOMPLETE generation, and an incomplete generation is
  ignored on read: a partial record must never be readable as a whole one.
- **Generations.** Each write stamps a token, so chunks of an update can never
  interleave with chunks of the write it replaces.

Everything is best-effort in the sense that it never raises into a Discord
callback — but unlike `memory.py`, **failure is reported rather than swallowed**:
`append` returns False and the caller decides. For an intake report that
distinction is the whole feature. A caller that ignores the return value has a
bug, and the intake service is written so it cannot.
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any, Protocol

import discord

log = logging.getLogger("spiderbot.store")

# Bumped only when the envelope shape below changes. A reader rejects a
# generation it predates rather than mis-parsing it (memory.py's lesson,
# generalised).
SCHEMA_VERSION = 2

_FENCE = "```json"
# Discord caps a message at 2000 characters. The envelope around a chunk costs
# ~120 for the fence, the keys and JSON escaping of the payload slice; 1500
# leaves room for a pathological slice where every character escapes to six.
CHUNK_CHARS = 1500
# A single record may not exceed this many chunks. 40 x 1500 is 60 KB of one
# record, far past anything the intake service will accept, and the cap exists
# so a bug upstream cannot post a hundred messages into a staff channel.
MAX_CHUNKS = 40
# How far back a cold load reads. One record is 1-2 messages in practice, so
# this is thousands of records; a channel that outgrows it needs the database
# this seam exists to make cheap.
HISTORY_LIMIT = 2000


class StoreUnavailable(Exception):
    """Raised only by `Store.require()`, never by a read or a write."""


class Store(Protocol):
    """Append-only, keyed, per-collection storage. Latest write per key wins."""

    @property
    def available(self) -> bool:
        """False when nothing is configured. Callers must degrade, not crash."""

    async def append(self, collection: str, key: str, data: dict[str, Any]) -> bool:
        """Write `data` under `key`. Returns False if it did not durably land."""

    async def get(self, collection: str, key: str) -> dict[str, Any] | None:
        """The latest record for `key`, or None."""

    async def load(self, collection: str) -> dict[str, dict[str, Any]]:
        """Every key in `collection` mapped to its latest record."""


def _generation() -> str:
    return secrets.token_hex(4)


def encode_chunks(collection: str, key: str, data: dict[str, Any]) -> list[str] | None:
    """A record as the messages that carry it, or None if it is too large.

    Pure, so the wire format is testable without a Discord channel.
    """
    try:
        payload = json.dumps(data, ensure_ascii=False, default=str, sort_keys=True)
    except Exception:
        # Deliberately broad. `default=str` calls str() on any unexpected type,
        # and a type whose own __str__ raises would otherwise propagate out of
        # a store write into a Discord callback. Nothing below this line may
        # take the gateway down; a record that cannot be encoded is a refused
        # write, which the caller already knows how to report honestly.
        log.exception("store: record for %s/%s could not be encoded", collection, key)
        return None
    slices = [payload[i : i + CHUNK_CHARS] for i in range(0, len(payload), CHUNK_CHARS)] or [""]
    if len(slices) > MAX_CHUNKS:
        log.error(
            "store: record %s/%s needs %d chunks, cap is %d",
            collection, key, len(slices), MAX_CHUNKS,
        )
        return None
    gen = _generation()
    out = []
    for index, part in enumerate(slices):
        envelope = {
            "v": SCHEMA_VERSION,
            "c": collection,
            "k": key,
            "g": gen,
            "i": index,
            "n": len(slices),
            "d": part,
        }
        out.append(f"{_FENCE}\n{_escape_backticks(envelope)}\n```")
    return out


def _escape_backticks(envelope: dict[str, Any]) -> str:
    """Serialise an envelope so the emitted text contains NO literal backtick.

    The envelope rides inside a ```json fence and `decode_chunk` finds its end
    by splitting on the next fence. A record whose own content contains a
    backtick run therefore truncated the envelope and the record became
    unreadable — silently, which is the worst kind. `MEASURED` 2026-09-04: a
    bug report quoting a crash log inside a fenced block (exactly what a tester
    pastes) round-tripped to `{}`.

    The fix is not to escape the fence but to emit no backticks at all: JSON's
    own `\u0060` escape means the same character to every parser and is not a
    backtick in the text. So the split cannot be confused by content, whatever
    the content is. Old records are unaffected — a reader of them is unchanged,
    and any that contained a backtick was already unreadable.
    """
    return json.dumps(envelope, ensure_ascii=False).replace("`", "\\u0060")


def decode_chunk(content: str) -> dict[str, Any] | None:
    """One message back into an envelope, or None if it is not one of ours.

    The channel is human-visible, so someone will eventually type in it, and a
    stray message must never break a read.
    """
    if not content or _FENCE not in content:
        return None
    try:
        body = content.split(_FENCE, 1)[1].split("```", 1)[0].strip()
        envelope = json.loads(body)
    except (ValueError, IndexError):
        return None
    if not isinstance(envelope, dict) or envelope.get("v") != SCHEMA_VERSION:
        return None
    if not all(k in envelope for k in ("c", "k", "g", "i", "n", "d")):
        return None
    if not isinstance(envelope["i"], int) or not isinstance(envelope["n"], int):
        return None
    return envelope


def assemble(envelopes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Fold envelopes (any order) into {key: latest complete record}.

    Two rules do the work. An incomplete generation is dropped, because a
    half-written record must not read as a whole one. Where a key has several
    complete generations, the one whose chunks appear LATEST in the channel
    wins — writes append, so later is newer.
    """
    groups: dict[tuple[str, str], dict[int, str]] = {}
    expected: dict[tuple[str, str], int] = {}
    order: dict[tuple[str, str], int] = {}
    for position, env in enumerate(envelopes):
        ident = (env["k"], env["g"])
        groups.setdefault(ident, {})[env["i"]] = env["d"]
        expected.setdefault(ident, env["n"])
        order[ident] = max(order.get(ident, -1), position)

    best: dict[str, tuple[int, dict[str, Any]]] = {}
    for ident, parts in groups.items():
        key, _gen = ident
        count = expected[ident]
        if count <= 0 or len(parts) != count or set(parts) != set(range(count)):
            continue  # incomplete or malformed write: ignore it entirely
        try:
            record = json.loads("".join(parts[i] for i in range(count)))
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        position = order[ident]
        if key not in best or position > best[key][0]:
            best[key] = (position, record)
    return {key: record for key, (_, record) in best.items()}


class NullStore:
    """No storage configured. Every write fails honestly; every read is empty.

    This is what `unconfigured = silent` (invariant 4) looks like at this
    layer — with one deliberate difference. Silent means the *feature* does not
    announce itself; it does not mean a caller is told a report was saved when
    it was not. `append` returning False is what the intake service turns into
    an honest message to the reporter.
    """

    @property
    def available(self) -> bool:
        return False

    async def append(self, collection: str, key: str, data: dict[str, Any]) -> bool:
        return False

    async def get(self, collection: str, key: str) -> dict[str, Any] | None:
        return None

    async def load(self, collection: str) -> dict[str, dict[str, Any]]:
        return {}


class InMemoryStore:
    """A real store that forgets on restart. Tests use it; production must not.

    It exists so the layers above can be exercised without a Discord channel,
    and so a future backend has a reference implementation of the contract.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict[str, Any]]] = {}

    @property
    def available(self) -> bool:
        return True

    async def append(self, collection: str, key: str, data: dict[str, Any]) -> bool:
        if encode_chunks(collection, key, data) is None:
            return False  # same size and encodability contract as the real one
        self._data.setdefault(collection, {})[key] = json.loads(
            json.dumps(data, default=str)
        )
        return True

    async def get(self, collection: str, key: str) -> dict[str, Any] | None:
        return self._data.get(collection, {}).get(key)

    async def load(self, collection: str) -> dict[str, dict[str, Any]]:
        return dict(self._data.get(collection, {}))


class DiscordChannelStore:
    """Discord is the database: one private channel, one message per chunk.

    The channel is read once, lazily, and folded into an index that writes keep
    current. A deploy costs one history read; everything after it is memory.
    If that read fails the index is NOT cached, so the next call retries rather
    than serving an empty store as if it were an empty channel — the difference
    between "no reports" and "could not read reports" is the difference between
    a quiet Home panel and a lost report.
    """

    def __init__(self, channel) -> None:
        self._channel = channel
        self._index: dict[str, dict[str, dict[str, Any]]] | None = None

    @property
    def available(self) -> bool:
        return self._channel is not None

    async def _ensure_index(self) -> bool:
        if self._index is not None:
            return True
        if self._channel is None:
            return False
        envelopes: list[dict[str, Any]] = []
        try:
            async for message in self._channel.history(limit=HISTORY_LIMIT):
                env = decode_chunk(getattr(message, "content", ""))
                if env is not None:
                    envelopes.append(env)
        except discord.HTTPException:
            log.warning("store: could not read history; index stays cold")
            return False
        envelopes.reverse()  # history is newest-first; assemble() wants append order
        index: dict[str, dict[str, dict[str, Any]]] = {}
        by_collection: dict[str, list[dict[str, Any]]] = {}
        for env in envelopes:
            by_collection.setdefault(env["c"], []).append(env)
        for collection, group in by_collection.items():
            index[collection] = assemble(group)
        self._index = index
        log.info(
            "store: index built — %s",
            ", ".join(f"{c}={len(r)}" for c, r in index.items()) or "empty",
        )
        return True

    async def append(self, collection: str, key: str, data: dict[str, Any]) -> bool:
        if self._channel is None:
            return False
        chunks = encode_chunks(collection, key, data)
        if chunks is None:
            return False
        # Build the index BEFORE the first write of the session, so a later
        # read cannot fold this write in twice (once from the cache, once from
        # history) or miss what was there before it.
        await self._ensure_index()
        for chunk in chunks:
            try:
                await self._channel.send(
                    chunk, allowed_mentions=discord.AllowedMentions.none()
                )
            except discord.HTTPException:
                log.warning(
                    "store: write failed part-way for %s/%s — record not stored",
                    collection, key,
                )
                return False
        if self._index is not None:
            self._index.setdefault(collection, {})[key] = json.loads(
                json.dumps(data, default=str)
            )
        return True

    async def get(self, collection: str, key: str) -> dict[str, Any] | None:
        if not await self._ensure_index():
            return None
        return (self._index or {}).get(collection, {}).get(key)

    async def load(self, collection: str) -> dict[str, dict[str, Any]]:
        if not await self._ensure_index():
            return {}
        return dict((self._index or {}).get(collection, {}))


# Collection names. Constants rather than literals so a typo is an import
# error instead of a silently empty listing.
REPORTS = "reports"
CASES = "cases"
REVIEWS = "case_reviews"
PUBLICATIONS = "publications"
