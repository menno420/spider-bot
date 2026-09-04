"""Slingy Spider run evidence, validated and summarised — untrusted input.

The game keeps a local ledger of completed runs and Run History offers
**COPY JSON**. A tester saying *"the game feels impossible around 5 km"* can
paste that export, and this module turns it into numbers a developer can act
on. That is the whole point: it converts a subjective difficulty complaint into
evidence.

**The contract is `spider-swing`'s, not ours.** Established from that repo's
source at `fc64a3fb`, not from a description of it:

- the wrapper is exactly `{"format", "local_only", "transmission", "ledger"}`,
  with `format == "spider-swing-local-run-evidence@2"`
  (`game/domain/run_record_ledger.gd:7,160-166`);
- the ledger carries `schema_version`, `history_limit`, `records`,
  `feedback_responses` and four lifetime aggregates (`:169-187`);
- a record carries 43 keys (`game/domain/run_record.gd:238-285`);
- `PIXELS_PER_METRE = 10.0` (`game/domain/course_region_catalog.gd:9`), so a
  distance in pixels is metres x 10 — the one conversion that makes "5 km"
  mean the same thing here as in the game.
  distance in pixels is metres x 10 — the one conversion that makes "5 km"
  mean the same thing here as in the game.

`spider-swing` is canonical. This module **consumes**; it never becomes a
second definition of a run.

**Everything here treats the JSON as hostile**, because a paste in a public
Discord is exactly that. Two of the defences exist because Python's own
defaults are wrong for this job:

- `json.loads` accepts the non-standard tokens `NaN`, `Infinity` and
  `-Infinity` unless you pass `parse_constant`, and a NaN reaching an embed
  renders as `nan` while a NaN reaching a comparison is false against
  everything. Rejected outright.
- `json.loads` silently keeps the LAST of two identical keys, so
  `{"schema_version": 2, "schema_version": 99}` parses as 99 while a human
  reading the paste sees 2. Rejected outright via `object_pairs_hook`.

And two because the producer's own decode is more permissive than a consumer
should assume (measured in its source, not guessed): `course_seed` gets no
finiteness guard, every `StringName` field is stored as whatever string was
present with no catalog check, and `configuration_details` is an arbitrary
unbounded nested object. So nothing here trusts a field's type, range, or
membership — each is checked where it is read.

No execution, no dynamic import, no path is ever derived from this data.
Schema validation only.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any

from spiderbot import redact

log = logging.getLogger("spiderbot.evidence")

#: The only export format this consumer understands. A different one is an
#: honest refusal, never a best-effort parse: the estate's cross-repo feed rule
#: is that a consumer pins the version it was built against and fails honestly
#: on anything else (spider-swing `CONSTITUTION.md`).
SUPPORTED_FORMAT = "spider-swing-local-run-evidence@2"
SUPPORTED_LEDGER_SCHEMAS = frozenset({1, 2})

#: Discord's own attachment limit at boost tier 0 is 10 MB
#: (`discord/utils.py:120`). A run ledger is capped at 100 records by the
#: producer, which is tens of kilobytes; 512 KB is generous by an order of
#: magnitude and still small enough that parsing one cannot stall the gateway.
MAX_BYTES = 512 * 1024
#: The producer's own `HISTORY_LIMIT` (`run_record_ledger.gd`). A file claiming
#: more did not come from the game.
MAX_RECORDS = 100
#: What the producer writes on every row. A record missing any of these is not
#: an incomplete record to render with zeroes — it is not one of these records.
#: Deliberately the MEASUREMENTS and the classification, not every optional
#: field: an export from a slightly older build should still be readable, and
#: `unrecognised` already reports a value outside the catalogue.
REQUIRED_RECORD_FIELDS: tuple[str, ...] = (
    "difficulty_id",
    "terminal_outcome",
    "final_distance_pixels",
    "active_duration_seconds",
)

PIXELS_PER_METRE = 10.0

#: Finiteness is not enough. The course is 8 regions of 50,000 px
#: (`course_region_catalog.gd:9-21`), so 400,000 px is the whole world; 10x
#: that is generous for a future longer course and still small enough to
#: render. A value past a cap is not a run — it is a hand-edited file — so the
#: field is clamped AND the summary is marked implausible, because silently
#: showing the clamped number would present an edited file as a real one.
#: Measured need: 1e308 is finite, passes every NaN check, and renders as a
#: 309-digit number that breaks an embed. This is the check that catches it.
MAX_PIXELS = 4_000_000.0          # 400 km, ~10x the whole course
MAX_SECONDS = 24 * 60 * 60.0      # one day in a single run
MAX_SPEED_PPS = 100_000.0         # 10 km/s
MAX_COUNT = 1_000_000             # attachments, reels, flies


#: Closed catalogues, from spider-swing source. A value outside one is not
#: rejected — the producer itself stores raw strings — but it is reported as
#: `unrecognised` rather than shown as though the consumer understood it.
TERMINAL_OUTCOMES = frozenset({"death", "campaign_complete"})
DIFFICULTIES = frozenset({"relaxed", "standard", "harsh"})
CONFIGURATION_KINDS = frozenset(
    {"standard", "campaign", "course_lab", "debug_test", "region_practice", "trace_replay"}
)
INPUT_SOURCES = frozenset({"human", "trace_replay"})
REGIONS = (
    "bramble_canopy",
    "ancient_forest",
    "silk_hollow",
    "ruined_arboretum",
    "storm_ridge",
    "web_city",
    "ashen_hollow",
    "deep_mist",
)


class Rejected(Exception):
    """Internal control flow only. `parse` never lets one escape."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _no_constants(token: str) -> Any:
    raise Rejected("non_finite_number", token)


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise Rejected("duplicate_key", str(key)[:60])
        seen[key] = value
    return seen


@dataclass(frozen=True)
class RunSummary:
    """One run, as much of it as is safe and useful to show.

    Every string field has been through `redact.clean`; every number has been
    checked finite. Rendering helpers below are the only place a unit
    conversion happens, so "5 km" cannot mean two things.
    """

    record_id: str
    build_version: str
    android_version_code: int
    difficulty: str
    outcome: str
    death_cause: str
    final_region: str
    distance_pixels: float
    travelled_pixels: float
    active_seconds: float
    mean_speed_pps: float
    max_speed_pps: float
    above_reference_share: float
    web_attachments: int
    reel_activations: int
    burst_activations: int
    dive_activations: int
    flies: int
    configuration_kind: str
    input_source: str
    is_ordinary_human_run: bool
    unrecognised: tuple[str, ...] = ()
    #: Fields whose value was outside anything the game can produce and was
    #: clamped. A non-empty tuple means the file was edited, and every surface
    #: that shows this run says so rather than presenting it as a measurement.
    clamped: tuple[str, ...] = ()

    @property
    def implausible(self) -> bool:
        return bool(self.clamped)

    @property
    def distance_metres(self) -> float:
        return self.distance_pixels / PIXELS_PER_METRE

    @property
    def travelled_metres(self) -> float:
        return self.travelled_pixels / PIXELS_PER_METRE

    def headline(self, escape) -> str:
        """One line for a named destination.

        `escape` has no default ON PURPOSE. Every string in this object came
        from a file a member pasted, and the right neutralisation differs by
        destination — Discord needs markdown escaped, GitHub needs `@` and `#`
        broken. A default would let a caller leak member-controlled markdown
        into a public issue by forgetting a keyword, so forgetting is a
        TypeError instead. Pass `redact.for_discord` or `redact.for_github`.
        """
        return (
            f"{self.distance_metres:,.0f} m on {escape(self.difficulty)} "
            f"in {self.active_seconds:.0f}s — {escape(self.outcome)}"
            + (f" ({escape(self.death_cause)})" if self.death_cause else "")
        )

    def detail_lines(self, escape) -> list[str]:
        """Detail for a named destination. `escape` is required — see `headline`."""
        return [
            f"Build {escape(self.build_version)} "
            f"(version code {self.android_version_code})",
            f"Reached {self.distance_metres:,.0f} m in {escape(self.final_region)}"
            f", travelled {self.travelled_metres:,.0f} m",
            f"Mean speed {self.mean_speed_pps / PIXELS_PER_METRE:.1f} m/s, "
            f"peak {self.max_speed_pps / PIXELS_PER_METRE:.1f} m/s, "
            f"{self.above_reference_share * 100:.0f}% of the run above the reference pace",
            f"{self.web_attachments} attachments, {self.reel_activations} reels, "
            f"{self.burst_activations} bursts, {self.dive_activations} dives, "
            f"{self.flies} flies",
            f"Run kind: {escape(self.configuration_kind)} / {escape(self.input_source)}"
            + ("" if self.is_ordinary_human_run else "  (not an ordinary human run)"),
        ] + (
            [
                "**This file has been edited**: "
                + ", ".join(escape(c) for c in self.clamped)
                + " held values the game cannot produce. Treat the numbers as "
                "unreliable."
            ]
            if self.clamped
            else []
        ) + (
            [
                "Values this bot does not recognise: "
                + ", ".join(escape(u) for u in self.unrecognised)
                + " — shown as the file gave them."
            ]
            if self.unrecognised
            else []
        )


#: What the lifetime ledger can plausibly hold: every record at its own cap.
#: `MEASURED` 2026-09-04, the ledger aggregates had NO bounds at all while the
#: per-record fields were capped and banner-marked, so an edited export showing
#: 4.6 billion km travelled rendered as a plain fact under a summary whose
#: whole contract is that an implausible number is clamped AND marked.
MAX_LIFETIME_PIXELS = MAX_PIXELS * MAX_RECORDS
MAX_LIFETIME_SECONDS = MAX_SECONDS * MAX_RECORDS


@dataclass(frozen=True)
class Lifetime:
    completed_runs: int
    active_seconds: float
    travelled_pixels: float
    best_by_difficulty: dict[str, float]
    #: Same meaning as `RunSummary.clamped`: the names of the aggregates that
    #: held a value the game cannot produce.
    clamped: tuple[str, ...] = ()

    def lines(self, escape) -> list[str]:
        """`escape` is required, exactly as on `RunSummary` — the difficulty
        names in `best_by_difficulty` are keys from the pasted file."""
        out = [
            f"{self.completed_runs} recorded runs, "
            f"{self.active_seconds / 60:.0f} minutes of play, "
            f"{self.travelled_pixels / PIXELS_PER_METRE / 1000:.1f} km travelled"
        ]
        for mode in ("relaxed", "standard", "harsh"):
            best = self.best_by_difficulty.get(mode)
            if best is not None:
                out.append(f"Best on {escape(mode)}: {best / PIXELS_PER_METRE:,.0f} m")
        if self.clamped:
            out.append(
                "**This file has been edited**: "
                + ", ".join(escape(c) for c in self.clamped)
                + " held values the game cannot produce."
            )
        return out


@dataclass(frozen=True)
class Evidence:
    """A validated export. `ok` is the only thing a caller should branch on."""

    ok: bool
    reason: str = ""
    detail: str = ""
    latest: RunSummary | None = None
    lifetime: Lifetime | None = None
    record_count: int = 0
    skipped_records: int = 0
    feedback_answers: tuple[tuple[str, str], ...] = ()

    def summary_lines(self, escape) -> list[str]:
        """The whole thing for one destination. `escape` is required.

        Pass `redact.for_discord` for an embed, `redact.for_github` for an
        issue body. There is deliberately no default: see `RunSummary.headline`.
        """
        if not self.ok:
            return [f"Could not read that run evidence: {escape(self.reason)}"]
        lines: list[str] = []
        if self.latest is not None:
            lines.append(f"**Latest run** — {self.latest.headline(escape)}")
            lines += [f"· {line}" for line in self.latest.detail_lines(escape)]
        if self.lifetime is not None:
            lines.append("**Lifetime**")
            lines += [f"· {line}" for line in self.lifetime.lines(escape)]
        if self.skipped_records:
            lines.append(
                f"*{self.skipped_records} of {self.record_count + self.skipped_records} "
                "records were unreadable and were skipped.*"
            )
        return lines


def _to_finite(value: Any) -> float | None:
    """The value as a finite float, or None when it cannot be one.

    `None` and a legitimate `0.0` are different answers, which is the whole
    reason this exists beside `_finite`: a number the file supplied that this
    module cannot represent must be MARKED, not quietly replaced by a default
    that reads as a real measurement.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        # A JSON integer literal larger than ~1.8e308 is a finite number, so
        # `parse_constant` never sees it and `float()` raises OverflowError.
        # `MEASURED` 2026-09-04: one such literal anywhere in an export aborted
        # the WHOLE parse as "internal_error", where the module's design is
        # that a bad record is skipped and counted in `skipped_records`.
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _finite(value: Any, *, default: float = 0.0) -> float:
    number = _to_finite(value)
    return default if number is None else number


class _Bounds:
    """Collects whether anything had to be clamped while a record is read."""

    def __init__(self) -> None:
        self.clamped: list[str] = []

    def number(self, key: str, value: Any, *, cap: float, default: float = 0.0) -> float:
        number = _to_finite(value)
        if number is None:
            # Absent is not a defect; present-and-unrepresentable is. A value
            # of 1e400 replaced by 0.0 and NOT marked would show "0 m" as
            # though the game had measured it.
            if value is not None:
                self.clamped.append(key)
            return min(max(default, 0.0), cap)
        if number < 0.0 or number > cap:
            self.clamped.append(key)
            return min(max(number, 0.0), cap)
        return number

    def count(self, key: str, value: Any, *, cap: int = MAX_COUNT) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            if value is not None:
                self.clamped.append(key)
            return 0
        if value < 0 or value > cap:
            self.clamped.append(key)
            return max(0, min(value, cap))
        return value


def _nonneg_int(value: Any, *, default: int = 0, cap: int = 10**9) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(0, min(value, cap))


def _text(value: Any, *, limit: int = 80) -> str:
    return redact.one_line(value if isinstance(value, str) else "", limit=limit)


def _summarise_record(record: dict[str, Any]) -> RunSummary | None:
    if not isinstance(record, dict):
        return None
    record_id = _text(record.get("record_id"), limit=64)
    if not record_id:
        return None  # the producer rejects these too
    # `record.get(field) is None`, not `field not in record`: an export
    # carrying explicit nulls has every key present. Gemini (free-key review of
    # this fix, 2026-09-04) — and confirmed by running it: a fully-nulled
    # record passed the key check and rendered "0 m on unknown in 0s" as
    # though the game had measured it, which is the exact defect the check was
    # added to stop, arriving one JSON literal further along.
    missing = [field for field in REQUIRED_RECORD_FIELDS if record.get(field) is None]
    if missing:
        # Codex, spider-bot#3, 2026-09-04: a record was accepted on a non-empty
        # `record_id` alone, so `{"record_id": "x"}` inside a correctly wrapped
        # export produced `ok=True` and a summary reading "0 m on unknown in 0s
        # — unknown", published under a line saying it was validated against
        # the game's committed schema. Every absent measurement rendered as a
        # measured zero. A row that does not carry the producer's fields is not
        # a row this module can summarise; it is skipped and counted, which is
        # what `skipped_records` is for.
        log.debug("evidence: record %s is missing %s", record_id, ",".join(missing))
        return None

    unrecognised: list[str] = []
    bounds = _Bounds()

    def catalogued(key: str, allowed, *, limit: int = 40) -> str:
        raw = _text(record.get(key), limit=limit)
        if raw and raw not in allowed:
            unrecognised.append(f"{key}={raw}")
        return raw

    difficulty = catalogued("difficulty_id", DIFFICULTIES)
    outcome = catalogued("terminal_outcome", TERMINAL_OUTCOMES)
    kind = catalogued("configuration_kind", CONFIGURATION_KINDS)
    source = catalogued("input_source", INPUT_SOURCES)
    region = catalogued("final_region_id", set(REGIONS))

    return RunSummary(
        record_id=record_id,
        build_version=_text(record.get("build_version"), limit=48) or "unknown",
        android_version_code=_nonneg_int(record.get("android_version_code")),
        difficulty=difficulty or "unknown",
        outcome=outcome or "unknown",
        # No closed catalogue exists for death_cause — spider-swing's own doc
        # says it stores "the raw authoritative identifier" and does not group
        # or relabel it. So it is shown as free text, cleaned, never checked.
        death_cause=_text(record.get("death_cause"), limit=48),
        final_region=region or "unknown",
        distance_pixels=bounds.number(
            "final_distance_pixels", record.get("final_distance_pixels"), cap=MAX_PIXELS
        ),
        travelled_pixels=bounds.number(
            "travelled_distance_pixels",
            record.get("travelled_distance_pixels"),
            cap=MAX_PIXELS,
        ),
        active_seconds=bounds.number(
            "active_duration_seconds", record.get("active_duration_seconds"), cap=MAX_SECONDS
        ),
        mean_speed_pps=bounds.number(
            "mean_forward_speed_pixels_per_second",
            record.get("mean_forward_speed_pixels_per_second"),
            cap=MAX_SPEED_PPS,
        ),
        max_speed_pps=bounds.number(
            "maximum_forward_speed_pixels_per_second",
            record.get("maximum_forward_speed_pixels_per_second"),
            cap=MAX_SPEED_PPS,
        ),
        above_reference_share=bounds.number(
            "above_reference_speed_share",
            record.get("above_reference_speed_share"),
            cap=1.0,
        ),
        web_attachments=bounds.count(
            "successful_web_attachments", record.get("successful_web_attachments")
        ),
        reel_activations=bounds.count("reel_activations", record.get("reel_activations")),
        burst_activations=bounds.count("burst_activations", record.get("burst_activations")),
        dive_activations=bounds.count("dive_activations", record.get("dive_activations")),
        flies=bounds.count("flies_collected", record.get("flies_collected")),
        configuration_kind=kind or "unknown",
        input_source=source or "unknown",
        # The one classification that changes how a developer reads the numbers:
        # a replay or a debug run is not a person struggling at 5 km.
        is_ordinary_human_run=(kind == "standard" and source == "human"),
        unrecognised=tuple(unrecognised),
        clamped=tuple(bounds.clamped),
    )


def parse(raw: str | bytes, *, max_bytes: int = MAX_BYTES) -> Evidence:
    """Validate an export and reduce it to a summary. Never raises.

    The order matters: size before parse (so a 40 MB paste costs a length
    check, not a parse), parse before shape, shape before content.
    """
    try:
        if isinstance(raw, bytes):
            if len(raw) > max_bytes:
                return Evidence(False, "too_large", f"{len(raw)} bytes")
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                return Evidence(False, "not_utf8")
        if not isinstance(raw, str):
            return Evidence(False, "not_text")
        if len(raw.encode("utf-8", "ignore")) > max_bytes:
            return Evidence(False, "too_large", f"{len(raw)} characters")
        if not raw.strip():
            return Evidence(False, "empty")

        try:
            document = json.loads(
                raw, parse_constant=_no_constants, object_pairs_hook=_no_duplicate_keys
            )
        except Rejected:
            raise
        except (ValueError, RecursionError) as exc:
            return Evidence(False, "not_json", str(exc)[:120])

        if not isinstance(document, dict):
            return Evidence(False, "not_an_object")
        got_format = document.get("format")
        if got_format != SUPPORTED_FORMAT:
            # An honest refusal naming both sides, so a tester on a newer build
            # is told what happened rather than shown a silent failure.
            return Evidence(
                False,
                "unsupported_format",
                f"got {_text(got_format, limit=60) or type(got_format).__name__}, "
                f"this bot reads {SUPPORTED_FORMAT}",
            )
        ledger = document.get("ledger")
        if not isinstance(ledger, dict):
            return Evidence(False, "no_ledger")

        schema = ledger.get("schema_version")
        if isinstance(schema, bool) or not isinstance(schema, int):
            return Evidence(False, "bad_schema_version", repr(schema)[:40])
        if schema not in SUPPORTED_LEDGER_SCHEMAS:
            return Evidence(
                False,
                "unsupported_ledger_schema",
                f"got {schema}, this bot reads "
                f"{sorted(SUPPORTED_LEDGER_SCHEMAS)}",
            )

        records = ledger.get("records")
        if not isinstance(records, list):
            return Evidence(False, "no_records")
        if len(records) > MAX_RECORDS:
            return Evidence(
                False, "too_many_records", f"{len(records)} > {MAX_RECORDS}"
            )

        summaries = [_summarise_record(r) for r in records]
        good = [s for s in summaries if s is not None]
        skipped = len(summaries) - len(good)

        answers: list[tuple[str, str]] = []
        responses = ledger.get("feedback_responses")
        if isinstance(responses, list):
            for response in responses[:MAX_RECORDS]:
                if not isinstance(response, dict):
                    continue
                question = _text(response.get("question_id"), limit=48)
                answer = _text(response.get("answer_id"), limit=48)
                if question and answer:
                    answers.append((question, answer))

        # The ledger goes through the same `_Bounds` a record does, so an
        # implausible aggregate is clamped AND marked rather than printed.
        ledger_bounds = _Bounds()
        bests_raw = ledger.get("best_distance_pixels_by_difficulty")
        lifetime = Lifetime(
            completed_runs=ledger_bounds.count(
                "total_completed_recorded_runs",
                ledger.get("total_completed_recorded_runs"),
                cap=MAX_RECORDS,
            ),
            active_seconds=ledger_bounds.number(
                "total_active_duration_seconds",
                ledger.get("total_active_duration_seconds"),
                cap=MAX_LIFETIME_SECONDS,
            ),
            travelled_pixels=ledger_bounds.number(
                "total_distance_travelled_pixels",
                ledger.get("total_distance_travelled_pixels"),
                cap=MAX_LIFETIME_PIXELS,
            ),
            best_by_difficulty={
                mode: ledger_bounds.number(
                    f"best_distance_pixels[{mode}]", value, cap=MAX_PIXELS
                )
                for mode, value in (bests_raw or {}).items()
                if isinstance(mode, str) and mode in DIFFICULTIES
            }
            if isinstance(bests_raw, dict)
            else {},
            clamped=tuple(ledger_bounds.clamped),
        )

        return Evidence(
            ok=True,
            latest=good[-1] if good else None,
            lifetime=lifetime,
            record_count=len(good),
            skipped_records=skipped,
            feedback_answers=tuple(answers),
        )
    except Rejected as exc:
        return Evidence(False, exc.reason, exc.detail)
    except Exception:  # a paste must never take a Discord callback down
        log.exception("evidence: unexpected failure parsing an export")
        return Evidence(False, "internal_error")


def looks_like_evidence(text: str) -> bool:
    """Cheap pre-check before spending a parse on a pasted blob."""
    return SUPPORTED_FORMAT.split("@")[0] in (text or "")[:4000]
