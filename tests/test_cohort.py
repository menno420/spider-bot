"""spiderbot/cohort.py - the closed-test clock.

Google's rule is the product requirement: 12 testers, each opted in for 14
continuous days. These tests pin the arithmetic and, just as importantly, the
honesty rules - an unknown grant date is never counted as qualified, and a
finish date is never projected from fewer than the required number of people.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from spiderbot.cohort import (
    REQUIRED_TESTERS,
    WINDOW_DAYS,
    RosterEntry,
    report_lines,
    summarize,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def rec(name: str, days_ago: float | None):
    """A tester granted `days_ago` days before NOW (None = unknown date)."""
    at = None if days_ago is None else NOW - timedelta(days=days_ago)
    return RosterEntry(name=name, granted_at=at)


def roster(n: int, days_ago: float):
    return [rec(f"t{i:02d}", days_ago) for i in range(n)]


# -- empty and small rosters ------------------------------------------------


def test_empty_roster_reports_nothing_qualified():
    s = summarize([], now=NOW)
    assert (s.roster, s.qualified, s.clears_at) == (0, 0, None)
    assert s.short_by == REQUIRED_TESTERS
    assert "No verified testers yet" in s.verdict


def test_short_roster_never_projects_a_finish_date():
    # Eleven people all well past the window still cannot clear the bar.
    s = summarize(roster(11, 30), now=NOW)
    assert s.qualified == 11
    assert s.clears_at is None, "a date must not be projected below the required count"
    assert s.short_by == 1
    assert "1 more tester" in s.verdict


# -- day counting -----------------------------------------------------------


@pytest.mark.parametrize(
    ("days_ago", "expected_days", "qualified"),
    [
        (0, 0, False),
        (0.9, 0, False),
        (7, 7, False),
        (13.9, 13, False),
        (14, 14, True),
        (40, 40, True),
    ],
)
def test_day_count_and_qualification_boundary(days_ago, expected_days, qualified):
    s = summarize([rec("solo", days_ago)], now=NOW)
    standing = s.standings[0]
    assert standing.days == expected_days
    assert standing.qualified is qualified


def test_future_grant_date_clamps_to_day_zero():
    s = summarize([rec("clockskew", -3)], now=NOW)
    assert s.standings[0].days == 0
    assert s.standings[0].qualified is False


# -- the projection ---------------------------------------------------------


def test_clear_date_is_the_required_th_earliest():
    # Twelve granted a day apart: the 12th (oldest-last) sets the date.
    records = [rec(f"t{i:02d}", 12 - i) for i in range(REQUIRED_TESTERS)]
    s = summarize(records, now=NOW)
    newest_grant = NOW - timedelta(days=1)
    assert s.clears_at == newest_grant + timedelta(days=WINDOW_DAYS)
    assert s.buffer == 0
    assert "no spare testers" in s.verdict


def test_extra_testers_do_not_delay_the_clear_date():
    # A 13th tester granted today must not push the projection out.
    base = [rec(f"t{i:02d}", 10) for i in range(REQUIRED_TESTERS)]
    s_twelve = summarize(base, now=NOW)
    s_thirteen = summarize([*base, rec("latecomer", 0)], now=NOW)
    assert s_thirteen.clears_at == s_twelve.clears_at
    assert s_thirteen.buffer == 1
    assert "1 spare tester" in s_thirteen.verdict


def test_full_cohort_past_the_window_reports_cleared():
    s = summarize(roster(REQUIRED_TESTERS, 20), now=NOW)
    assert s.qualified == REQUIRED_TESTERS
    assert "Bar cleared" in s.verdict


# -- honesty about unknown dates -------------------------------------------


def test_unknown_grant_date_is_never_qualified():
    s = summarize([rec("mystery", None)], now=NOW)
    assert s.unknown_dates == 1
    assert s.standings[0].qualified is False
    assert s.standings[0].days is None
    assert s.clears_at is None


def test_unknown_dates_block_the_projection_but_count_on_the_roster():
    records = [*roster(REQUIRED_TESTERS - 1, 5), rec("mystery", None)]
    s = summarize(records, now=NOW)
    assert s.roster == REQUIRED_TESTERS  # they do hold the role
    assert s.short_by == 0
    assert s.clears_at is None  # ...but the date cannot be honestly projected
    assert "unknown" in s.verdict


# -- ordering and rendering -------------------------------------------------


def test_longest_serving_first_unknowns_last():
    s = summarize([rec("new", 1), rec("mystery", None), rec("old", 30)], now=NOW)
    assert [x.name for x in s.standings] == ["old", "new", "mystery"]


def test_report_lists_every_tester_with_days_remaining():
    s = summarize([rec("alice", 3), rec("bob", 20)], now=NOW)
    text = "\n".join(report_lines(s))
    assert "bob - day 20" in text and "cleared" in text
    assert "alice - day 3, 11 to go" in text


def test_report_flags_unknown_dates_to_the_owner():
    s = summarize([rec("mystery", None)], now=NOW)
    text = "\n".join(report_lines(s))
    assert "grant date unknown" in text
    assert "45 days" in text


def test_report_on_empty_roster_is_two_lines():
    assert len(report_lines(summarize([], now=NOW))) == 2


def test_report_stays_within_discord_message_limit():
    s = summarize(roster(40, 5), now=NOW)
    assert len("\n".join(report_lines(s))) < 2000
