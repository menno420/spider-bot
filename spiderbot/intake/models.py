"""The canonical report — one model, every entry point, every category.

Pure data. Nothing here touches Discord, GitHub or a model client, so the
privacy rules and the publication rules can be tested exhaustively without any
of them.

**The split that matters is public versus private FIELDS, not public versus
private reports.** A bug report is publishable and its reporter is not: the
Discord user id, the display name and the source channel link are the private
return path, kept in the bot's own store so a fix can be announced back to the
person who reported it, and never written into a public issue. `public_body()`
is built from an allow-list of fields rather than by removing the private ones,
because a field added later defaults to absent rather than to leaked.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from spiderbot import redact

SCHEMA_VERSION = 1

#: Every member-controlled field that reaches public output. ONE list, used by
#: `Report.published_text()` (what the classifier reads) and asserted against
#: `public_body()`'s source by `tests/test_intake.py`, so "published" and
#: "scanned" cannot drift apart again. They already had: `evidence_format` was
#: printed into the issue body and never classified.
PUBLISHED_FIELDS: tuple[str, ...] = (
    "title",
    "description",
    "repro_steps",
    "device",
    "build_version",
    "ai_summary",
    "evidence_format",
)

MAX_TITLE = 120
MAX_DESCRIPTION = 4000
MAX_STEPS = 2000
MAX_DEVICE = 120
MAX_TAGS = 8


class Category(StrEnum):
    """What kind of report. Covers the five things the owner named plus one.

    `COMPLAINT` is deliberately its own category rather than folded into
    feedback: it is the ambiguous one, and naming it is what lets
    `privacy.py` treat it carefully instead of guessing per report.
    """

    BUG = "bug"
    IDEA = "idea"
    GAMEPLAY_FEEDBACK = "gameplay_feedback"
    COMPLAINT = "complaint"
    TESTING_PROBLEM = "testing_problem"
    GENERAL = "general"


#: What each category is called when a human reads it.
CATEGORY_LABELS: dict[Category, str] = {
    Category.BUG: "Bug",
    Category.IDEA: "Idea",
    Category.GAMEPLAY_FEEDBACK: "Gameplay feedback",
    Category.COMPLAINT: "Complaint",
    Category.TESTING_PROBLEM: "Testing problem",
    Category.GENERAL: "General feedback",
}


class Sensitivity(StrEnum):
    """Whether a report may leave the server.

    `UNCLASSIFIED` is not a third state to handle everywhere — it is the
    starting value, and it is treated exactly as `PRIVATE` by every consumer.
    That is the "if sensitivity is unclear, default private" rule expressed as
    a type rather than as a convention someone has to remember.
    """

    PUBLIC_SAFE = "public_safe"
    PRIVATE = "private"
    UNCLASSIFIED = "unclassified"


class Status(StrEnum):
    DRAFT = "draft"
    STORED = "stored"
    PUBLISH_PENDING = "publish_pending"
    PUBLISHED = "published"
    PUBLISH_FAILED = "publish_failed"
    RESOLVED = "resolved"
    DUPLICATE = "duplicate"


#: Statuses from which publication may still be attempted. A published report
#: is never re-published; that is half of idempotency, enforced by type rather
#: than by remembering to check.
#:
#: `RESOLVED` and `DUPLICATE` are HERE deliberately, and the reason is a real
#: sequence: a moderator marking a report resolved before the retry queue has
#: drained would otherwise make it permanently unpublishable, so a GitHub
#: outage plus a tidy moderator equals a silently dropped report. Lifecycle and
#: developer judgement are different axes; `Status` is the lifecycle one.
PUBLISHABLE_STATUSES: frozenset[Status] = frozenset(
    {
        Status.STORED,
        Status.PUBLISH_PENDING,
        Status.PUBLISH_FAILED,
        Status.RESOLVED,
        Status.DUPLICATE,
    }
)


@dataclass(frozen=True)
class Reporter:
    """The private return path. Never published, by construction.

    Held so the bot can answer *"where did my report go?"* and *"has it been
    fixed?"* in the place the person actually reported from. A public GitHub
    issue does not need any of it — see `Report.public_body`.
    """

    user_id: int
    display_name: str = ""
    channel_id: int | None = None
    message_id: int | None = None
    thread_id: int | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "display_name": redact.one_line(self.display_name, limit=64),
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "thread_id": self.thread_id,
        }

    @classmethod
    def from_record(cls, data: dict[str, Any] | None) -> Reporter | None:
        if not isinstance(data, dict) or "user_id" not in data:
            return None
        try:
            user_id = int(data["user_id"])
        except (TypeError, ValueError):
            return None
        return cls(
            user_id=user_id,
            display_name=str(data.get("display_name") or ""),
            channel_id=data.get("channel_id"),
            message_id=data.get("message_id"),
            thread_id=data.get("thread_id"),
        )


@dataclass(frozen=True)
class Report:
    """One report, whatever entry point produced it."""

    id: str
    category: Category
    title: str
    description: str
    submitted_at: float
    correlation_id: str = ""

    reporter: Reporter | None = None

    build_version: str = ""
    device: str = ""
    repro_steps: str = ""

    evidence_summary: tuple[str, ...] = ()
    evidence_format: str = ""

    ai_summary: str = ""
    ai_tags: tuple[str, ...] = ()

    sensitivity: Sensitivity = Sensitivity.UNCLASSIFIED
    sensitivity_reason: str = ""

    github_issue_number: int | None = None
    github_issue_url: str = ""
    duplicate_of: str = ""

    #: Whether the reporter agreed to this leaving the server. An explicit form
    #: submission IS that agreement — they typed it into a form whose button
    #: said what it does — so entry points set it True. The conversational path
    #: sets it only when the person presses confirm on the summary the bot
    #: showed them, which is the brief's own sequence.
    reporter_cleared: bool = True
    #: Who cleared this for a PUBLIC issue. Empty means nobody, and nobody
    #: means it stays private — see `may_publish`.
    approved_by: str = ""

    #: Why the last publish attempt failed, and whether trying again could
    #: ever help. `github_sink` classifies 404 (wrong repo or no access), 410
    #: (issues disabled) and 422 (rejected) as permanent, and its docstring
    #: says that distinction "is what stops a retry loop hammering a 404
    #: forever" — but nothing read `.retryable` until 2026-09-04, so a
    #: permanently-failed report was re-POSTed on every pass, for ever.
    publish_failure: str = ""
    publish_failure_retryable: bool = True

    status: Status = Status.DRAFT
    resolution: str = ""
    schema_version: int = SCHEMA_VERSION
    notes: tuple[str, ...] = field(default_factory=tuple)

    # -- the publication predicate ------------------------------------------

    @property
    def is_public_safe(self) -> bool:
        """The single predicate the GitHub sink checks. Nothing else may.

        Four conditions, and every one of them is a real failure mode: an
        explicit `PUBLIC_SAFE` (so `UNCLASSIFIED` never leaks), a category that
        can be public at all, a classification reason on the record (so an
        unclassified report cannot be waved through by setting one field), and
        the reporter's own agreement.
        """
        return (
            self.sensitivity is Sensitivity.PUBLIC_SAFE
            and self.category is not Category.COMPLAINT
            and bool(self.sensitivity_reason)
            and self.reporter_cleared
        )

    @property
    def may_publish(self) -> bool:
        """Whether this may become a PUBLIC GitHub issue.

        **`approved_by` is the load-bearing condition, and it is here because
        the first design was wrong in a way no amount of tuning fixes.** That
        design published anything a keyword list did not object to — so "no
        signal found" meant "safe", and:

        - a plain, unobfuscated complaint about a named member, filed as a bug,
          published verbatim;
        - **every non-English report was unprotected** — the vocabulary is
          English and this server's own language is Dutch;
        - leetspeak, spaced-out words and contact details written as words all
          published;
        - a zero-width space inside each trigger word blinded the classifier
          while the published text carried the words intact.

        All four reproduced. A regex miss must not mean publish, so publication
        now needs a person: the classifier PRE-SORTS the queue, which is what
        it is genuinely good at, and its failure mode becomes "a moderator sees
        it in the wrong bucket" rather than "it is on the internet".

        At this server's volume — a handful of reports a week — one press is a
        trivial cost for removing the entire class.
        """
        return (
            self.is_public_safe
            and bool(self.approved_by)
            and self.status in PUBLISHABLE_STATUSES
            and self.github_issue_number is None
            and self.publish_failure_retryable
        )

    def published_text(self) -> str:
        """Everything member-controlled that would reach a public issue.

        **Cleaned the way it will be published.** That is the whole point and
        it was the hole: `redact.clean` strips zero-width and control
        characters on the way OUT, so a member writing `har<U+200B>assing` was
        scanned as one string and published as another, with the trigger word
        restored. The classifier now reads the same text the reader will.
        """
        parts = [redact.clean(getattr(self, name, "") or "") for name in PUBLISHED_FIELDS]
        parts += [redact.clean(x) for x in self.ai_tags]
        parts += [redact.clean(x) for x in self.evidence_summary]
        return " ".join(p for p in parts if p)

    # -- rendering -----------------------------------------------------------

    def public_title(self) -> str:
        """The issue title. Prefixed so a projected issue is recognisable."""
        return redact.for_github(
            f"[{CATEGORY_LABELS[self.category]}] {self.title}", limit=MAX_TITLE
        )

    def public_body(self) -> str:
        """The PUBLIC issue body, built from an allow-list.

        Nothing about the reporter, the channel, or any staff-only material can
        appear here, because nothing about them is assembled here. A field
        added to `Report` later is absent from this body until someone
        deliberately adds it — which is the direction a mistake should point.
        """
        gh = redact.for_github
        lines = [
            f"**Report type:** {CATEGORY_LABELS[self.category]}",
            "",
            "### What was reported",
            "",
            gh(self.description, limit=MAX_DESCRIPTION) or "_(no description)_",
        ]
        if self.repro_steps:
            lines += [
                "",
                "### What they did just before it",
                "",
                gh(self.repro_steps, limit=MAX_STEPS),
            ]
        facts = []
        if self.build_version:
            facts.append(f"- **Build:** {gh(self.build_version, limit=64)}")
        if self.device:
            facts.append(f"- **Device:** {gh(self.device, limit=MAX_DEVICE)}")
        if facts:
            lines += ["", "### Build and device", "", *facts]
        if self.evidence_summary:
            lines += [
                "",
                "### Run evidence",
                "",
                f"_Attached by the reporter, format `{gh(self.evidence_format, limit=64)}`, "
                "validated against the game's committed schema._",
                "",
                # Escaped HERE, not where the summary was produced. These
                # lines come from `evidence.Evidence.summary_lines`, whose
                # escaper is chosen per destination — and a summary rendered
                # for Discord carries backslash escapes that mean nothing on
                # GitHub while leaving `#123` live as a cross-reference. Re-
                # escaping is the only way this body can promise anything
                # about text it did not render itself.
                # The middot prefix belongs to the Discord rendering these
                # lines were produced for; a markdown list already has its own
                # bullet, and "- · Build ..." reads as a mistake.
                *[
                    f"- {gh(line.lstrip('· ').strip(), limit=400)}"
                    for line in self.evidence_summary
                ],
            ]
        if self.ai_summary:
            lines += [
                "",
                "### AI-derived summary",
                "",
                f"> {gh(self.ai_summary, limit=1000)}",
                "",
                "_Generated by Spider Bot from the report above. AI-derived, not the "
                "reporter's words — read the original text first._",
            ]
        lines += [
            "",
            "---",
            "",
            f"Filed by Spider Bot from the Slingy Spider Discord. Intake id `{self.id}`.",
            "The reporter's identity is deliberately not published; Spider Bot holds "
            "the return path privately so a fix can be reported back to them.",
        ]
        return "\n".join(lines)

    def marker(self) -> str:
        """The string that makes republication detectable. Must be unique."""
        return f"Intake id `{self.id}`"

    def labels(self) -> list[str]:
        """Labels for the projected issue.

        Reuses `spider-swing`'s existing taxonomy rather than inventing a
        competing one: `MEASURED` 2026-09-04, that repo has the nine GitHub
        defaults plus `owner-action`, `phase:0-swing-lab`, `type:feature` and
        `type:infrastructure`. `bug` and `enhancement` are its own defaults and
        `type:feature` is its own convention, so all three are reused as-is.
        `from-spider-bot` is the one label this adds, because nothing existing
        answers "where did this come from" and the developer needs to be able
        to filter the channel off.
        """
        out = ["from-spider-bot"]
        if self.category is Category.BUG:
            out.append("bug")
        elif self.category is Category.IDEA:
            out += ["enhancement", "type:feature"]
        elif self.category is Category.GAMEPLAY_FEEDBACK:
            out.append("enhancement")
        elif self.category is Category.TESTING_PROBLEM:
            out.append("question")
        return out

    # -- persistence ---------------------------------------------------------

    def as_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "category": str(self.category),
            "title": self.title,
            "description": self.description,
            "submitted_at": self.submitted_at,
            "correlation_id": self.correlation_id,
            "reporter": self.reporter.as_record() if self.reporter else None,
            "build_version": self.build_version,
            "device": self.device,
            "repro_steps": self.repro_steps,
            "evidence_summary": list(self.evidence_summary),
            "evidence_format": self.evidence_format,
            "ai_summary": self.ai_summary,
            "ai_tags": list(self.ai_tags),
            "sensitivity": str(self.sensitivity),
            "sensitivity_reason": self.sensitivity_reason,
            "reporter_cleared": self.reporter_cleared,
            "approved_by": self.approved_by,
            "github_issue_number": self.github_issue_number,
            "github_issue_url": self.github_issue_url,
            "duplicate_of": self.duplicate_of,
            "publish_failure": self.publish_failure,
            "publish_failure_retryable": self.publish_failure_retryable,
            "status": str(self.status),
            "resolution": self.resolution,
            "notes": list(self.notes),
        }

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> Report | None:
        """A stored record back into a Report, or None if it is not one.

        Tolerant of unknown extra keys, strict about the three fields that
        identify a report. An unreadable record is skipped, never guessed at.
        """
        if not isinstance(data, dict):
            return None
        if int(data.get("schema_version", SCHEMA_VERSION)) > SCHEMA_VERSION:
            return None  # written by a newer bot: refuse rather than mis-read
        try:
            category = Category(str(data["category"]))
            sensitivity = Sensitivity(str(data.get("sensitivity", "unclassified")))
            status = Status(str(data.get("status", "stored")))
        except (KeyError, ValueError):
            return None
        report_id = str(data.get("id") or "")
        if not report_id:
            return None
        return cls(
            id=report_id,
            category=category,
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            submitted_at=float(data.get("submitted_at") or 0.0),
            correlation_id=str(data.get("correlation_id") or ""),
            reporter=Reporter.from_record(data.get("reporter")),
            build_version=str(data.get("build_version") or ""),
            device=str(data.get("device") or ""),
            repro_steps=str(data.get("repro_steps") or ""),
            evidence_summary=tuple(str(x) for x in (data.get("evidence_summary") or [])),
            evidence_format=str(data.get("evidence_format") or ""),
            ai_summary=str(data.get("ai_summary") or ""),
            ai_tags=tuple(str(x) for x in (data.get("ai_tags") or []))[:MAX_TAGS],
            sensitivity=sensitivity,
            sensitivity_reason=str(data.get("sensitivity_reason") or ""),
            reporter_cleared=bool(data.get("reporter_cleared", True)),
            approved_by=str(data.get("approved_by") or ""),
            github_issue_number=data.get("github_issue_number"),
            github_issue_url=str(data.get("github_issue_url") or ""),
            duplicate_of=str(data.get("duplicate_of") or ""),
            publish_failure=str(data.get("publish_failure") or ""),
            publish_failure_retryable=bool(data.get("publish_failure_retryable", True)),
            status=status,
            resolution=str(data.get("resolution") or ""),
            notes=tuple(str(x) for x in (data.get("notes") or [])),
        )

    def with_(self, **changes: Any) -> Report:
        return replace(self, **changes)
