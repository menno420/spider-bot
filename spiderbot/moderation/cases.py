"""One moderation case model. Not five unconnected systems.

The owner's requirement, and it is a structural one: AI logs, warnings,
timeouts, staff notes and appeals must not each live in their own place. A
moderator asking *"what has happened with this member?"* should read one list.

So a case is created for **every** decision the pipeline reaches, including the
ones where nothing happened — a shadow verdict, a flag, a refusal by the gate.
That is what makes the review surface able to answer the question shadow mode
exists to ask: *was this decision right?* A system that only records the actions
it took cannot be evaluated, because its false positives are exactly the entries
it did not write.

Cases are private. They carry a member's conduct and a model's judgement of it,
and neither is ever published to GitHub or shown outside the staff channel.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from spiderbot import redact
from spiderbot.moderation.contracts import MUTATING_OPERATIONS, Operation


class Source(StrEnum):
    """What started this case."""

    AI_MESSAGE_SCAN = "ai_message_scan"
    DETERMINISTIC_RULE = "deterministic_rule"
    STAFF_ACTION = "staff_action"
    MEMBER_REPORT = "member_report"


class Mode(StrEnum):
    """Which mode the moderation service was in when the case was made."""

    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"


class CaseStatus(StrEnum):
    OPEN = "open"
    ACTED = "acted"
    SHADOW_ONLY = "shadow_only"
    REFUSED = "refused"
    REVIEWED = "reviewed"
    APPEALED = "appealed"


class ReviewOutcome(StrEnum):
    """What a moderator says about a decision. The evaluation data.

    These four are the owner's own list, and they are chosen so that the
    aggregate is directly actionable: too many `TOO_STRICT` means the
    confidence thresholds are low, too many `TOO_LENIENT` means they are high,
    and `WRONG_CATEGORY` points at the classifier rather than the policy.
    """

    CORRECT = "correct"
    TOO_STRICT = "too_strict"
    TOO_LENIENT = "too_lenient"
    WRONG_CATEGORY = "wrong_category"


@dataclass(frozen=True)
class Case:
    id: str
    created_at: float
    mode: Mode
    source: Source
    guild_id: int | None = None
    channel_id: int | None = None
    message_id: int | None = None
    subject_id: int | None = None
    subject_name: str = ""
    actor: str = "spider-bot"
    correlation_id: str = ""

    #: The model's judgement, if there was one. `None` means the AI never ran,
    #: failed, or returned something invalid - and `verdict_rejection` says which.
    verdict: dict[str, Any] | None = None
    verdict_rejection: str = ""
    deterministic_rule: str = ""

    decision: dict[str, Any] | None = None
    operation: Operation = Operation.NOTHING
    #: What actually happened to the member. In shadow mode this is always
    #: `nothing`, whatever `operation` says - that gap IS the shadow record.
    performed: Operation = Operation.NOTHING
    refusal_reason: str = ""

    status: CaseStatus = CaseStatus.OPEN
    review_outcome: ReviewOutcome | None = None
    review_note: str = ""
    reviewed_by: str = ""
    reviewed_at: float | None = None
    appeal_outcome: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def acted(self) -> bool:
        return self.performed is not Operation.NOTHING

    @property
    def would_have_acted(self) -> bool:
        """True when the policy chose an action that shadow mode withheld."""
        # Membership in `MUTATING_OPERATIONS`, not inequality with NOTHING.
        # Codex, spider-bot#3, 2026-09-04: with the SHIPPING ceiling
        # (`flag_for_review`) every clamped case counted as "would have acted"
        # — so the mod-log fired and Home's count inflated for an outcome the
        # ceiling deliberately permits, and the summary said "would have done
        # flag_for_review" instead of naming the operation actually withheld.
        return self.operation in MUTATING_OPERATIONS and not self.acted

    def summary_line(self) -> str:
        """One line for a staff list. Member text is escaped for Discord."""
        who = redact.for_discord(self.subject_name or str(self.subject_id or "?"), limit=32)
        category = (self.verdict or {}).get("category", "-")
        confidence = (self.verdict or {}).get("confidence")
        confidence_text = f"{confidence:.0%}" if isinstance(confidence, (int, float)) else "-"
        if self.acted:
            what = f"did {self.performed}"
        elif self.would_have_acted:
            what = f"would have done {self.operation}"
        elif (self.decision or {}).get("clamped_from"):
            # The ceiling held something back. Naming the operation the policy
            # WANTED is the whole point of running shadow mode: "would have
            # done flag_for_review" tells a reviewer nothing.
            what = f"policy wanted {(self.decision or {})['clamped_from']}, ceiling held it"
        elif self.refusal_reason:
            what = "refused"
        else:
            what = "no action"
        reviewed = f" · reviewed {self.review_outcome}" if self.review_outcome else ""
        return f"`{self.id}` {who} — {category} {confidence_text} — {what}{reviewed}"

    def as_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "mode": str(self.mode),
            "source": str(self.source),
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "subject_id": self.subject_id,
            "subject_name": self.subject_name,
            "actor": self.actor,
            "correlation_id": self.correlation_id,
            "verdict": self.verdict,
            "verdict_rejection": self.verdict_rejection,
            "deterministic_rule": self.deterministic_rule,
            "decision": self.decision,
            "operation": str(self.operation),
            "performed": str(self.performed),
            "refusal_reason": self.refusal_reason,
            "status": str(self.status),
            "review_outcome": str(self.review_outcome) if self.review_outcome else None,
            "review_note": self.review_note,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "appeal_outcome": self.appeal_outcome,
            "notes": list(self.notes),
        }

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> Case | None:
        if not isinstance(data, dict) or not data.get("id"):
            return None
        try:
            mode = Mode(str(data.get("mode", "shadow")))
            source = Source(str(data.get("source", "ai_message_scan")))
            status = CaseStatus(str(data.get("status", "open")))
            operation = Operation(str(data.get("operation", "nothing")))
            performed = Operation(str(data.get("performed", "nothing")))
        except ValueError:
            return None
        review = data.get("review_outcome")
        try:
            review_outcome = ReviewOutcome(str(review)) if review else None
        except ValueError:
            review_outcome = None
        return cls(
            id=str(data["id"]),
            created_at=float(data.get("created_at") or time.time()),
            mode=mode,
            source=source,
            guild_id=data.get("guild_id"),
            channel_id=data.get("channel_id"),
            message_id=data.get("message_id"),
            subject_id=data.get("subject_id"),
            subject_name=str(data.get("subject_name") or ""),
            actor=str(data.get("actor") or "spider-bot"),
            correlation_id=str(data.get("correlation_id") or ""),
            verdict=data.get("verdict") if isinstance(data.get("verdict"), dict) else None,
            verdict_rejection=str(data.get("verdict_rejection") or ""),
            deterministic_rule=str(data.get("deterministic_rule") or ""),
            decision=data.get("decision") if isinstance(data.get("decision"), dict) else None,
            operation=operation,
            performed=performed,
            refusal_reason=str(data.get("refusal_reason") or ""),
            status=status,
            review_outcome=review_outcome,
            review_note=str(data.get("review_note") or ""),
            reviewed_by=str(data.get("reviewed_by") or ""),
            reviewed_at=data.get("reviewed_at"),
            appeal_outcome=str(data.get("appeal_outcome") or ""),
            notes=tuple(str(n) for n in (data.get("notes") or [])),
        )

    def with_(self, **changes: Any) -> Case:
        return replace(self, **changes)


def review_tally(cases: list[Case]) -> dict[str, int]:
    """How the policy is doing, from what moderators actually said.

    The number that matters is not how many cases exist but how many were
    judged, and how they split. A policy nobody has reviewed is not evidence
    for enabling anything — which is the whole point of shadow mode.
    """
    tally = {str(outcome): 0 for outcome in ReviewOutcome}
    tally["unreviewed"] = 0
    for case in cases:
        if case.review_outcome is None:
            tally["unreviewed"] += 1
        else:
            tally[str(case.review_outcome)] += 1
    return tally
