"""The game-support feed: what Spider Bot knows about Slingy Spider, versioned.

`spiderbot/knowledge.py` is a hand-copied block of game facts, and the drift is
already measurable. `MEASURED` 2026-09-04 against spider-swing at `fc64a3fb`:
it says the game is *"currently in CLOSED ALPHA testing"* while that repo's own
runbook describes a closed track that has not started; its *"wait ~15 minutes"*
and *"wait an hour and retry"* figures appear nowhere in the runbook; its tester
retention rules have no textual match there at all; and it carries **no build
version whatsoever**, which is the fastest-changing fact in the whole system.

The fix is not to have the bot read a hundred markdown files at question time.
It is a small, explicit contract: `spider-swing` publishes exactly the facts
this bot needs, versioned, and this module consumes it.

**The contract is spider-swing's own**, from its `CONSTITUTION.md`:

> *"Cross-repo feeds carry a pinned contract. When this repo commits a generated
> artifact another repo consumes over a raw URL, the seam carries a committed,
> versioned shape contract: the producer stamps the version into the artifact
> and enforces fail-closed parity in CI; the consumer pins the version it built
> against and verifies at render time, surfacing drift as an honest banner —
> never faking data."*

So this consumer:

- **pins** `SUPPORTED_SCHEMA`, and refuses a version it does not know rather
  than reading a shape it is guessing at;
- **falls back to last-known-good**, then to the built-in block, and **says
  which one it is using** — an honest banner, never faked data;
- **never blocks** — a feed fetch failure degrades the answer's freshness, not
  the bot's availability.

Until the producer exists in `spider-swing`, the fetch 404s, the fallback is
the built-in block, and `staleness()` says so out loud. That is the honest
intermediate state rather than a half-built dependency.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from spiderbot import knowledge, redact

log = logging.getLogger("spiderbot.support")

#: The schema this consumer was built against. A feed stamped with anything
#: else is refused — the consumer's half of the pinned-feed contract.
SUPPORTED_SCHEMA = 1
FEED_FORMAT = "slingy-spider-support-feed"

MAX_BYTES = 256 * 1024
FETCH_TIMEOUT_S = 10.0


class Source:
    FEED = "feed"
    CACHED = "cached"
    BUILT_IN = "built_in"


@dataclass(frozen=True)
class SupportFacts:
    """What the bot may tell a tester, and where each fact came from."""

    source: str
    build_version: str = ""
    android_version_code: int = 0
    testing_state: str = ""
    join_steps: tuple[str, ...] = ()
    known_issues: tuple[str, ...] = ()
    troubleshooting: tuple[tuple[str, str], ...] = ()
    facts: tuple[tuple[str, str], ...] = ()
    feedback_wanted: tuple[str, ...] = ()
    retention_rules: tuple[str, ...] = ()
    links: tuple[tuple[str, str], ...] = ()
    generated_at: str = ""
    source_sha: str = ""
    schema_version: int = SUPPORTED_SCHEMA
    problem: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def live(self) -> bool:
        return self.source == Source.FEED

    def staleness(self) -> str:
        """One honest line about where these facts came from. Never omitted."""
        if self.source == Source.FEED:
            # Built from the parts that are actually present rather than by
            # string-patching a template: the first version closed a bracket it
            # had not always opened, and printed
            # "...from the support feed, spider-swing 0675ee01)".
            parts = []
            if self.generated_at:
                parts.append(f"generated {self.generated_at}")
            if self.source_sha:
                parts.append(f"spider-swing {self.source_sha[:8]}")
            detail = f" ({', '.join(parts)})" if parts else ""
            return f"Current game facts from the support feed{detail}"
        if self.source == Source.CACHED:
            return (
                "Using the last support feed I could read — the live one is "
                f"unavailable right now ({self.problem or 'fetch failed'})."
            )
        return (
            "Using the built-in game notes: no support feed is available"
            + (f" ({self.problem})" if self.problem else "")
            + ". Build-specific answers may be out of date."
        )

    def as_prompt_block(self) -> str:
        """The block handed to the model. Built-in text when there is no feed."""
        if self.source == Source.BUILT_IN:
            return knowledge.GAME_KNOWLEDGE
        parts = [
            "## The game: Slingy Spider — current facts",
            "",
            f"Current test build: {self.build_version or 'unknown'}"
            + (f" (Android version code {self.android_version_code})"
               if self.android_version_code else ""),
            f"Testing state: {self.testing_state or 'unknown'}",
        ]
        if self.join_steps:
            parts += ["", "How to join the test:"]
            parts += [f"{i + 1}. {s}" for i, s in enumerate(self.join_steps)]
        if self.known_issues:
            parts += ["", "Known issues in the current build (say so if asked):"]
            parts += [f"- {issue}" for issue in self.known_issues]
        if self.troubleshooting:
            parts += ["", "Troubleshooting:"]
            parts += [f"- {symptom}: {fix}" for symptom, fix in self.troubleshooting]
        if self.facts:
            parts += ["", "How the game works:"]
            parts += [f"- {name}: {value}" for name, value in self.facts]
        if self.feedback_wanted:
            parts += ["", "What feedback is most useful right now:"]
            parts += [f"- {want}" for want in self.feedback_wanted]
        if self.retention_rules:
            parts += ["", "What testers agreed to (Google counts CONTINUOUS opt-in):"]
            parts += [f"- {rule}" for rule in self.retention_rules]
        if self.links:
            parts += ["", "Official links (never invent others):"]
            parts += [f"- {label}: {url}" for label, url in self.links]
        parts += [
            "",
            "If a question is not answered above, say you are not sure and "
            "suggest asking Menno. Never invent a release date, a feature or a "
            "fix.",
        ]
        return "\n".join(parts)


BUILT_IN = SupportFacts(
    source=Source.BUILT_IN, problem="the producer feed has not been published yet"
)


def _pairs(
    raw: Any, *, names: tuple[str, str], limit: int = 20
) -> tuple[tuple[str, str], ...]:
    """Label/value pairs out of a feed list, read BY KEY NAME.

    `MEASURED` 2026-09-04: this used to do `keys = list(item)` and take the
    first two by insertion order. The current producer happens to emit `label`
    before `url`, so it rendered correctly — but the contract was silently
    "emit your keys in this order", which JSON does not promise and no test
    pinned. The natural shape `{"url": ..., "label": ...}` would have rendered
    `- https://…: Official page` into the model's prompt, under a heading that
    says these are the official links and never to invent others.

    A JSON array item `["label", "value"]` is still read positionally: there
    are no names to read there, and order IS the contract for an array.
    """
    if not isinstance(raw, list):
        return ()
    first, second = names
    out = []
    for item in raw[:limit]:
        if isinstance(item, dict):
            if first not in item or second not in item:
                continue  # not this shape; a guess here is what caused the bug
            pair = (item[first], item[second])
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            pair = (item[0], item[1])
        else:
            continue
        out.append(
            (
                redact.one_line(str(pair[0]), limit=120),
                redact.one_line(str(pair[1]), limit=400),
            )
        )
    return tuple(out)


#: The only hosts an "official link" may point at. The feed is fetched from a
#: repository the owner controls, so this is not defence against him — it is
#: defence against the block of text being written into the CHAT SYSTEM PROMPT
#: under the heading "Official links (never invent others)". Anything the model
#: is told is official should be checkable without trusting the transport, the
#: CDN, or a future producer edit. Everything else is dropped, not printed.
LINK_HOSTS: tuple[str, ...] = (
    "play.google.com",
    "groups.google.com",
    "github.com",
    "discord.gg",
    "discord.com",
    "youtube.com",
    "www.youtube.com",
)


def _safe_links(raw: Any, limit: int = 20) -> tuple[tuple[str, str], ...]:
    """Label/url pairs whose url is https and on `LINK_HOSTS`."""
    from urllib.parse import urlsplit

    out = []
    for label, url in _pairs(raw, names=("label", "url"), limit=limit):
        try:
            parts = urlsplit(url)
        except ValueError:
            continue
        if parts.scheme != "https" or parts.hostname not in LINK_HOSTS:
            log.warning("support feed: dropped a link to %r", url[:80])
            continue
        out.append((label, url))
    return tuple(out)


def _strings(raw: Any, limit: int = 20) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(redact.one_line(str(x), limit=300) for x in raw[:limit] if str(x).strip())


def parse(raw: str) -> SupportFacts:
    """A fetched feed into facts, or a built-in fallback naming the problem.

    Never raises. The feed is fetched over the network from a public URL, so it
    is treated with the same suspicion as any other external input.
    """
    if len(raw.encode("utf-8", "ignore")) > MAX_BYTES:
        return SupportFacts(source=Source.BUILT_IN, problem="feed is implausibly large")
    try:
        document = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        return SupportFacts(source=Source.BUILT_IN, problem=f"feed is not JSON: {exc}"[:120])
    if not isinstance(document, dict):
        return SupportFacts(source=Source.BUILT_IN, problem="feed is not an object")
    if document.get("format") != FEED_FORMAT:
        return SupportFacts(
            source=Source.BUILT_IN,
            problem=f"unexpected feed format {str(document.get('format'))[:40]!r}",
        )
    schema = document.get("schema_version")
    if isinstance(schema, bool) or schema != SUPPORTED_SCHEMA:
        # `True == 1` in Python, so a feed claiming `"schema_version": true`
        # would otherwise pin as version 1 and be read as if understood.
        # The honest banner the contract requires: a version we do not know is
        # refused, and the bot says so, rather than reading a guessed shape.
        return SupportFacts(
            source=Source.BUILT_IN,
            problem=(
                f"the feed is schema {schema} and this bot reads {SUPPORTED_SCHEMA} — "
                "it needs updating"
            ),
        )
    return SupportFacts(
        source=Source.FEED,
        build_version=redact.one_line(str(document.get("build_version", "")), limit=64),
        android_version_code=(
            int(document["android_version_code"])
            if isinstance(document.get("android_version_code"), int)
            and not isinstance(document.get("android_version_code"), bool)
            else 0
        ),
        testing_state=redact.one_line(str(document.get("testing_state", "")), limit=200),
        join_steps=_strings(document.get("join_steps")),
        known_issues=_strings(document.get("known_issues")),
        troubleshooting=_pairs(
            document.get("troubleshooting"), names=("symptom", "fix")
        ),
        facts=_pairs(document.get("facts"), names=("name", "value")),
        feedback_wanted=_strings(document.get("feedback_wanted")),
        retention_rules=_strings(document.get("retention_rules")),
        links=_safe_links(document.get("links")),
        generated_at=redact.one_line(str(document.get("generated_at", "")), limit=40),
        source_sha=redact.one_line(str(document.get("source_sha", "")), limit=64),
    )


class SupportFeed:
    """Fetches, caches, and always answers. Never blocks the bot.

    The cache is in memory and the refresh is lazy: a deploy costs one fetch on
    the first question, and the answer to every question in the next hour is
    served without a network call.
    """

    def __init__(self, cfg, *, session=None, now=time.time) -> None:
        self._cfg = cfg
        self._session = session
        self._now = now
        self._facts: SupportFacts = BUILT_IN
        self._fetched_at: float = 0.0

    @property
    def current(self) -> SupportFacts:
        """Whatever is cached. Always a usable answer, never None.

        Synchronous by contract — the AI path reads it while building a prompt
        — so it cannot fetch. `due` below is what makes the interval real.
        """
        return self._facts

    @property
    def due(self) -> bool:
        """True when `refresh()` would actually go to the network.

        `MEASURED` 2026-09-04: `refresh()` had a correct lazy-refresh guard and
        exactly one caller, `bot.on_ready`. Nothing called it again, so
        `SUPPORT_FEED_REFRESH_SECONDS` was dead configuration and a worker
        running for a week served the build version it booted with while the
        prompt block called them "current game facts". The caller now asks.
        """
        if not self._cfg.support_feed_url:
            return False
        return (
            not self._facts.live
            or self._now() - self._fetched_at >= self._cfg.support_feed_refresh_s
        )

    async def refresh(self, *, force: bool = False) -> SupportFacts:
        if not self._cfg.support_feed_url:
            self._facts = SupportFacts(
                source=Source.BUILT_IN, problem="no SUPPORT_FEED_URL configured"
            )
            return self._facts
        age = self._now() - self._fetched_at
        if not force and self._facts.live and age < self._cfg.support_feed_refresh_s:
            return self._facts

        raw, problem = await self._fetch()
        if raw is None:
            # Last-known-good for as long as the outage lasts. Codex,
            # spider-bot#3, 2026-09-04: this branched on `live`, and a CACHED
            # fact is not live — so the FIRST failure demoted the real feed to
            # cached and the SECOND replaced it with the empty built-in block.
            # A continuing outage kept useful facts for exactly one retry.
            # `build_version` is the tell: anything ever fetched has one.
            if self._facts.build_version:
                self._facts = SupportFacts(
                    **{**self._facts.__dict__, "source": Source.CACHED, "problem": problem}
                )
            else:
                self._facts = SupportFacts(source=Source.BUILT_IN, problem=problem)
            return self._facts

        parsed = parse(raw)
        self._facts = parsed
        if parsed.live:
            self._fetched_at = self._now()
        else:
            log.warning("support feed refused: %s", parsed.problem)
        return self._facts

    async def _fetch(self) -> tuple[str | None, str]:
        session = self._session
        owned = session is None
        if owned:
            session = aiohttp.ClientSession()
        try:
            async with session.get(
                self._cfg.support_feed_url,
                timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_S),
            ) as response:
                if response.status != 200:
                    return None, f"feed returned HTTP {response.status}"
                # Read at most MAX_BYTES + 1 rather than materialising the
                # whole body and checking the cap afterwards. Codex,
                # spider-bot#3, 2026-09-04: `response.text()` allocated
                # whatever the endpoint sent, so the advertised 256 KiB cap
                # protected the parser and not the worker's memory.
                body = await response.content.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    return None, "feed is implausibly large"
                try:
                    return body.decode("utf-8"), ""
                except UnicodeDecodeError:
                    return None, "feed is not valid UTF-8"
        except (aiohttp.ClientError, TimeoutError) as exc:
            return None, f"feed unreachable: {type(exc).__name__}"
        except Exception as exc:  # never take a question down with the feed
            log.exception("support feed fetch failed")
            return None, f"feed error: {type(exc).__name__}"
        finally:
            if owned:
                await session.close()
