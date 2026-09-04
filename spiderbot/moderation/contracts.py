"""The typed moderation verdict, and the only door model output comes through.

Everything here is pure: no Discord, no model client, no I/O. That is what lets
the hostile-input tests be exhaustive and fast, and it is what keeps the model
side of the pipeline unable to reach the acting side.

**The rule this file exists to enforce:** free-form prose is never parsed into a
moderation action. A model returns a JSON object; `parse_verdict` either
produces a fully-validated `Verdict` or produces nothing. There is no partial
verdict, no "best effort" field, and no default that lets a malformed response
act. Invalid or incomplete model output means **no automatic action** — the
owner's words, and the reason every rejection below returns `None` with a
reason rather than a lenient guess.

**The evidence quote is the anti-hallucination check, and it is the sharpest
tool here.** The model must return the span of the message it is judging,
verbatim. A verdict whose quote is not actually present in the content it
analysed is discarded — which catches a model that invented a message, judged
the wrong message, or was talked by an injected instruction into "quoting"
something the member never wrote. It costs one substring test and it is the
only structural check that can tell a real reading from a plausible one.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any

# Bumped whenever the categories, the operations, or the meaning of a severity
# changes. It is stamped onto every case so a later review knows which rules
# produced a decision, and so a policy change does not silently reinterpret the
# history it is being evaluated against.
POLICY_VERSION = "1"

MAX_REASON_CHARS = 400
MAX_QUOTE_CHARS = 400


class Category(StrEnum):
    """What kind of problem, closed set.

    Deliberately about *conduct toward people*, because that is what a
    deterministic rule cannot judge and a context-reading model can. Volume,
    duplication, mention floods, invite spam and link filtering are NOT here:
    Discord's own AutoMod does those natively and better, and
    `docs/product-shape.md` lists rebuilding them as a standing non-goal.
    """

    NONE = "none"
    HARASSMENT = "harassment"
    TARGETED_HOSTILITY = "targeted_hostility"
    PERSONAL_ATTACK = "personal_attack"
    THREAT = "threat"
    HATE = "hate"
    SEXUAL_HARASSMENT = "sexual_harassment"
    SCAM = "scam"
    MALICIOUS_TEST_LINK = "malicious_test_link"
    COERCION = "coercion"
    RULE_EVASION = "rule_evasion"
    SELF_HARM_CONCERN = "self_harm_concern"


#: Categories where the right answer is a human, never an automatic action —
#: whatever the confidence. Recorded here rather than in the policy table
#: because it is a property of the category, not of a threshold someone might
#: tune. A self-harm message answered by a timeout is the worst outcome this
#: system could produce.
HUMAN_ONLY_CATEGORIES: frozenset[Category] = frozenset(
    {Category.SELF_HARM_CONCERN, Category.THREAT}
)


class Severity(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    SEVERE = 4


class Operation(StrEnum):
    """What the system may do, ordered by how hard it is to undo.

    `NOTHING` and `FLAG_FOR_REVIEW` change nothing a member can see.
    `DELETE_MESSAGE` and `WARN` are visible and reversible in effect if not in
    fact. `TIMEOUT_*` restrict. `KICK` and `BAN` are the two that end someone's
    presence, and neither is ever autonomous — see `policy.py`.
    """

    NOTHING = "nothing"
    FLAG_FOR_REVIEW = "flag_for_review"
    DELETE_MESSAGE = "delete_message"
    WARN = "warn"
    TIMEOUT_SHORT = "timeout_short"
    TIMEOUT_LONG = "timeout_long"
    KICK = "kick"
    BAN = "ban"


#: Operations no policy may ever take without a human pressing a button. This
#: is a property of the operation, so it lives beside the enum rather than in
#: the tunable table: a future policy edit cannot widen it by accident.
HUMAN_CONFIRMED_OPERATIONS: frozenset[Operation] = frozenset(
    {Operation.KICK, Operation.BAN}
)

#: Operations that mutate something a member experiences. `FLAG_FOR_REVIEW` is
#: deliberately absent: flagging is what shadow mode does for real.
MUTATING_OPERATIONS: frozenset[Operation] = frozenset(
    {
        Operation.DELETE_MESSAGE,
        Operation.WARN,
        Operation.TIMEOUT_SHORT,
        Operation.TIMEOUT_LONG,
        Operation.KICK,
        Operation.BAN,
    }
)


class Rejection(StrEnum):
    """Why a model response did not become a verdict.

    Every one of these is audited by its own name rather than as a generic
    "invalid", because the distribution across them is the signal that says
    whether the classifier is drifting, being injected, or simply being asked
    a badly-shaped question.
    """

    NOT_JSON = "not_json"
    NOT_AN_OBJECT = "not_an_object"
    MISSING_FIELD = "missing_field"
    UNKNOWN_CATEGORY = "unknown_category"
    UNKNOWN_OPERATION = "unknown_operation"
    BAD_SEVERITY = "bad_severity"
    BAD_CONFIDENCE = "bad_confidence"
    QUOTE_NOT_IN_CONTENT = "quote_not_in_content"
    QUOTE_MISSING = "quote_missing"
    EMPTY_RESPONSE = "empty_response"


@dataclass(frozen=True)
class Verdict:
    """A model's judgement, fully validated. Constructing one is a claim that
    every field came through `parse_verdict`."""

    category: Category
    severity: Severity
    confidence: float
    reason: str
    evidence_quote: str
    recommended_operation: Operation
    human_review_required: bool
    model: str
    policy_version: str = POLICY_VERSION
    targets_member: bool = False

    def as_record(self) -> dict[str, Any]:
        return {
            "category": str(self.category),
            "severity": int(self.severity),
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "evidence_quote": self.evidence_quote,
            "recommended_operation": str(self.recommended_operation),
            "human_review_required": self.human_review_required,
            "model": self.model,
            "policy_version": self.policy_version,
            "targets_member": self.targets_member,
        }


@dataclass(frozen=True)
class ParseResult:
    """Exactly one of `verdict` or `rejection` is set."""

    verdict: Verdict | None = None
    rejection: Rejection | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict is not None


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_WHITESPACE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """Fold the differences a model reliably introduces when it quotes.

    NFKC because a model echoing a fullwidth or ligature character back as its
    ASCII form is quoting faithfully; casefold because capitalisation is not
    evidence; whitespace collapse because a model re-wraps. Nothing here weakens
    the check into "roughly similar" — the quote must still be a contiguous run
    of the member's actual words.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _WHITESPACE.sub(" ", folded).strip()


def _extract_json(raw: str) -> str:
    """The JSON object out of a response that may be fenced or prefaced.

    Models add prose. This tolerates the wrapper and nothing else: what comes
    out still has to parse as JSON and still has to validate. Being lenient
    about a code fence is not the same as being lenient about content.
    """
    fenced = _FENCE.search(raw)
    if fenced:
        return fenced.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        return raw[start : end + 1]
    return raw.strip()


def parse_verdict(raw: str | None, *, content: str, model: str) -> ParseResult:
    """Model output into a `Verdict`, or a named rejection. Never raises.

    `content` is the exact text the model was asked to judge; the evidence
    quote is checked against it. Passing the wrong `content` here would defeat
    the check, so callers pass the same string they wrapped into the prompt.
    """
    if raw is None or not raw.strip():
        return ParseResult(rejection=Rejection.EMPTY_RESPONSE)

    try:
        parsed = json.loads(_extract_json(raw))
    except (ValueError, RecursionError):
        return ParseResult(rejection=Rejection.NOT_JSON, detail=raw[:120])
    if not isinstance(parsed, dict):
        return ParseResult(rejection=Rejection.NOT_AN_OBJECT)

    required = (
        "category",
        "severity",
        "confidence",
        "reason",
        "evidence_quote",
        "recommended_operation",
        "human_review_required",
    )
    missing = [f for f in required if f not in parsed]
    if missing:
        return ParseResult(rejection=Rejection.MISSING_FIELD, detail=",".join(missing))

    try:
        category = Category(str(parsed["category"]).strip().lower())
    except ValueError:
        return ParseResult(
            rejection=Rejection.UNKNOWN_CATEGORY, detail=str(parsed["category"])[:60]
        )
    try:
        operation = Operation(str(parsed["recommended_operation"]).strip().lower())
    except ValueError:
        return ParseResult(
            rejection=Rejection.UNKNOWN_OPERATION,
            detail=str(parsed["recommended_operation"])[:60],
        )

    severity_raw = parsed["severity"]
    if isinstance(severity_raw, bool) or not isinstance(severity_raw, int):
        return ParseResult(rejection=Rejection.BAD_SEVERITY, detail=repr(severity_raw)[:60])
    try:
        severity = Severity(severity_raw)
    except ValueError:
        return ParseResult(rejection=Rejection.BAD_SEVERITY, detail=repr(severity_raw)[:60])

    confidence_raw = parsed["confidence"]
    if isinstance(confidence_raw, bool) or not isinstance(confidence_raw, (int, float)):
        return ParseResult(rejection=Rejection.BAD_CONFIDENCE, detail=repr(confidence_raw)[:60])
    confidence = float(confidence_raw)
    if not 0.0 <= confidence <= 1.0 or confidence != confidence:  # NaN fails both
        return ParseResult(rejection=Rejection.BAD_CONFIDENCE, detail=repr(confidence_raw)[:60])

    quote = str(parsed["evidence_quote"])[:MAX_QUOTE_CHARS]
    if category is Category.NONE:
        # Nothing to point at, and demanding a quote for "this is fine" would
        # push the model to manufacture one.
        quote = quote.strip()
    else:
        if not quote.strip():
            return ParseResult(rejection=Rejection.QUOTE_MISSING)
        if _normalise(quote) not in _normalise(content):
            # The check that catches an invented message, the wrong message, or
            # an injected instruction persuading the model to "quote" something
            # nobody wrote.
            return ParseResult(
                rejection=Rejection.QUOTE_NOT_IN_CONTENT, detail=quote[:80]
            )

    return ParseResult(
        verdict=Verdict(
            category=category,
            severity=severity,
            confidence=confidence,
            reason=str(parsed["reason"])[:MAX_REASON_CHARS],
            evidence_quote=quote,
            recommended_operation=operation,
            human_review_required=bool(parsed["human_review_required"]),
            model=model,
            targets_member=bool(parsed.get("targets_member", False)),
        )
    )


#: The schema shown to the model. Kept beside the parser so the two cannot
#: drift: a field added to one without the other is immediately a test failure.
RESPONSE_SCHEMA_DESCRIPTION = """\
Return ONE JSON object and nothing else, with exactly these keys:
  "category": one of {categories}
  "severity": integer 0-4 (0 none, 1 low, 2 medium, 3 high, 4 severe)
  "confidence": number between 0.0 and 1.0
  "reason": one short sentence, at most {reason} characters, describing WHY
  "evidence_quote": the exact substring of the message that shows it, copied
      character-for-character from the message. If category is "none", use "".
      A quote that is not present verbatim in the message is discarded and the
      whole verdict with it.
  "recommended_operation": one of {operations}
  "human_review_required": true or false
  "targets_member": true if the conduct is aimed at a specific person present
      in this server, false if it is general
""".format(
    categories=", ".join(f'"{c}"' for c in Category),
    operations=", ".join(f'"{o}"' for o in Operation),
    reason=MAX_REASON_CHARS,
)


@dataclass(frozen=True)
class Analysis:
    """What the classifier hands the policy engine: a verdict, or why not.

    Carrying the rejection rather than swallowing it is what makes
    "the model failed" and "the model said nothing is wrong" distinguishable in
    the audit log — one needs attention and the other is the happy path.
    """

    verdict: Verdict | None
    rejection: Rejection | None = None
    detail: str = ""
    model: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.verdict is not None
