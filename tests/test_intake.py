"""spiderbot/intake/ - one intake path, and the journeys that must not break.

Organised by the question each block answers, because a test count proves
nothing: the brief for this work names the journeys, and these are them.

  * explicit form -> stored
  * natural-language report -> the same service
  * GitHub works / GitHub fails then retry succeeds / retry cannot duplicate
  * a sensitive complaint never becomes public
  * malicious text cannot inject issue metadata or mentions
"""

from __future__ import annotations

import time
from asyncio import run

import pytest

from spiderbot import redact, store
from spiderbot.intake import github_sink, privacy
from spiderbot.intake import service as intake_service
from spiderbot.intake.models import Category, Report, Reporter, Sensitivity, Status

TICKS = "`" * 3


def a_report(**overrides) -> Report:
    base = dict(
        id="SB-R-TEST01",
        category=Category.BUG,
        title="Game freezes on release",
        description="The game froze when I released the silk near 3 km.",
        submitted_at=time.time(),
        reporter=Reporter(user_id=42, display_name="tester", channel_id=7, message_id=8),
        device="Pixel 7a, Android 15",
    )
    base.update(overrides)
    return Report(**base)


def a_service(github=None) -> intake_service.IntakeService:
    return intake_service.IntakeService(store.InMemoryStore(), github)


class FakeGitHub:
    """A GitHub that can be made to fail, and that records every create."""

    def __init__(self, *, fail: github_sink.PublishFailure | None = None) -> None:
        self.fail = fail
        self.created: list[tuple[str, str, list[str]]] = []
        self.searches: list[str] = []
        self.next_number = 100

    @property
    def available(self) -> bool:
        return True

    async def find_issue_by_marker(self, marker):
        self.searches.append(marker)
        for index, (_title, body, _labels) in enumerate(self.created):
            if marker in body:
                return github_sink.Published(100 + index, f"https://example/{100 + index}")
        return None

    async def create_issue(self, title, body, labels):
        if self.fail is not None:
            return self.fail
        self.created.append((title, body, labels))
        number = self.next_number
        self.next_number += 1
        return github_sink.Published(number, f"https://example/{number}")


# -- the journeys ------------------------------------------------------------


def test_an_explicit_bug_form_produces_a_durable_record_with_a_reference():
    svc = a_service()
    out = run(
        svc.file(
            category=Category.BUG,
            title="Game freezes on release",
            description="Froze when I released the silk.",
            reporter=Reporter(user_id=42),
            device="Pixel 7a",
        )
    )
    assert out.ok and out.stored
    assert out.report.id in out.reporter_message
    assert run(svc.get(out.report.id)) is not None


def test_a_natural_language_report_goes_through_the_same_service():
    """One implementation, many entry points: the conversational path passes an
    AI summary and nothing else changes."""
    svc = a_service()
    out = run(
        svc.file(
            category=Category.BUG,
            title="Freeze when releasing silk",
            description="i think i found a bug, the game froze when i let go of the silk",
            ai_summary="The game freezes when the player releases the silk mid-swing.",
            ai_tags=("freeze", "silk"),
            reporter=Reporter(user_id=42),
        )
    )
    assert out.ok
    stored = run(svc.get(out.report.id))
    assert stored.ai_summary.startswith("The game freezes")


def test_a_report_is_durable_before_anything_is_published():
    """Store first. GitHub is a sink, not the record."""
    github = FakeGitHub()
    svc = a_service(github)
    out = run(svc.file(category=Category.BUG, title="t", description="d"))
    assert run(svc.get(out.report.id)).status is Status.STORED
    assert github.created == [], "nothing may be published during filing"


def test_publication_returns_the_issue_reference_to_discord():
    github = FakeGitHub()
    svc = a_service(github)
    out = run(svc.file(category=Category.BUG, title="t", description="d"))
    published = run(svc.publish(out.report.id))
    assert published.published
    assert published.report.github_issue_number == 100
    assert "https://example/100" in published.reporter_message


def test_github_failing_leaves_the_report_queued_and_the_reporter_told_the_truth():
    github = FakeGitHub(fail=github_sink.PublishFailure("network", "boom", retryable=True))
    svc = a_service(github)
    out = run(svc.file(category=Category.BUG, title="t", description="d"))
    result = run(svc.publish(out.report.id))
    assert not result.published
    assert result.report.status is Status.PUBLISH_FAILED
    assert "Saved as" in result.reporter_message and "retry" in result.reporter_message
    assert [r.id for r in run(svc.pending_publication())] == [out.report.id]


def test_a_retry_after_an_outage_succeeds():
    github = FakeGitHub(fail=github_sink.PublishFailure("network", "boom"))
    svc = a_service(github)
    out = run(svc.file(category=Category.BUG, title="t", description="d"))
    run(svc.publish(out.report.id))
    github.fail = None                       # the outage ends
    results = run(svc.retry_pending())
    assert results[0].published
    assert run(svc.pending_publication()) == []


def test_a_retry_cannot_create_a_second_issue():
    """Five retries, one issue. The store record is the fast path."""
    github = FakeGitHub()
    svc = a_service(github)
    out = run(svc.file(category=Category.BUG, title="t", description="d"))
    for _ in range(5):
        run(svc.publish(out.report.id))
    assert len(github.created) == 1


def test_a_retry_cannot_duplicate_even_if_the_store_forgot_the_issue_number():
    """The window the marker search exists to close: the issue was created and
    the record of it was lost."""
    github = FakeGitHub()
    svc = a_service(github)
    out = run(svc.file(category=Category.BUG, title="t", description="d"))
    run(svc.publish(out.report.id))
    # Simulate the write of the publication result having been lost.
    forgotten = run(svc.get(out.report.id)).with_(
        github_issue_number=None, github_issue_url="", status=Status.PUBLISH_FAILED
    )
    run(svc._store.append(store.REPORTS, forgotten.id, forgotten.as_record()))
    again = run(svc.publish(out.report.id))
    assert len(github.created) == 1, "the marker search must find the existing issue"
    assert again.published and again.report.github_issue_number == 100


def test_the_marker_is_unique_per_report():
    assert a_report(id="SB-R-A").marker() != a_report(id="SB-R-B").marker()
    assert "SB-R-A" in a_report(id="SB-R-A").marker()


# -- privacy: the thing that must never go wrong ------------------------------


def test_a_complaint_about_a_member_never_becomes_public():
    svc = a_service(FakeGitHub())
    out = run(
        svc.file(
            category=Category.COMPLAINT,
            title="Harassment",
            description="This user keeps insulting me in general chat.",
            reporter=Reporter(user_id=42),
        )
    )
    assert out.report.sensitivity is Sensitivity.PRIVATE
    assert not out.report.may_publish
    result = run(svc.publish(out.report.id))
    assert not result.published and result.failure == "not_publishable"


def test_a_complaint_about_the_game_also_stays_private_until_a_human_decides():
    """The category is ambiguous, so the category is the answer - not a guess
    about which kind of complaint this one is."""
    decision = privacy.classify(
        a_report(category=Category.COMPLAINT, description="The game is way too hard")
    )
    assert not decision.public


@pytest.mark.parametrize(
    "text",
    [
        "<@123456789> keeps ruining my runs",
        "he keeps sending me weird links",
        "this player is harassing me",
        "someone is bullying the new testers",
        "she won't stop DMing me",
        "my email is bob@example.com, contact me",
    ],
)
def test_anything_that_reads_as_being_about_a_person_is_private(text):
    assert not privacy.classify(a_report(description=text)).public


@pytest.mark.parametrize(
    "text",
    [
        "The game froze when I released the silk.",
        "The reel button feels too weak.",
        "I think the bird should slow down after a dive.",
        "App not available on the opt-in page.",
    ],
)
def test_an_ordinary_report_about_the_game_is_publishable(text):
    """The positive control. Without it, a classifier that refuses everything
    would pass every test above."""
    assert privacy.classify(a_report(description=text)).public


def test_the_ai_can_only_make_a_report_more_private_never_less():
    report = a_report(description="The game froze when I released the silk.")
    assert privacy.classify(report, ai_says_private=True).sensitivity is Sensitivity.PRIVATE
    assert privacy.classify(report, ai_says_private=False).public
    assert privacy.classify(report, ai_says_private=None).public
    interpersonal = a_report(description="he keeps insulting me")
    assert not privacy.classify(interpersonal, ai_says_private=False).public


def test_an_unclassified_report_cannot_be_published():
    """`UNCLASSIFIED` is the initial value, so this is what happens when nothing
    decides - and nothing deciding must not mean publishing."""
    assert not a_report(sensitivity=Sensitivity.UNCLASSIFIED).is_public_safe


def test_a_public_sensitivity_without_a_recorded_reason_is_not_enough():
    """Setting one field must not be sufficient to make a report publishable."""
    assert not a_report(
        sensitivity=Sensitivity.PUBLIC_SAFE, sensitivity_reason=""
    ).is_public_safe
    assert a_report(
        sensitivity=Sensitivity.PUBLIC_SAFE, sensitivity_reason="checked"
    ).is_public_safe


def test_the_sink_refuses_a_private_report_even_when_called_directly():
    """A publication path that trusts its caller is one refactor away from
    publishing a complaint."""
    private = a_report(sensitivity=Sensitivity.PRIVATE, sensitivity_reason="r")
    result = run(github_sink.publish(FakeGitHub(), private))
    assert isinstance(result, github_sink.PublishFailure)
    assert result.reason == "not_publishable"


# -- what reaches a public issue ---------------------------------------------


def published_body(**overrides) -> str:
    return a_report(
        sensitivity=Sensitivity.PUBLIC_SAFE, sensitivity_reason="checked", **overrides
    ).public_body()


def test_the_public_issue_carries_no_discord_identity():
    body = published_body()
    assert "42" not in body
    assert "tester" not in body
    assert "discord.com/channels" not in body


def test_the_public_issue_carries_the_intake_id_so_the_loop_can_close():
    assert "SB-R-TEST01" in published_body()


def test_the_ai_summary_is_labelled_as_ai_derived():
    body = published_body(ai_summary="Freezes on silk release.")
    assert "AI-derived" in body


def test_a_malicious_title_cannot_mention_anyone_on_github():
    report = a_report(
        title="@menno420 look at #1",
        sensitivity=Sensitivity.PUBLIC_SAFE,
        sensitivity_reason="checked",
    )
    title = report.public_title()
    assert "@menno420" not in title and "#1" not in title
    assert redact.ZERO_WIDTH in title


def test_malicious_body_text_cannot_notify_anyone_or_cross_reference():
    body = published_body(
        description="ping @everyone and @menno420, see #1 and menno420/fleet-manager#2"
    )
    for live in ("@everyone", "@menno420", "#1 ", "fleet-manager#2"):
        assert live not in body


def test_a_fence_in_member_text_cannot_swallow_the_rest_of_the_issue():
    body = published_body(description=f"{TICKS}\neverything after this would hide")
    assert TICKS not in body
    assert "Intake id" in body, "the footer must still be visible"


def test_labels_reuse_the_repos_existing_taxonomy():
    """spider-swing already has `bug`, `enhancement`, `question` and
    `type:feature`. Only the origin label is new."""
    assert a_report(category=Category.BUG).labels() == ["from-spider-bot", "bug"]
    assert "type:feature" in a_report(category=Category.IDEA).labels()
    assert "question" in a_report(category=Category.TESTING_PROBLEM).labels()
    for category in Category:
        assert a_report(category=category).labels()[0] == "from-spider-bot"


# -- persistence round-trips --------------------------------------------------


def test_a_report_round_trips_through_the_store():
    original = a_report(evidence_summary=("5,123 m on standard",), ai_tags=("freeze",))
    restored = Report.from_record(original.as_record())
    assert restored == original


def test_a_record_written_by_a_newer_bot_is_refused_not_misread():
    record = a_report().as_record()
    record["schema_version"] = 99
    assert Report.from_record(record) is None


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"id": "SB-R-1"},
        {"id": "", "category": "bug"},
        {"id": "SB-R-1", "category": "not-a-category"},
        {"id": "SB-R-1", "category": "bug", "status": "not-a-status"},
        "not a dict",
    ],
)
def test_an_unreadable_record_is_skipped_not_guessed_at(record):
    assert Report.from_record(record) is None


def test_an_unknown_extra_key_does_not_break_a_read():
    record = a_report().as_record()
    record["something_a_later_version_added"] = 1
    assert Report.from_record(record) is not None


def test_reports_come_back_newest_first():
    svc = a_service()
    first = run(svc.file(category=Category.BUG, title="a", description="d"))
    second = run(svc.file(category=Category.BUG, title="b", description="d"))
    ids_seen = [r.id for r in run(svc.all_reports())]
    assert ids_seen[0] == second.report.id and ids_seen[1] == first.report.id


# -- honest failure -----------------------------------------------------------


def test_a_failed_durable_write_is_never_reported_as_saved():
    """The one thing that must not be quietly wrong."""
    svc = intake_service.IntakeService(store.NullStore(), FakeGitHub())
    out = run(svc.file(category=Category.BUG, title="t", description="d"))
    assert not out.ok
    assert out.failure == "store_unavailable"
    assert "could not save" in out.reporter_message.lower()
    assert "saved" not in out.reporter_message.lower().replace("could not save", "")


def test_publishing_an_unknown_report_is_a_named_failure():
    assert run(a_service().publish("SB-R-nope")).failure == "unknown_report"


def test_the_retry_pass_is_bounded():
    """A long outage must not become a burst against a secondary rate limit."""
    github = FakeGitHub(fail=github_sink.PublishFailure("network", "boom"))
    svc = a_service(github)
    for index in range(15):
        run(svc.file(category=Category.BUG, title=f"t{index}", description="d"))
    github.fail = None
    assert len(run(svc.retry_pending(limit=10))) == 10
