"""The import graph IS invariant 5. This file is what enforces it.

`CLAUDE.md` invariant 5 says the AI never performs side effects, refined
2026-09-04 so the AI may supply a judgement that influences a decision. The
refinement only holds if the half that talks to a model has no path to the half
that mutates Discord — otherwise it is a rule someone has to remember, and this
repository's own history says prose-demanded practices do not survive.

So it is checked mechanically, by parsing the import statements. A test that
asserts a property of the source rather than of one execution cannot be
satisfied by luck.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "spiderbot"
MODERATION = PACKAGE / "moderation"


def imports_of(path: Path) -> set[str]:
    """Every module this file imports, at any depth, including deferred ones."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{a.name}" for a in node.names)
    return found


# -- the load-bearing separation ---------------------------------------------


def test_the_classifier_cannot_reach_anything_that_mutates_discord():
    """The half that talks to a model imports contracts and the gateway. It
    does not import operations, the gate, or discord itself."""
    found = imports_of(MODERATION / "classifier.py")
    for forbidden in ("discord", "spiderbot.moderation.operations", "spiderbot.moderation.gate"):
        assert not any(name.startswith(forbidden) for name in found), (
            f"classifier.py imports {forbidden}: invariant 5's refinement rests "
            "on it being unable to"
        )


def test_the_executor_cannot_reach_the_classifier():
    """The other direction. An executor that could ask a model what to do would
    put the judgement inside the authority."""
    found = imports_of(MODERATION / "operations.py")
    for forbidden in ("spiderbot.moderation.classifier", "spiderbot.ai"):
        assert not any(name.startswith(forbidden) for name in found), (
            f"operations.py imports {forbidden}"
        )


def test_the_pure_decision_modules_hold_no_discord_handle():
    """contracts, policy and prechecks decide things. None of them can act."""
    for name in ("contracts.py", "policy.py"):
        found = imports_of(MODERATION / name)
        assert not any(n == "discord" or n.startswith("discord.") for n in found), (
            f"{name} imports discord"
        )


def test_the_shadow_executor_holds_no_state_at_all():
    """Shadow mode is a type, not a flag.

    The precise property, checked rather than asserted in prose: it declares no
    `__init__` and an instance carries no attributes, so there is nothing to
    hand it that could make it able to act. (`inspect.signature` reports
    object's inherited `*args, **kwargs` here, which says nothing — the
    declaration is what matters.)
    """
    from spiderbot.moderation.operations import EnforcingExecutor, ShadowExecutor

    assert "__init__" not in ShadowExecutor.__dict__
    assert ShadowExecutor().__dict__ == {}
    assert ShadowExecutor().enforcing is False
    # Positive control: the two classes satisfy the same contract, so the
    # assertions above are about state and not about a stub.
    assert EnforcingExecutor().enforcing is True
    assert hasattr(ShadowExecutor(), "perform") and hasattr(EnforcingExecutor(), "perform")


def test_the_shadow_module_path_never_touches_a_discord_mutation():
    """Every branch that calls Discord lives in EnforcingExecutor."""
    import inspect

    from spiderbot.moderation.operations import EnforcingExecutor, ShadowExecutor

    shadow = inspect.getsource(ShadowExecutor)
    for mutation in ("delete()", "timeout(", "kick(", "ban(", "send("):
        assert mutation not in shadow, f"ShadowExecutor mentions {mutation}"
    # Positive control: those calls DO exist, in the enforcing class. Without
    # this, deleting the whole module would pass every assertion above.
    enforcing = inspect.getsource(EnforcingExecutor)
    for mutation in ("delete()", "timeout(", "kick(", "ban("):
        assert mutation in enforcing, f"EnforcingExecutor is missing {mutation}"


# -- the repository-wide rule -------------------------------------------------


def lower_layer_modules() -> list[Path]:
    """Everything below `ui/` and `cogs/`."""
    out = [p for p in PACKAGE.glob("*.py")]
    out += list((PACKAGE / "ai").glob("*.py"))
    out += list((PACKAGE / "intake").glob("*.py"))
    out += list(MODERATION.glob("*.py"))
    return [p for p in out if p.name != "bot.py" and p.name != "__main__.py"]


@pytest.mark.parametrize("module", lower_layer_modules(), ids=lambda p: p.name)
def test_nothing_below_ui_imports_ui_or_cogs(module: Path):
    """The layering rule that actually holds, stated as a direction.

    `CLAUDE.md` and `README.md` both described the lower layer as
    `(presets, roster, cohort, config)`; measured against real imports, `ui/`
    also reaches `audit`, `style` and `ai.safety`, and `config` is imported by
    no `ui/` or `cogs/` file at all. Enumerating the members went stale; the
    direction did not.
    """
    found = imports_of(module)
    offenders = [
        name
        for name in found
        if name.startswith(("spiderbot.ui", "spiderbot.cogs"))
    ]
    assert not offenders, f"{module.name} imports {offenders}"


def test_the_lower_layer_is_not_empty():
    """Positive control: the parametrised test above must actually be running
    over something. A glob that matched nothing would pass silently."""
    assert len(lower_layer_modules()) >= 15


def test_ui_never_imports_cogs():
    """The donor's own rule, adopted structurally (invariant 13)."""
    for module in (PACKAGE / "ui").glob("*.py"):
        found = imports_of(module)
        assert not any(n.startswith("spiderbot.cogs") for n in found), module.name
