"""spiderbot/moderation/contracts.py + policy.py - the typed verdict and the rules.

The question this file answers is the one the owner's word "reliable" asks:
can a model, malfunctioning or being manipulated, cause an action? Every test
below is a way of trying, and the expected answer is always no.
"""

from __future__ import annotations

import json

import pytest

from spiderbot.moderation import policy as P
from spiderbot.moderation.contracts import (
    HUMAN_CONFIRMED_OPERATIONS,
    Category,
    Operation,
    Rejection,
    Severity,
    Verdict,
    parse_verdict,
)

CONTENT = "you are absolutely worthless and everyone here knows it"


def response(**overrides) -> str:
    body = {
        "category": "personal_attack",
        "severity": 3,
        "confidence": 0.93,
        "reason": "a direct insult aimed at another member",
        "evidence_quote": "you are absolutely worthless",
        "recommended_operation": "warn",
        "human_review_required": False,
        "targets_member": True,
    }
    body.update(overrides)
    return json.dumps(body)


def parsed(**overrides):
    return parse_verdict(response(**overrides), content=CONTENT, model="test-model")


def a_verdict(**overrides) -> Verdict:
    base = dict(
        category=Category.HARASSMENT,
        severity=Severity.HIGH,
        confidence=0.95,
        reason="r",
        evidence_quote="q",
        recommended_operation=Operation.BAN,
        human_review_required=False,
        model="test-model",
        # Harassment at high severity is person-directed by construction, and
        # the acting rules require the model to have said so. Left implicit,
        # every ceiling and threshold test below would silently be testing the
        # targeting fall-through instead of what it was written for.
        targets_member=True,
    )
    base.update(overrides)
    return Verdict(**base)


# -- parsing: the only door model output comes through ------------------------


def test_a_well_formed_verdict_parses():
    result = parsed()
    assert result.ok
    assert result.verdict.category is Category.PERSONAL_ATTACK
    assert result.verdict.severity is Severity.HIGH
    assert result.verdict.model == "test-model"


def test_a_verdict_wrapped_in_prose_or_a_fence_still_parses():
    """Models add wrappers. Tolerating a fence is not tolerating bad content."""
    fenced = "Here is my judgement:\n```json\n" + response() + "\n```\nHope that helps."
    assert parse_verdict(fenced, content=CONTENT, model="m").ok


@pytest.mark.parametrize(
    ("raw", "rejection"),
    [
        (None, Rejection.EMPTY_RESPONSE),
        ("", Rejection.EMPTY_RESPONSE),
        ("   ", Rejection.EMPTY_RESPONSE),
        ("I think this is harassment, you should time them out.", Rejection.NOT_JSON),
        ("[1, 2, 3]", Rejection.NOT_AN_OBJECT),
        ('"a string"', Rejection.NOT_AN_OBJECT),
    ],
)
def test_free_form_prose_is_never_parsed_into_an_action(raw, rejection):
    result = parse_verdict(raw, content=CONTENT, model="m")
    assert not result.ok and result.rejection is rejection


def test_a_missing_field_is_a_rejection_not_a_default():
    body = json.loads(response())
    del body["confidence"]
    result = parse_verdict(json.dumps(body), content=CONTENT, model="m")
    assert result.rejection is Rejection.MISSING_FIELD
    assert "confidence" in result.detail


def test_an_invented_category_is_rejected():
    assert parsed(category="thoughtcrime").rejection is Rejection.UNKNOWN_CATEGORY


def test_an_invented_operation_is_rejected():
    assert parsed(recommended_operation="delete_the_server").rejection is (
        Rejection.UNKNOWN_OPERATION
    )


@pytest.mark.parametrize("value", [-1, 5, 99, "3", 3.5, True, None])
def test_a_severity_outside_the_enum_is_rejected(value):
    assert parsed(severity=value).rejection is Rejection.BAD_SEVERITY


@pytest.mark.parametrize("value", [-0.1, 1.1, "0.9", True, None, float("nan")])
def test_a_confidence_outside_zero_to_one_is_rejected(value):
    if value != value:  # NaN cannot survive json.dumps round-trip as a literal
        raw = response().replace('"confidence": 0.93', '"confidence": NaN')
        assert parse_verdict(raw, content=CONTENT, model="m").rejection is (
            Rejection.NOT_JSON
        ) or parse_verdict(raw, content=CONTENT, model="m").rejection is (
            Rejection.BAD_CONFIDENCE
        )
        return
    assert parsed(confidence=value).rejection is Rejection.BAD_CONFIDENCE


# -- the evidence quote: the anti-hallucination check -------------------------


def test_a_quote_that_is_not_in_the_message_kills_the_verdict():
    """The check that catches a model judging a message it was never shown."""
    result = parsed(evidence_quote="I am going to find where you live")
    assert result.rejection is Rejection.QUOTE_NOT_IN_CONTENT


def test_an_empty_quote_kills_a_non_none_verdict():
    assert parsed(evidence_quote="").rejection is Rejection.QUOTE_MISSING


def test_a_none_verdict_needs_no_quote():
    """Demanding a quote for "this is fine" would push a model to invent one."""
    result = parse_verdict(
        response(category="none", evidence_quote="", recommended_operation="nothing"),
        content=CONTENT,
        model="m",
    )
    assert result.ok and result.verdict.category is Category.NONE


def test_the_quote_check_tolerates_how_a_model_actually_quotes():
    """Case, unicode form and re-wrapping are not evidence of hallucination."""
    for quote in (
        "You Are Absolutely Worthless",
        "you are  absolutely\n worthless",
        "you are absolutely worthless",
    ):
        assert parsed(evidence_quote=quote).ok, quote


def test_the_quote_check_is_not_a_fuzzy_match():
    """Tolerant about form, strict about words: a paraphrase is not a quote."""
    assert not parsed(evidence_quote="you are worthless entirely").ok


# -- policy: what the deterministic side decides ------------------------------


def test_the_default_policy_is_structurally_healthy():
    assert P.validate() == []


def test_no_default_rule_can_produce_a_kick_or_a_ban():
    """Not a guard clause - there is nothing in the table to bypass."""
    for rule in P.DEFAULT_POLICY:
        assert rule.operation not in HUMAN_CONFIRMED_OPERATIONS


def test_no_verdict_at_all_produces_no_action():
    """Malformed, timed out, or never called - all the same answer."""
    decision = P.Policy(ceiling=Operation.BAN).decide(None)
    assert decision.operation is Operation.NOTHING
    assert not decision.acts


def test_a_model_recommending_a_ban_does_not_get_one():
    """The recommendation is advisory. The policy decides."""
    decision = P.Policy(ceiling=Operation.TIMEOUT_LONG).decide(
        a_verdict(recommended_operation=Operation.BAN)
    )
    assert decision.operation is Operation.TIMEOUT_SHORT


def test_low_confidence_does_nothing():
    decision = P.Policy(ceiling=Operation.BAN).decide(
        a_verdict(confidence=0.30, severity=Severity.LOW)
    )
    assert decision.operation is Operation.NOTHING


def test_medium_confidence_reaches_a_human_rather_than_acting():
    decision = P.Policy(ceiling=Operation.BAN).decide(
        a_verdict(confidence=0.70, severity=Severity.MEDIUM, category=Category.SCAM)
    )
    assert decision.operation is Operation.FLAG_FOR_REVIEW
    assert decision.requires_human


@pytest.mark.parametrize("category", [Category.SELF_HARM_CONCERN, Category.THREAT])
def test_the_human_only_categories_never_act_however_confident(category):
    """A message about self-harm answered by a timeout is the worst outcome
    this system could produce."""
    decision = P.Policy(ceiling=Operation.BAN).decide(
        a_verdict(category=category, confidence=1.0, severity=Severity.SEVERE)
    )
    assert decision.operation is Operation.FLAG_FOR_REVIEW
    assert decision.requires_human


def test_category_none_does_nothing():
    assert not P.Policy(ceiling=Operation.BAN).decide(
        a_verdict(category=Category.NONE)
    ).acts


def test_the_ceiling_clamps_and_says_so():
    """The rollout lever: the whole path runs and nothing visible changes."""
    decision = P.Policy(ceiling=Operation.FLAG_FOR_REVIEW).decide(a_verdict())
    assert decision.operation is Operation.FLAG_FOR_REVIEW
    assert decision.clamped_from is Operation.TIMEOUT_SHORT
    assert decision.requires_human


def test_the_ceiling_does_not_promote():
    """Clamping is one-directional: a ceiling above the rule does not raise it."""
    decision = P.Policy(ceiling=Operation.BAN).decide(
        a_verdict(confidence=0.86, severity=Severity.MEDIUM, category=Category.PERSONAL_ATTACK)
    )
    assert decision.operation is Operation.WARN


def test_a_verdict_asking_for_human_review_gets_it_even_when_the_rule_would_act():
    decision = P.Policy(ceiling=Operation.BAN).decide(
        a_verdict(human_review_required=True)
    )
    assert decision.requires_human


def test_the_validator_catches_an_autonomous_ban():
    bad = (
        P.PolicyRule(
            operation=Operation.BAN, min_severity=Severity.SEVERE, min_confidence=0.99
        ),
    )
    assert any("without human confirmation" in p for p in P.validate(bad))


def test_the_validator_catches_a_dead_rule():
    dead = (
        P.PolicyRule(
            operation=Operation.FLAG_FOR_REVIEW,
            min_severity=Severity.LOW,
            min_confidence=0.10,
            requires_human=True,
        ),
        P.PolicyRule(
            operation=Operation.WARN,
            min_severity=Severity.HIGH,
            min_confidence=0.90,
            categories=frozenset({Category.HATE}),
        ),
    )
    assert any("can never fire" in p for p in P.validate(dead))


def test_the_validator_does_not_flag_rules_with_disjoint_categories():
    """Two rules covering different categories do not shadow each other however
    their operations rank. A rank heuristic reported the real table as broken."""
    fine = (
        P.PolicyRule(
            operation=Operation.DELETE_MESSAGE,
            min_severity=Severity.HIGH,
            min_confidence=0.90,
            categories=frozenset({Category.SCAM}),
        ),
        P.PolicyRule(
            operation=Operation.WARN,
            min_severity=Severity.MEDIUM,
            min_confidence=0.85,
            categories=frozenset({Category.HARASSMENT}),
            # Required by `validate()` for any mutating rule on a
            # person-directed category — this fixture is about shadowing, so
            # it satisfies the other invariant rather than tripping it.
            requires_targeting=True,
        ),
    )
    assert P.validate(fine) == []


def test_the_policy_prints_itself_for_the_console():
    lines = P.Policy().describe()
    assert any("ceiling" in line for line in lines)
    assert any("Never automatic" in line for line in lines)
    assert len(lines) >= len(P.DEFAULT_POLICY)


# -- the quote floor, found by the design pilot reading committed code --------


def test_a_one_character_quote_is_not_evidence():
    """MEASURED 2026-09-04: a fabricated harassment verdict at confidence 0.99
    against "the reel button feels a bit weak" was accepted on a quote of "a",
    because a single character is contained in almost any message."""
    result = parse_verdict(
        response(evidence_quote="a"),
        content="the reel button feels a bit weak on the newest build",
        model="m",
    )
    assert result.rejection is Rejection.QUOTE_TOO_SHORT


def test_a_message_shorter_than_the_floor_may_be_quoted_whole():
    """The floor must not make a short hostile message unjudgeable."""
    result = parse_verdict(response(evidence_quote="kys"), content="kys", model="m")
    assert result.ok


def test_a_substantial_quote_still_passes():
    """Positive control for the floor."""
    result = parse_verdict(
        response(evidence_quote="feels a bit weak"),
        content="the reel button feels a bit weak on the newest build",
        model="m",
    )
    assert result.ok


def test_flag_for_review_does_not_count_as_acting():
    """`Decision.acts` means "a member can see this". Reading flag_for_review as
    an action would make the shipping default report itself as acting on every
    case."""
    decision = P.Policy().decide(
        a_verdict(confidence=0.70, severity=Severity.MEDIUM, category=Category.SCAM)
    )
    assert decision.operation is Operation.FLAG_FOR_REVIEW
    assert not decision.acts


def test_every_mutating_operation_counts_as_acting():
    """Positive control: the predicate must not simply be False."""
    for operation in (Operation.DELETE_MESSAGE, Operation.WARN, Operation.TIMEOUT_SHORT):
        decision = P.Decision(
            operation=operation, requires_human=False, rule_index=0, rationale="r"
        )
        assert decision.acts


# -- what an adversarial review executed against the committed code -----------


def test_a_one_character_quote_cannot_clear_the_floor_by_expanding():
    """`MEASURED` 2026-09-04: the floor ran on the NFKC-normalised quote only,
    and exactly two characters in Unicode expand past eight characters under
    NFKC. U+FDFA is one of them, so a single character was accepted as
    evidence."""
    content = f"the reel button feels {chr(0xFDFA)} a bit weak honestly"
    result = parse_verdict(response(evidence_quote=chr(0xFDFA)), content=content, model="m")
    assert not result.ok and result.rejection is Rejection.QUOTE_TOO_SHORT


def test_invisible_characters_are_folded_out_of_the_containment_check():
    """A member sprinkling zero-width spaces through a sentence would otherwise
    have every honest verdict discarded as "not in content"."""
    content = "you are​ absolutely​ worthless and everyone here knows it"
    assert parse_verdict(
        response(evidence_quote="you are absolutely worthless"), content=content, model="m"
    ).ok
    # And the other direction: an invisible character inside a word of the
    # QUOTE. (Inside a space it would not match, and should not: folding an
    # invisible character out cannot conjure the space it replaced.)
    assert parse_verdict(
        response(evidence_quote="you are absolute​ly worthless"),
        content=CONTENT,
        model="m",
    ).ok


def test_folding_invisible_characters_does_not_make_the_check_fuzzy():
    """Positive control for the two above. Removing characters neither side can
    see must not let a quote the member never wrote through."""
    result = parse_verdict(
        response(evidence_quote="you should be permanently banned"),
        content=CONTENT,
        model="m",
    )
    assert not result.ok and result.rejection is Rejection.QUOTE_NOT_IN_CONTENT


# -- targets_member: asked for, parsed, and now actually read -----------------


def test_a_verdict_that_says_it_is_not_aimed_at_anyone_does_not_act():
    """`MEASURED` 2026-09-04: `targets_member` was parsed, stored and read by
    nothing, so a verdict explicitly saying "general frustration, not aimed at
    a person" still fired the timeout rule whose own note says "aimed at
    someone"."""
    # Ceiling raised past the shipping default, so the assertion below is
    # about targeting rather than about the clamp that would hide it.
    lenient = P.Policy(ceiling=Operation.TIMEOUT_LONG)
    decision = lenient.decide(
        a_verdict(category=Category.TARGETED_HOSTILITY, targets_member=False)
    )
    assert decision.operation is Operation.FLAG_FOR_REVIEW
    assert decision.requires_human
    assert decision.clamped_from is None, "it fell through the rule, it was not clamped"
    # Positive control: the identical verdict aimed at a person does act.
    aimed = lenient.decide(
        a_verdict(category=Category.TARGETED_HOSTILITY, targets_member=True)
    )
    assert aimed.operation is Operation.TIMEOUT_SHORT


def test_every_acting_rule_about_conduct_toward_people_requires_targeting():
    """The property, not the row numbers: any default rule that mutates
    something a member experiences and is about person-directed categories must
    require the model to say it was aimed at a person."""
    from spiderbot.moderation.contracts import MUTATING_OPERATIONS

    person_directed = {
        Category.HARASSMENT,
        Category.TARGETED_HOSTILITY,
        Category.PERSONAL_ATTACK,
        Category.HATE,
        Category.SEXUAL_HARASSMENT,
    }
    checked = 0
    for rule in P.DEFAULT_POLICY:
        if rule.operation in MUTATING_OPERATIONS and rule.categories <= person_directed:
            if not rule.categories:
                continue
            checked += 1
            assert rule.requires_targeting, f"{rule.operation} acts on an untargeted verdict"
    assert checked >= 3, "the loop above must actually be examining rules"


def test_a_targeting_rule_cannot_shadow_a_general_one():
    """The validator's shadow check had to learn about the new field, or it
    would report a correct table as broken."""
    narrow = P.PolicyRule(
        operation=Operation.WARN,
        min_severity=Severity.MEDIUM,
        min_confidence=0.5,
        requires_targeting=True,
    )
    broad = P.PolicyRule(
        operation=Operation.FLAG_FOR_REVIEW,
        min_severity=Severity.MEDIUM,
        min_confidence=0.5,
    )
    assert not P._shadows(narrow, broad)
    # Positive control: without the targeting difference it DOES shadow.
    assert P._shadows(
        P.PolicyRule(
            operation=Operation.WARN, min_severity=Severity.MEDIUM, min_confidence=0.5
        ),
        broad,
    )
    assert P.validate(P.DEFAULT_POLICY) == []


def test_the_validator_refuses_a_person_directed_rule_without_targeting():
    """Codex, spider-bot#3, 2026-09-04: `requires_targeting` narrowed the
    shipped table and nothing stopped the next edit widening it again — a
    routine policy change could reintroduce a rule that acts on a verdict
    saying "not aimed at anyone"."""
    loose = (
        P.PolicyRule(
            operation=Operation.TIMEOUT_SHORT,
            min_severity=Severity.HIGH,
            min_confidence=0.90,
            categories=frozenset({Category.HARASSMENT}),
        ),
    )
    problems = P.validate(loose)
    assert any("requires_targeting" in p for p in problems), problems

    # Two positive controls. The same rule WITH targeting is accepted...
    assert P.validate(
        (
            P.PolicyRule(
                operation=Operation.TIMEOUT_SHORT,
                min_severity=Severity.HIGH,
                min_confidence=0.90,
                categories=frozenset({Category.HARASSMENT}),
                requires_targeting=True,
            ),
        )
    ) == []
    # ...and a NON-mutating rule on the same categories does not need it, since
    # flagging a case for a human is not acting on anyone.
    assert P.validate(
        (
            P.PolicyRule(
                operation=Operation.FLAG_FOR_REVIEW,
                min_severity=Severity.MEDIUM,
                min_confidence=0.60,
                categories=frozenset({Category.HARASSMENT}),
                requires_human=True,
            ),
        )
    ) == []


def test_a_scam_rule_does_not_need_targeting():
    """The invariant is about conduct toward people. A fake tester link harms
    whoever clicks it and is aimed at nobody in particular."""
    assert P.validate(
        (
            P.PolicyRule(
                operation=Operation.DELETE_MESSAGE,
                min_severity=Severity.HIGH,
                min_confidence=0.90,
                categories=frozenset({Category.SCAM}),
            ),
        )
    ) == []
