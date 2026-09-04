"""The policy engine: a table of rules, evaluated deterministically.

Two things make this a *policy engine* rather than a pile of if-statements, and
both are deliberate:

1. **The rules are data.** `DEFAULT_POLICY` is a tuple of frozen `PolicyRule`s.
   Changing what the system does to a medium-confidence personal attack is
   editing one row, and the row is printable — the owner/mod console can show
   the whole policy without anyone reading Python.
2. **The severe operations are not in the table at all.** `KICK` and `BAN`
   appear in no default rule, so the automatic path cannot reach them by any
   combination of category, severity and confidence. That is stronger than a
   guard clause: there is nothing to bypass. A moderator can still kick or ban —
   through the staff console, as a human action with a human actor recorded.

**The ordering rule:** rules are evaluated top to bottom and the first match
wins, so the table is written most-severe-first. `validate()` checks that
ordering property mechanically at import time, because a table where a
permissive rule shadows a strict one is a policy that silently under-acts, and
that is exactly the class of mistake nobody notices.

**The autonomy ceiling is the rollout lever.** Whatever the table decides,
`Policy.decide` clamps the result to `ceiling`. Shipping with a ceiling of
`FLAG_FOR_REVIEW` means the full classifier and policy path runs and records,
and nothing a member can see ever changes — which is what "start in shadow, earn
each enforcement class" looks like in one field.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from spiderbot.moderation.contracts import (
    HUMAN_CONFIRMED_OPERATIONS,
    HUMAN_ONLY_CATEGORIES,
    MUTATING_OPERATIONS,
    POLICY_VERSION,
    Category,
    Operation,
    Severity,
    Verdict,
)

#: How dangerous each operation is. Used to clamp to the ceiling and to check
#: the table's ordering. Ordering here is the ONLY place operation severity is
#: defined, so a new operation must be ranked or the checks below fail loudly.
OPERATION_RANK: dict[Operation, int] = {
    Operation.NOTHING: 0,
    Operation.FLAG_FOR_REVIEW: 1,
    Operation.DELETE_MESSAGE: 2,
    Operation.WARN: 3,
    Operation.TIMEOUT_SHORT: 4,
    Operation.TIMEOUT_LONG: 5,
    Operation.KICK: 6,
    Operation.BAN: 7,
}

ANY_CATEGORY: frozenset[Category] = frozenset()


@dataclass(frozen=True)
class PolicyRule:
    """One row. `categories` empty means "any category other than none"."""

    operation: Operation
    min_severity: Severity
    min_confidence: float
    categories: frozenset[Category] = ANY_CATEGORY
    requires_human: bool = False
    note: str = ""

    def matches(self, verdict: Verdict) -> bool:
        if self.categories and verdict.category not in self.categories:
            return False
        return (
            verdict.severity >= self.min_severity
            and verdict.confidence >= self.min_confidence
        )

    def describe(self) -> str:
        who = (
            "any category"
            if not self.categories
            else "/".join(sorted(str(c) for c in self.categories))
        )
        return (
            f"{who} at severity >= {self.min_severity.name.lower()} "
            f"and confidence >= {self.min_confidence:.2f} -> {self.operation}"
            + (" (human confirms)" if self.requires_human else "")
        )


#: The starting policy. Conservative by construction: nothing above a short
#: timeout is reachable automatically, and everything below high confidence
#: lands in the review queue rather than acting.
DEFAULT_POLICY: tuple[PolicyRule, ...] = (
    PolicyRule(
        operation=Operation.TIMEOUT_LONG,
        min_severity=Severity.SEVERE,
        min_confidence=0.90,
        categories=frozenset({Category.HATE, Category.SEXUAL_HARASSMENT}),
        note="Severe and near-certain hate or sexual harassment: stop it now, "
        "then a human reviews the case.",
    ),
    PolicyRule(
        operation=Operation.TIMEOUT_SHORT,
        min_severity=Severity.HIGH,
        min_confidence=0.90,
        categories=frozenset(
            {
                Category.HARASSMENT,
                Category.TARGETED_HOSTILITY,
                Category.HATE,
                Category.SEXUAL_HARASSMENT,
            }
        ),
        note="Sustained conduct aimed at someone, at high confidence.",
    ),
    PolicyRule(
        operation=Operation.DELETE_MESSAGE,
        min_severity=Severity.HIGH,
        min_confidence=0.90,
        categories=frozenset({Category.SCAM, Category.MALICIOUS_TEST_LINK}),
        note="A fake tester link is the one thing that damages people fastest "
        "in a server whose whole purpose is handing out install links.",
    ),
    PolicyRule(
        operation=Operation.WARN,
        min_severity=Severity.MEDIUM,
        min_confidence=0.85,
        categories=frozenset(
            {
                Category.HARASSMENT,
                Category.TARGETED_HOSTILITY,
                Category.PERSONAL_ATTACK,
                Category.HATE,
                Category.SEXUAL_HARASSMENT,
            }
        ),
        note="Say something, change nothing else.",
    ),
    PolicyRule(
        operation=Operation.FLAG_FOR_REVIEW,
        min_severity=Severity.MEDIUM,
        min_confidence=0.60,
        requires_human=True,
        note="Anything else the model is moderately sure about goes to a human. "
        "This is the rule that should fire most often.",
    ),
)


@dataclass(frozen=True)
class Decision:
    """What the deterministic side decided, and why. Never a model's output."""

    operation: Operation
    requires_human: bool
    rule_index: int | None
    rationale: str
    policy_version: str = POLICY_VERSION
    clamped_from: Operation | None = None

    @property
    def acts(self) -> bool:
        """True when this decision would change something a member can see.

        Membership in `MUTATING_OPERATIONS`, not inequality with `NOTHING`:
        `FLAG_FOR_REVIEW` changes nothing a member experiences, and reading it
        as an action would make the shipping default (ceiling
        `flag_for_review`) report itself as acting on every case.
        """
        return self.operation in MUTATING_OPERATIONS

    def as_record(self) -> dict[str, Any]:
        return {
            "operation": str(self.operation),
            "requires_human": self.requires_human,
            "rule_index": self.rule_index,
            "rationale": self.rationale,
            "policy_version": self.policy_version,
            "clamped_from": str(self.clamped_from) if self.clamped_from else None,
        }


NO_ACTION = Decision(
    operation=Operation.NOTHING,
    requires_human=False,
    rule_index=None,
    rationale="no rule matched",
)


class Policy:
    """Evaluates verdicts against a rule table. Pure; holds no Discord handle."""

    def __init__(
        self,
        rules: tuple[PolicyRule, ...] = DEFAULT_POLICY,
        *,
        ceiling: Operation = Operation.FLAG_FOR_REVIEW,
    ) -> None:
        self.rules = rules
        self.ceiling = ceiling

    def decide(self, verdict: Verdict | None) -> Decision:
        """The one entry point. A missing verdict is not a permissive default.

        A `None` verdict means the model failed, timed out, returned something
        malformed, or was never called. Every one of those means **no automatic
        action** — the owner's rule, and the reason this is the first branch
        rather than a fallthrough at the bottom where a later edit could slip
        past it.
        """
        if verdict is None:
            return Decision(
                operation=Operation.NOTHING,
                requires_human=False,
                rule_index=None,
                rationale="no valid verdict: nothing acts on absent or malformed "
                "model output",
            )
        if verdict.category is Category.NONE:
            return Decision(
                operation=Operation.NOTHING,
                requires_human=False,
                rule_index=None,
                rationale="the model found nothing to act on",
            )
        if verdict.category in HUMAN_ONLY_CATEGORIES:
            # A property of the category, not a tunable threshold: a message
            # about self-harm answered by a timeout is the worst thing this
            # system could do, and a threat is a person's decision to make.
            return self._clamp(
                Decision(
                    operation=Operation.FLAG_FOR_REVIEW,
                    requires_human=True,
                    rule_index=None,
                    rationale=f"{verdict.category} is always a human's call",
                )
            )

        for index, rule in enumerate(self.rules):
            if rule.matches(verdict):
                requires_human = (
                    rule.requires_human
                    or verdict.human_review_required
                    or rule.operation in HUMAN_CONFIRMED_OPERATIONS
                )
                return self._clamp(
                    Decision(
                        operation=rule.operation,
                        requires_human=requires_human,
                        rule_index=index,
                        rationale=rule.note or rule.describe(),
                    )
                )
        return NO_ACTION

    def _clamp(self, decision: Decision) -> Decision:
        """Never exceed the ceiling. The rollout lever, applied in one place."""
        if OPERATION_RANK[decision.operation] <= OPERATION_RANK[self.ceiling]:
            return decision
        return Decision(
            operation=self.ceiling,
            requires_human=True,
            rule_index=decision.rule_index,
            rationale=(
                f"{decision.rationale} (clamped from {decision.operation} by the "
                f"autonomy ceiling)"
            ),
            clamped_from=decision.operation,
        )

    def describe(self) -> list[str]:
        """The whole policy as readable lines, for the mod console."""
        lines = [f"Policy version {POLICY_VERSION}, ceiling {self.ceiling}."]
        lines += [f"{i + 1}. {r.describe()}" for i, r in enumerate(self.rules)]
        lines.append(
            "Always a human: "
            + ", ".join(sorted(str(c) for c in HUMAN_ONLY_CATEGORIES))
            + ". Never automatic: "
            + ", ".join(sorted(str(o) for o in HUMAN_CONFIRMED_OPERATIONS))
            + "."
        )
        return lines


def validate(rules: tuple[PolicyRule, ...] = DEFAULT_POLICY) -> list[str]:
    """Structural checks on a rule table. Empty list means healthy.

    Run at boot beside `routes.validate()`, and asserted in the tests. Three
    properties, each of which has a silent failure mode if unchecked:

    - **No autonomous kick or ban.** A table row producing one without
      `requires_human` would make the un-bypassable rule bypassable.
    - **Dead rules.** A rule that an earlier rule already matches everything of
      can never fire, and the system quietly under-acts forever. The check is
      exact rather than a severity-ordering heuristic: it asks whether every
      verdict matching the later rule also matches the earlier one, which is
      the only form of the question that survives disjoint category sets. Two
      rules covering different categories do not shadow each other however
      their operations rank, and an ordering heuristic reports that as a defect
      (it did, on the first run of this table, against rules that are correct).
    - **Confidence in range.** A threshold above 1.0 makes a rule dead; a
      threshold of 0.0 makes it fire on a coin flip.
    """
    problems: list[str] = []
    for index, rule in enumerate(rules):
        if rule.operation in HUMAN_CONFIRMED_OPERATIONS and not rule.requires_human:
            problems.append(
                f"rule {index} produces {rule.operation} without human confirmation"
            )
        if not 0.0 < rule.min_confidence <= 1.0:
            problems.append(
                f"rule {index} has confidence threshold {rule.min_confidence}, "
                "which is outside (0.0, 1.0]"
            )
        if rule.operation not in OPERATION_RANK:
            problems.append(f"rule {index} uses unranked operation {rule.operation}")
    for later_index in range(1, len(rules)):
        later = rules[later_index]
        for earlier_index in range(later_index):
            if _shadows(rules[earlier_index], later):
                problems.append(
                    f"rule {later_index} ({later.operation}) can never fire: "
                    f"rule {earlier_index} ({rules[earlier_index].operation}) "
                    "above it already matches everything it would"
                )
                break
    return problems


def _shadows(earlier: PolicyRule, later: PolicyRule) -> bool:
    """True when every verdict matching `later` already matched `earlier`.

    Exact, not heuristic. All three conditions must hold: the earlier rule
    covers at least the later one's categories (an empty set means every
    category), and both of its thresholds are no higher.
    """
    covers_categories = not earlier.categories or later.categories <= earlier.categories
    if later.categories == ANY_CATEGORY and earlier.categories != ANY_CATEGORY:
        covers_categories = False
    return (
        covers_categories
        and earlier.min_severity <= later.min_severity
        and earlier.min_confidence <= later.min_confidence
    )
