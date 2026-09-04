"""spiderbot/ids.py - the ids that tie Discord to GitHub to a case.

Pure module, so these are pure tests: no Discord, no conftest fakes, an
injected clock and an injected entropy source so assertions are on exact
strings rather than on shapes.
"""

from __future__ import annotations

import pytest

from spiderbot import ids

FIXED_TIME = 1788523566.662
FIXED_RAND = 12345


def at(t=FIXED_TIME, r=FIXED_RAND):
    return {"now": lambda: t, "rand": lambda n: r}


def test_an_id_has_four_groups_and_names_its_kind():
    value = ids.mint(ids.KIND_REPORT, **at())
    prefix, kind, stamp, suffix = value.split("-")
    assert prefix == "SB"
    assert kind == ids.KIND_REPORT
    assert len(stamp) == 8
    assert len(suffix) == ids.SUFFIX_LENGTH


def test_the_same_clock_and_entropy_give_the_same_id():
    assert ids.mint(ids.KIND_REPORT, **at()) == ids.mint(ids.KIND_REPORT, **at())


def test_string_order_is_time_order():
    early = ids.mint(ids.KIND_REPORT, **at(t=1788423566.0, r=0))
    middle = ids.mint(ids.KIND_REPORT, **at(t=1788523566.0, r=0))
    late = ids.mint(ids.KIND_REPORT, **at(t=1788623566.0, r=0))
    assert sorted([late, early, middle]) == [early, middle, late]


def test_the_alphabet_excludes_the_characters_people_transcribe_wrongly():
    """I, L, O and U are the four that come back as 1, 1, 0 and V."""
    for bad in "ILOU":
        assert bad not in ids.ALPHABET


def test_every_minted_id_is_readable_back():
    for mint in (ids.report_id, ids.case_id, ids.correlation_id):
        assert ids.is_valid(mint())


def test_kind_is_checked_when_asked_for():
    report = ids.report_id()
    assert ids.is_valid(report, ids.KIND_REPORT)
    assert not ids.is_valid(report, ids.KIND_CASE)


def test_an_unknown_kind_raises_rather_than_minting_a_poisoned_id():
    """The one place this codebase raises: a bad id would reach GitHub."""
    with pytest.raises(ValueError):
        ids.mint("X")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "SB-R",
        "SB-R-01K2M9WQ",
        "SB-R-01K2M9WQ-7F3KZ2-EXTRA",
        "XX-R-01K2M9WQ-7F3KZ2",       # wrong prefix
        "SB-Z-01K2M9WQ-7F3KZ2",       # unknown kind
        "SB-R-01K2M9W-7F3KZ2",        # short stamp
        "SB-R-01K2M9WQ-7F3KZ",        # short suffix
        "SB-R-01K2M9WQ-7F3KZI",       # I is not in the alphabet
        "sb-r-01k2m9wq-7f3kz2",       # lowercase
        None,
        12345,
    ],
)
def test_anything_not_minted_here_is_rejected(value):
    """Ids arrive from outside - typed into a form, parsed out of an issue
    body - and reach a store lookup. Never trust the shape of one."""
    assert not ids.is_valid(value)


def test_suffix_entropy_is_large_enough_that_a_same_millisecond_clash_is_remote():
    """32**6 possibilities inside one millisecond."""
    assert len(ids.ALPHABET) ** ids.SUFFIX_LENGTH > 10**9


def test_ids_carry_nothing_about_a_person():
    """An id is printed in public and pasted into a public GitHub issue."""
    value = ids.report_id()
    body = value.split("-", 2)[2]
    assert all(c in ids.ALPHABET or c == "-" for c in body)
