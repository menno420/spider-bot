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

import json
import time
from asyncio import run

import pytest
from conftest import FakeInteraction, FakeUser

from spiderbot import redact, store
from spiderbot.cogs import intake as intake_cog
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



def file_and_approve(svc, **kw):
    """File a report and have a human clear it for publication.

    Publication now REQUIRES a named approver — `Report.may_publish` checks it
    — because the first design let a keyword classifier decide, and that
    classifier could not read this server's own language. Tests about
    publication therefore go through the same gate a moderator does.
    """
    kw.setdefault("category", Category.BUG)
    kw.setdefault("title", "t")
    kw.setdefault("description", "d")
    out = run(svc.file(**kw))
    run(svc.approve(out.report.id, by="menno"))
    return out


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
    out = file_and_approve(svc)
    published = run(svc.publish(out.report.id))
    assert published.published
    assert published.report.github_issue_number == 100
    assert "https://example/100" in published.reporter_message


def test_github_failing_leaves_the_report_queued_and_the_reporter_told_the_truth():
    github = FakeGitHub(fail=github_sink.PublishFailure("network", "boom", retryable=True))
    svc = a_service(github)
    out = file_and_approve(svc)
    result = run(svc.publish(out.report.id))
    assert not result.published
    assert result.report.status is Status.PUBLISH_FAILED
    assert "Saved as" in result.reporter_message and "retry" in result.reporter_message
    assert [r.id for r in run(svc.pending_publication())] == [out.report.id]


def test_a_retry_after_an_outage_succeeds():
    github = FakeGitHub(fail=github_sink.PublishFailure("network", "boom"))
    svc = a_service(github)
    out = file_and_approve(svc)
    run(svc.publish(out.report.id))
    github.fail = None                       # the outage ends
    results = run(svc.retry_pending())
    assert results[0].published
    assert run(svc.pending_publication()) == []


def test_a_retry_cannot_create_a_second_issue():
    """Five retries, one issue. The store record is the fast path."""
    github = FakeGitHub()
    svc = a_service(github)
    out = file_and_approve(svc)
    for _ in range(5):
        run(svc.publish(out.report.id))
    assert len(github.created) == 1


def test_a_retry_cannot_duplicate_even_if_the_store_forgot_the_issue_number():
    """The window the marker search exists to close: the issue was created and
    the record of it was lost."""
    github = FakeGitHub()
    svc = a_service(github)
    out = file_and_approve(svc)
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
        file_and_approve(svc, title=f"t{index}")
    github.fail = None
    assert len(run(svc.retry_pending(limit=10))) == 10


# -- defects the design pilot found by reading the committed code -------------


def test_evidence_lines_are_escaped_where_they_reach_a_github_body():
    """They arrive already rendered for ANOTHER destination. A summary escaped
    for Discord leaves `#123` live as a GitHub cross-reference."""
    body = published_body(
        evidence_summary=("Build @menno420 saw #1 fail", "Reached 5,123 m"),
        evidence_format="spider-swing-local-run-evidence@2",
    )
    assert "@menno420" not in body
    assert "#1 " not in body
    assert "5,123 m" in body, "the useful content must survive"


def test_every_field_the_body_publishes_is_scanned_by_the_classifier():
    """A member typing contact details into the DEVICE box would otherwise be
    published: the classifier never looked at that field."""
    for field_name, value in (
        ("device", "Pixel 7a, reach me at bob@example.com"),
        ("ai_summary", "The reporter says this player keeps harassing them"),
        ("build_version", "<@123456789> build"),
    ):
        report = a_report(**{field_name: value})
        assert not privacy.classify(report).public, field_name


def test_evidence_lines_are_scanned_too():
    report = a_report(evidence_summary=("Build @everyone contact me at a@b.com",))
    assert not privacy.classify(report).public


def test_a_resolved_report_can_still_be_published():
    """Marking a report resolved before the retry queue drained would otherwise
    make it permanently unpublishable: a GitHub outage plus a tidy moderator
    equals a silently dropped report."""
    github = FakeGitHub(fail=github_sink.PublishFailure("network", "down"))
    svc = a_service(github)
    out = file_and_approve(svc)
    run(svc.publish(out.report.id))
    run(svc.mark_resolved(out.report.id, "fixed in 0.46"))
    github.fail = None
    assert [r.id for r in run(svc.pending_publication())] == [out.report.id]
    assert run(svc.publish(out.report.id)).published


def test_a_report_the_reporter_did_not_clear_is_not_published():
    """An explicit form submission is consent; a conversational draft is not
    until they press confirm."""
    assert not a_report(
        sensitivity=Sensitivity.PUBLIC_SAFE,
        sensitivity_reason="checked",
        reporter_cleared=False,
        status=Status.STORED,
        approved_by="menno",
    ).may_publish
    assert a_report(
        sensitivity=Sensitivity.PUBLIC_SAFE,
        sensitivity_reason="checked",
        reporter_cleared=True,
        status=Status.STORED,
        approved_by="menno",
    ).may_publish


def test_consent_survives_the_store_round_trip():
    original = a_report(reporter_cleared=False)
    assert Report.from_record(original.as_record()).reporter_cleared is False


class RejectsLabels(FakeGitHub):
    """A GitHub that 422s while labels are present, and accepts without them."""

    async def create_issue(self, title, body, labels):
        if labels:
            return github_sink.PublishFailure("rejected", "label does not exist", retryable=False)
        return await super().create_issue(title, body, [])


def test_a_label_that_does_not_exist_does_not_lose_the_issue():
    """`from-spider-bot` is absent from spider-swing (verified live 2026-09-04)
    and GitHub's docs do not say what happens to an unknown label name."""
    github = RejectsLabels()
    svc = a_service(github)
    out = file_and_approve(svc)
    result = run(svc.publish(out.report.id))
    assert result.published
    assert github.created[0][2] == []


def test_a_rejection_that_is_not_about_labels_still_fails():
    """The positive control: the bare retry must not swallow every 422."""
    github = FakeGitHub(fail=github_sink.PublishFailure("rejected", "title too long"))
    svc = a_service(github)
    out = file_and_approve(svc)
    assert not run(svc.publish(out.report.id)).published


# -- conversational filing ----------------------------------------------------


def test_natural_language_is_detected_and_categorised():
    from spiderbot.cogs.intake import detect

    cases = [
        ("The game froze when I released the silk near 3km", Category.BUG),
        ("i cant install it, it says app not available", Category.TESTING_PROBLEM),
        ("it would be nice if the bird slowed down after a dive", Category.IDEA),
        ("the reel button feels too weak on the newest build", Category.GAMEPLAY_FEEDBACK),
    ]
    for text, expected in cases:
        assert detect(text) is expected, text


@pytest.mark.parametrize(
    "text",
    [
        "",
        "lol",
        "gg",
        "hey everyone how is it going today, nice weather",
        "/home",
        "thanks menno that was quick",
    ],
)
def test_ordinary_chat_produces_no_offer(text):
    """A false offer is cheap; nagging is not."""
    from spiderbot.cogs.intake import detect

    assert detect(text) is None


def test_a_summary_shows_the_persons_own_words_not_a_paraphrase():
    """They have to be able to recognise and correct what will be saved."""
    from spiderbot.cogs.intake import summarise

    title, description = summarise(
        "The game froze when I released the silk. It happened twice near 3km."
    )
    assert title == "The game froze when I released the silk."
    assert "twice near 3km" in description


def test_a_very_long_first_sentence_is_trimmed_not_dropped():
    from spiderbot.cogs.intake import summarise

    title, _ = summarise("x" * 300)
    assert len(title) <= 90 and title.endswith("...")


# -- the publication gate, and why a classifier is not one --------------------
#
# Every case below reproduced against the FIRST design, which published
# anything the keyword vocabulary did not object to. They are kept as tests
# rather than as a note because the failure mode is silent and the fix is one
# `and` clause away from being deleted by someone who finds it inconvenient.

ZERO_WIDTH = "​"


@pytest.mark.parametrize(
    ("label", "description"),
    [
        (
            "plain English naming a member",
            "The panel freezes whenever Marcus Klein is in the lobby. Marcus calls "
            "me names every night and tells everyone I cheat.",
        ),
        (
            "Dutch - the server's own language",
            "Het spel loopt vast als Marcus Klein in de lobby zit. Marcus scheldt "
            "mij elke avond uit en pest de jongere testers.",
        ),
        (
            "leetspeak",
            "It freezes on level 3. That us3r Marcus k33ps h4rassing me, pls b4n h1m.",
        ),
        (
            "contact details written as words",
            "It freezes. You can reach Marcus at bob dot smith at gmail dot com.",
        ),
    ],
)
def test_a_report_the_classifier_misses_still_cannot_publish_itself(label, description):
    """The classifier CANNOT catch these — an English keyword vocabulary never
    will, and this server speaks Dutch. So publication does not depend on it."""
    svc = a_service(FakeGitHub())
    out = run(
        svc.file(category=Category.BUG, title="Freeze", description=description,
                 reporter=Reporter(user_id=1))
    )
    assert not out.report.may_publish, label
    assert not run(svc.publish(out.report.id)).published, label


def test_a_zero_width_space_cannot_split_the_scanned_text_from_the_published_text():
    """`redact.clean` strips zero-width characters on the way OUT, so scanning
    the raw field and publishing the cleaned one meant a member writing
    `har<ZWSP>assing` was scanned as one string and published as another with
    the word restored."""
    hostile = (
        f"The lobby freezes. Marcus keeps har{ZERO_WIDTH}assing the younger "
        f"testers; reach him at marcus.klein{ZERO_WIDTH}@gmail.com"
    )
    decision = privacy.classify(a_report(description=hostile))
    assert not decision.public
    assert decision.signals


def test_the_scanned_set_and_the_published_set_are_the_same_list():
    """They drifted once: `evidence_format` was printed into the issue body and
    never classified. One list now, and this asserts they cannot part again."""
    import inspect

    from spiderbot.intake.models import PUBLISHED_FIELDS

    # Both halves of the public output: the title is rendered by public_title,
    # the rest by public_body, and a field in neither would be dead weight in
    # the scanned set — which is its own (smaller) kind of drift.
    source = inspect.getsource(Report.public_body) + inspect.getsource(Report.public_title)
    for name in PUBLISHED_FIELDS:
        assert f"self.{name}" in source, f"{name} is scanned but never published"
    published_text = a_report(
        title="A", description="B", repro_steps="C", device="D",
        build_version="E", ai_summary="F", evidence_format="G",
        ai_tags=("H",), evidence_summary=("I",),
    ).published_text()
    for value in "ABCDEFGHI":
        assert value in published_text, f"{value} reaches the issue but is not scanned"


def test_publication_needs_a_named_human():
    svc = a_service(FakeGitHub())
    out = run(svc.file(category=Category.BUG, title="Freeze",
                       description="It froze when I released the silk."))
    assert out.report.sensitivity is Sensitivity.PUBLIC_SAFE
    assert not out.report.may_publish, "public-safe is not the same as cleared"
    approved = run(svc.approve(out.report.id, by="menno"))
    assert approved.approved_by == "menno"
    assert approved.may_publish
    assert run(svc.publish(out.report.id)).published


def test_a_private_report_cannot_be_approved_into_publication():
    """Approving is not a way around the classifier's private verdict — it is a
    way around its inability to be sure something is safe."""
    svc = a_service(FakeGitHub())
    out = run(svc.file(category=Category.COMPLAINT, title="x",
                       description="This user keeps insulting me."))
    unchanged = run(svc.approve(out.report.id, by="menno"))
    assert unchanged.approved_by == ""
    assert not unchanged.may_publish


def test_the_owner_queue_shows_what_is_waiting_for_a_decision():
    svc = a_service(FakeGitHub())
    a = run(svc.file(category=Category.BUG, title="a", description="It froze."))
    run(svc.file(category=Category.COMPLAINT, title="b", description="He insulted me."))
    waiting = run(svc.awaiting_approval())
    assert [r.id for r in waiting] == [a.report.id], "only public-safe, only unapproved"
    run(svc.approve(a.report.id, by="menno"))
    assert run(svc.awaiting_approval()) == []


def test_the_receipt_no_longer_promises_a_github_issue():
    """It used to, and the promise was made by a classifier that could not read
    the language the member was writing in."""
    svc = a_service(FakeGitHub())
    out = run(svc.file(category=Category.BUG, title="t", description="It froze."))
    assert "will file it" not in out.reporter_message
    assert "Menno" in out.reporter_message


def test_approval_survives_the_store_round_trip():
    assert Report.from_record(a_report(approved_by="menno").as_record()).approved_by == "menno"


# -- what an adversarial review executed against the committed code -----------


def test_a_member_cannot_steal_another_report_by_writing_its_id_in_their_own():
    """`MEASURED` 2026-09-04, the whole chain reproduced.

    The intake marker is the only backstop against republication, and it is a
    plain string in a field a member types. A member handed report A's id in
    their receipt wrote it into report B's description; B published first, and
    A then resolved to B's issue number without an issue ever being created —
    A's text never reached the tracker while both panels said "filed".
    """
    github = FakeGitHub()
    svc = a_service(github)
    victim = file_and_approve(svc, title="Swing physics break above 3 km",
                              description="the reel snaps at altitude")
    thief = file_and_approve(
        svc, title="also broken",
        description=f"see also {victim.report.id} which is the same thing",
    )
    run(svc.publish(thief.report.id))
    run(svc.publish(victim.report.id))

    assert len(github.created) == 2, "the victim's report must reach the tracker"
    numbers = {
        run(svc.get(victim.report.id)).github_issue_number,
        run(svc.get(thief.report.id)).github_issue_number,
    }
    assert len(numbers) == 2, "two reports, two issues"
    victim_body = next(b for _t, b, _l in github.created if "reel snaps" in b)
    assert victim.report.marker() in victim_body


def test_the_marker_break_is_invisible_to_a_reader():
    """Positive control: the id is still readable in the published body, so the
    defence costs the developer nothing."""
    broken = redact.for_github("see also SB-R-M1PB8V6G-KT0GBA which is the same")
    assert "SB-R-M1PB8V6G-KT0GBA" not in broken
    assert broken.replace(redact.ZERO_WIDTH, "").endswith(
        "see also SB-R-M1PB8V6G-KT0GBA which is the same"
    )


def test_a_permanent_failure_leaves_the_retry_queue():
    """`retryable` was computed by the sink, documented as "what stops a retry
    loop hammering a 404 forever", and read by nothing."""
    github = FakeGitHub(fail=github_sink.PublishFailure("not_found", "404", retryable=False))
    svc = a_service(github)
    out = file_and_approve(svc)
    run(svc.publish(out.report.id))

    assert run(svc.pending_publication()) == []
    stuck = run(svc.stuck())
    assert [r.id for r in stuck] == [out.report.id]
    assert stuck[0].publish_failure == "not_found"
    assert run(svc.retry_pending()) == []


def test_a_retryable_failure_stays_in_the_queue():
    """Positive control for the test above: the exclusion must be about the
    classification, not about failure."""
    github = FakeGitHub(fail=github_sink.PublishFailure("network", "boom", retryable=True))
    svc = a_service(github)
    out = file_and_approve(svc)
    run(svc.publish(out.report.id))

    assert [r.id for r in run(svc.pending_publication())] == [out.report.id]
    assert run(svc.stuck()) == []


def test_a_report_published_but_unrecorded_is_not_published_twice():
    """`MEASURED` 2026-09-04: seven presses of the retry command produced seven
    separate public issues for one report, because a refused store write left
    the record publishable and the marker search was unavailable too."""
    github = FakeGitHub()
    svc = a_service(github)
    out = file_and_approve(svc)

    real_append = svc._store.append

    async def refuse_the_published_write(collection, key, data):
        if data.get("status") == "published":
            return False
        return await real_append(collection, key, data)

    svc._store.append = refuse_the_published_write
    run(svc.publish(out.report.id))
    assert len(github.created) == 1

    # The marker search is down too — the exact case the in-process memory is for.
    async def search_is_down(marker):
        return None

    github.find_issue_by_marker = search_is_down
    for _ in range(7):
        run(svc.publish(out.report.id))
    assert len(github.created) == 1, "one report, one issue"


def test_one_member_cannot_flood_the_store():
    """No rate limit existed anywhere on filing, and the store is an
    append-only channel read to a fixed horizon: enough writes push older
    records out of every panel, which reads as reports being deleted."""
    svc = a_service()
    reporter = Reporter(user_id=42)
    outcomes = [
        run(svc.file(category=Category.BUG, title="t", description="d", reporter=reporter))
        for _ in range(intake_service.FILE_LIMIT + 3)
    ]
    assert sum(1 for o in outcomes if o.ok) == intake_service.FILE_LIMIT
    refused = [o for o in outcomes if not o.ok]
    assert all(o.failure == "rate_limited" for o in refused)
    assert "stopped" in refused[0].reporter_message

    # Positive control: a different member is unaffected — the limit is
    # per-reporter, not a global tap someone else can close.
    other = run(
        svc.file(category=Category.BUG, title="t", description="d",
                 reporter=Reporter(user_id=43))
    )
    assert other.ok


# -- the two buttons on a PUBLIC offer panel ---------------------------------


class _Bot:
    """Just enough bot for a DynamicItem callback: it reads `bot.intake`."""

    def __init__(self, service) -> None:
        self.intake = service


def _offer(svc, user_id=42, draft_id="SB-R-DRAFT01"):
    run(
        svc._store.append(
            intake_cog.DRAFTS,
            draft_id,
            {
                "user_id": user_id,
                "category": "bug",
                "title": "the reel snaps at altitude",
                "description": "it happened twice above 3 km",
                "channel_id": 7,
                "message_id": 8,
                "correlation_id": "SB-C-TEST",
            },
        )
    )
    return draft_id


def test_a_stranger_cannot_dismiss_someone_elses_report_offer():
    """`MEASURED` 2026-09-04: DismissFiling had no ownership check at all, and
    the offer is posted in a PUBLIC channel. Any member could press "No thanks"
    on somebody else's crash report; the panel was edited away for everyone and
    nothing was recorded."""
    svc = a_service()
    draft_id = _offer(svc, user_id=42)
    interaction = FakeInteraction(guild=None, user=FakeUser(99, "stranger"))
    interaction.client = _Bot(svc)

    run(intake_cog.DismissFiling(draft_id).callback(interaction))
    assert interaction.response.edits == [], "the offer must still be there"
    assert "someone else" in (interaction.response.messages[0][0] or "")

    # Positive control: the reporter themselves CAN dismiss it.
    mine = FakeInteraction(guild=None, user=FakeUser(42, "reporter"))
    mine.client = _Bot(svc)
    run(intake_cog.DismissFiling(draft_id).callback(mine))
    assert mine.response.edits and mine.response.edits[0]["content"] == "No problem."


def test_a_double_press_on_save_files_one_report_not_two():
    """The item is rebuilt from its custom_id on every press and the draft was
    never consumed, so any second click that reached the gateway before the
    edit landed filed the same report again."""
    svc = a_service()
    draft_id = _offer(svc, user_id=42)
    button = intake_cog.ConfirmFiling(draft_id)

    first = FakeInteraction(guild=None, user=FakeUser(42, "reporter"))
    first.client = _Bot(svc)
    run(button.callback(first))
    second = FakeInteraction(guild=None, user=FakeUser(42, "reporter"))
    second.client = _Bot(svc)
    run(button.callback(second))

    assert len(run(svc.all_reports())) == 1
    assert "Already saved" in (second.response.messages[0][0] or "")


# -- the real HTTP client's own robustness -----------------------------------


def _client_with(status, text):
    """An HttpGitHubClient whose one transport call is replaced. Nothing here
    reaches the network; the point is what the client does with a response."""
    client = github_sink.HttpGitHubClient(token="t", repo="o/r", session=object())

    async def request(method, url, payload=None):
        return status, text

    client._request = request
    return client


def test_a_marker_hit_is_not_believed_when_the_body_does_not_carry_it():
    """GitHub's search tokenises, so a quoted phrase is a hint. The marker sits
    in a field a member types, and an unverified hit is a way to make one
    report resolve to somebody else's issue."""
    body = 'no marker here'
    client = _client_with(200, json.dumps({"items": [{"number": 7, "body": body}]}))
    assert run(client.find_issue_by_marker("Intake id `SB-R-AAAA`")) is None

    # Positive control: a hit whose body DOES carry it is believed.
    good = json.dumps(
        {"items": [{"number": 7, "body": "text\nIntake id `SB-R-AAAA`", "html_url": "u"}]}
    )
    found = run(_client_with(200, good).find_issue_by_marker("Intake id `SB-R-AAAA`"))
    assert found is not None and found.number == 7


@pytest.mark.parametrize(
    "text",
    ['[1, 2, 3]', '"a string"', 'null', '{"items": [{"number": "not-a-number"}]}'],
)
def test_an_unexpected_two_hundred_body_does_not_raise_out_of_the_client(text):
    """The class's docstring promises "never raises: every failure is a
    PublishFailure". `json.loads(text).get(...)` raises AttributeError on a
    JSON array, which an interposing proxy or CDN error page can return."""
    assert run(_client_with(200, text).find_issue_by_marker("m")) is None


# -- the amplifier a member can drive ----------------------------------------


def test_a_channel_the_bot_cannot_post_in_does_not_become_a_write_amplifier():
    """`MEASURED` 2026-09-04: the offer cooldown was armed AFTER a successful
    reply, so a channel where the bot cannot post never armed it — and every
    message from one member wrote another draft into the shared store channel.
    2000 messages, 2000 writes, zero offers; and the store is read to a fixed
    horizon on a cold start, so those writes push real reports out of every
    panel. The cooldown protects the store, so it cannot depend on the delivery
    that can fail."""
    import discord
    from conftest import FakeAI, FakeBot, FakeChannel, FakeMessage, make_cfg

    bot = FakeBot(make_cfg(initiative_channels=("general",)), FakeAI())
    bot.intake = a_service()
    cog = intake_cog.IntakeCog(bot)
    channel = FakeChannel(name="general")
    author = FakeUser(42, "tester")

    text = "the game froze when I released the silk near 3 km and it crashed"
    assert intake_cog.detect(text) is not None, "the message must trip the detector"

    async def refuse(*_a, **_kw):
        raise discord.HTTPException(_FakeResponseObj(), "no permission")

    for n in range(50):
        message = FakeMessage(text, author, channel, guild=object())
        message.id = 1000 + n
        message.reply = refuse
        run(cog.on_message(message))

    drafts = run(bot.intake._store.load(intake_cog.DRAFTS))
    assert len(drafts) == 1, f"one draft per cooldown window, got {len(drafts)}"


class _FakeResponseObj:
    status = 403
    reason = "Forbidden"
