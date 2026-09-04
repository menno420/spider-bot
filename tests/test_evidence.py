"""spiderbot/evidence.py - a Slingy Spider run export is untrusted user data.

The happy path is one test. The rest of this file is the adversarial surface,
because that is where the risk is: this parser reads a blob a stranger pasted
into a public Discord, and everything it extracts ends up in an embed a
moderator reads or a GitHub issue anyone can see.

The contract these assertions encode came from spider-swing's own source at
`fc64a3fb` (`game/domain/run_record_ledger.gd`, `game/domain/run_record.gd`),
not from a description of it. When that repo bumps its export format, the
`unsupported_format` test is what fails first, on purpose - an honest refusal
naming both versions beats a best-effort parse of a shape we do not know.
"""

from __future__ import annotations

import json

import pytest

from spiderbot import evidence, redact, support

TICKS = "`" * 3

GOOD_RECORD = {
    "schema_version": 2,
    "record_id": "r1",
    "settlement_id": "s1",
    "build_version": "0.45.0-run-feedback",
    "android_version_code": 66,
    "runtime_platform": "Android",
    "difficulty_id": "standard",
    "terminal_outcome": "death",
    "death_cause": "camera_boundary",
    "final_region_id": "ancient_forest",
    "final_distance_pixels": 51234.0,
    "travelled_distance_pixels": 51234.0,
    "active_duration_seconds": 88.5,
    "mean_forward_speed_pixels_per_second": 580.0,
    "maximum_forward_speed_pixels_per_second": 940.0,
    "above_reference_speed_share": 0.42,
    "successful_web_attachments": 61,
    "reel_activations": 18,
    "burst_activations": 3,
    "dive_activations": 1,
    "flies_collected": 12,
    "configuration_kind": "standard",
    "input_source": "human",
}


def export(records=None, **ledger_overrides) -> str:
    ledger = {
        "schema_version": 2,
        "history_limit": 100,
        "records": [GOOD_RECORD] if records is None else records,
        "feedback_responses": [],
        "total_completed_recorded_runs": 1,
        "total_active_duration_seconds": 123.4,
        "total_distance_travelled_pixels": 51234.0,
        "best_distance_pixels_by_difficulty": {"standard": 50000.0},
    }
    ledger.update(ledger_overrides)
    return json.dumps(
        {
            "format": evidence.SUPPORTED_FORMAT,
            "local_only": True,
            "transmission": "none",
            "ledger": ledger,
        }
    )


# -- the thing it is for -----------------------------------------------------


def test_a_real_export_becomes_numbers_a_developer_can_act_on():
    result = evidence.parse(export())
    assert result.ok
    assert result.record_count == 1
    assert result.latest.record_id == "r1"
    # 51234 px / 10 px per metre = 5,123 m - the conversion that makes a
    # tester's "around 5 km" mean the same thing on both sides.
    assert round(result.latest.distance_metres) == 5123
    assert "5,123 m" in result.latest.headline(redact.for_discord)


def test_the_pixels_to_metres_constant_matches_the_game():
    """`course_region_catalog.gd:9`. If spider-swing changes it, this fails."""
    assert evidence.PIXELS_PER_METRE == 10.0


def test_a_replay_or_debug_run_is_not_presented_as_a_person_struggling():
    replay = dict(GOOD_RECORD, configuration_kind="trace_replay", input_source="trace_replay")
    result = evidence.parse(export([replay]))
    assert result.ok
    assert result.latest.is_ordinary_human_run is False
    assert "not an ordinary human run" in " ".join(
        result.latest.detail_lines(redact.for_discord)
    )


def test_the_cheap_precheck_recognises_an_export_without_parsing_it():
    assert evidence.looks_like_evidence(export())
    assert not evidence.looks_like_evidence("just a normal message about the bird")


# -- refusals, each named -----------------------------------------------------


@pytest.mark.parametrize(
    ("blob", "reason"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("{nope", "not_json"),
        ("[1, 2, 3]", "not_an_object"),
        ('{"format": "spider-swing-local-run-evidence@2"}', "no_ledger"),
        ('{"format": "other", "ledger": {}}', "unsupported_format"),
        ('{"format": 5, "ledger": {}}', "unsupported_format"),
    ],
)
def test_malformed_input_is_refused_by_name(blob, reason):
    result = evidence.parse(blob)
    assert not result.ok
    assert result.reason == reason


def test_an_unsupported_export_format_names_both_versions():
    """A tester on a newer build must be told what happened, not shown silence."""
    blob = export().replace(evidence.SUPPORTED_FORMAT, "spider-swing-local-run-evidence@3")
    result = evidence.parse(blob)
    assert result.reason == "unsupported_format"
    assert "@3" in result.detail and evidence.SUPPORTED_FORMAT in result.detail


def test_an_unsupported_ledger_schema_is_refused():
    result = evidence.parse(export(schema_version=99))
    assert result.reason == "unsupported_ledger_schema"


def test_a_schema_version_that_is_not_an_integer_is_refused():
    assert evidence.parse(export(schema_version="2")).reason == "bad_schema_version"
    assert evidence.parse(export(schema_version=True)).reason == "bad_schema_version"


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_pythons_non_standard_number_tokens_are_refused(token):
    """`json.loads` accepts these by default. A NaN in an embed renders as
    `nan`, and a NaN in a comparison is false against everything."""
    blob = (
        f'{{"format": "{evidence.SUPPORTED_FORMAT}", "ledger":'
        f' {{"schema_version": 2, "records": [],'
        f' "total_active_duration_seconds": {token}}}}}'
    )
    assert evidence.parse(blob).reason == "non_finite_number"


def test_a_duplicate_key_is_refused_rather_than_silently_last_wins():
    """`{"a": 1, "a": 2}` parses as 2 while a human reading the paste sees 1."""
    blob = (
        f'{{"format": "{evidence.SUPPORTED_FORMAT}", "format": "evil",'
        f' "ledger": {{"schema_version": 2, "records": []}}}}'
    )
    assert evidence.parse(blob).reason == "duplicate_key"


def test_more_records_than_the_game_can_produce_is_refused():
    """The producer caps its ledger at 100 (`run_record_ledger.gd`)."""
    result = evidence.parse(export([GOOD_RECORD] * (evidence.MAX_RECORDS + 1)))
    assert result.reason == "too_many_records"


def test_an_oversized_paste_costs_a_length_check_not_a_parse():
    result = evidence.parse("x" * (evidence.MAX_BYTES + 1))
    assert result.reason == "too_large"


def test_oversized_bytes_are_refused_before_decoding():
    result = evidence.parse(b"x" * (evidence.MAX_BYTES + 1))
    assert result.reason == "too_large"


def test_bytes_that_are_not_utf8_are_refused():
    assert evidence.parse(b"\xff\xfe\x00bad").reason == "not_utf8"


def test_deep_nesting_cannot_exhaust_the_stack():
    blob = '{"a":' * 3000 + "1" + "}" * 3000
    result = evidence.parse(blob)
    assert not result.ok  # refused, and the process is still alive to assert it


def test_a_record_without_an_id_is_skipped_not_fatal():
    """The producer rejects these too; one bad row must not lose the rest."""
    result = evidence.parse(export([{"schema_version": 2}, GOOD_RECORD]))
    assert result.ok
    assert result.record_count == 1
    assert result.skipped_records == 1
    assert "1 of 2 records were unreadable" in " ".join(
        result.summary_lines(redact.for_discord)
    )


def test_a_record_that_is_not_an_object_is_skipped():
    result = evidence.parse(export(["not a record", 5, None, GOOD_RECORD]))
    assert result.ok and result.record_count == 1 and result.skipped_records == 3


# -- hostile content ----------------------------------------------------------


def test_a_finite_but_absurd_number_is_clamped_and_the_file_is_marked_edited():
    """1e308 is finite, passes every NaN check, and renders as 309 digits."""
    record = dict(GOOD_RECORD, final_distance_pixels=1e308)
    result = evidence.parse(export([record]))
    assert result.ok
    assert result.latest.implausible
    assert "final_distance_pixels" in result.latest.clamped
    assert result.latest.distance_pixels == evidence.MAX_PIXELS
    text = " ".join(result.latest.detail_lines(redact.for_discord))
    assert "has been edited" in text


def test_a_negative_count_is_clamped_and_reported():
    result = evidence.parse(export([dict(GOOD_RECORD, flies_collected=-5)]))
    assert result.latest.flies == 0
    assert "flies_collected" in result.latest.clamped


def test_a_share_above_one_is_clamped_and_reported():
    result = evidence.parse(export([dict(GOOD_RECORD, above_reference_speed_share=99.0)]))
    assert result.latest.above_reference_share == 1.0
    assert "above_reference_speed_share" in result.latest.clamped


def test_a_plausible_file_is_not_marked_edited():
    """The positive control: the clamp must not fire on a real export."""
    result = evidence.parse(export())
    assert result.latest.clamped == ()
    assert not result.latest.implausible
    assert "has been edited" not in " ".join(
        result.latest.detail_lines(redact.for_discord)
    )


def test_mentions_in_an_uploaded_file_cannot_notify_anyone_on_github():
    record = dict(GOOD_RECORD, death_cause="@menno420 #1", build_version="@everyone")
    result = evidence.parse(export([record]))
    text = " ".join(result.summary_lines(redact.for_github))
    assert "@menno420" not in text
    assert "@everyone" not in text
    assert "#1 " not in text
    assert redact.ZERO_WIDTH in text


def test_markdown_in_an_uploaded_file_cannot_restructure_an_embed():
    record = dict(GOOD_RECORD, build_version="# HEADING **bold**")
    result = evidence.parse(export([record]))
    text = " ".join(result.summary_lines(redact.for_discord))
    assert "\\#" in text and "\\*\\*" in text


def test_a_fence_in_an_uploaded_file_cannot_swallow_a_github_issue_body():
    record = dict(GOOD_RECORD, build_version=f"{TICKS}js hidden")
    result = evidence.parse(export([record]))
    assert TICKS not in " ".join(result.summary_lines(redact.for_github))


def test_control_characters_and_direction_overrides_are_stripped():
    record = dict(GOOD_RECORD, death_cause="a\x00b‮c")
    result = evidence.parse(export([record]))
    assert result.latest.death_cause == "abc"


def test_a_value_outside_the_games_catalogue_is_reported_not_shown_as_understood():
    record = dict(GOOD_RECORD, difficulty_id="impossible", terminal_outcome="ascended")
    result = evidence.parse(export([record]))
    assert "difficulty_id=impossible" in result.latest.unrecognised
    assert "terminal_outcome=ascended" in result.latest.unrecognised


def test_a_free_text_death_cause_is_not_treated_as_a_catalogue():
    """spider-swing's own doc: the record stores the raw identifier and does
    not group or relabel it, so a new cause must not read as unrecognised."""
    record = dict(GOOD_RECORD, death_cause="some_new_hazard_the_game_added")
    result = evidence.parse(export([record]))
    assert result.latest.unrecognised == ()


def test_a_bogus_difficulty_key_in_the_lifetime_bests_is_dropped():
    result = evidence.parse(
        export(best_distance_pixels_by_difficulty={"standard": 1.0, "bogus": 2.0})
    )
    assert set(result.lifetime.best_by_difficulty) == {"standard"}


def test_forgetting_the_escaper_is_a_type_error_not_a_leak():
    """The unsafe call is impossible rather than documented."""
    result = evidence.parse(export())
    with pytest.raises(TypeError):
        result.summary_lines()
    with pytest.raises(TypeError):
        result.latest.headline()


def test_nothing_in_a_hostile_file_makes_the_parser_raise():
    """A paste must never take a Discord callback down."""
    for blob in (
        None,
        123,
        {"not": "a string"},
        export(records={"not": "a list"}),
        export(feedback_responses="nope"),
        export(best_distance_pixels_by_difficulty="nope"),
        export([dict(GOOD_RECORD, resolved_upgrade_levels={"a": {"b": {"c": 1}}})]),
    ):
        assert evidence.parse(blob) is not None


def test_the_provenance_line_is_never_malformed():
    """It is user-visible and it goes into the model's system prompt. The first
    version string-patched a template and closed a bracket it had not opened."""
    from spiderbot import support

    for facts in (
        support.SupportFacts(source=support.Source.FEED),
        support.SupportFacts(source=support.Source.FEED, source_sha="abcdef123456"),
        support.SupportFacts(source=support.Source.FEED, generated_at="2026-09-04T12:00:00Z"),
        support.SupportFacts(
            source=support.Source.FEED, generated_at="2026-09-04T12:00:00Z", source_sha="abc123"
        ),
        support.SupportFacts(source=support.Source.CACHED, problem="HTTP 500"),
        support.SupportFacts(source=support.Source.BUILT_IN, problem="no feed"),
    ):
        line = facts.staleness()
        assert line.count("(") == line.count(")"), line
        assert "((" not in line and " )" not in line, line
        assert line and line[0].isupper()


def test_evidence_lines_do_not_double_their_bullet_in_an_issue_body():
    """The summary is rendered for Discord with a middot prefix; a markdown list
    already has a bullet, and "- · Build ..." reads as a mistake."""
    from spiderbot.intake.models import Category, Report, Sensitivity

    body = Report(
        id="SB-R-1",
        category=Category.BUG,
        title="t",
        description="d",
        submitted_at=0.0,
        sensitivity=Sensitivity.PUBLIC_SAFE,
        sensitivity_reason="checked",
        evidence_summary=("**Latest run** - 5,123 m", "· Build 0.45.0"),
    ).public_body()
    assert "- · Build" not in body
    assert "- Build 0.45.0" in body


# -- what an adversarial review executed against the committed code -----------


def test_the_lifetime_ledger_is_bounded_and_says_when_it_was_edited():
    """`MEASURED` 2026-09-04: every per-record field was capped and marked
    while the ledger AGGREGATES had no bounds at all, so an edited export
    showing 4.6 billion km travelled rendered as a plain fact under a summary
    whose whole contract is that an implausible number is clamped AND marked."""
    result = evidence.parse(
        export(
            total_completed_recorded_runs=10**9,
            total_active_duration_seconds=1e300,
            total_distance_travelled_pixels=1e300,
            best_distance_pixels_by_difficulty={"harsh": 1e300},
        )
    )
    assert result.ok
    assert set(result.lifetime.clamped) == {
        "total_completed_recorded_runs",
        "total_active_duration_seconds",
        "total_distance_travelled_pixels",
        "best_distance_pixels[harsh]",
    }
    rendered = " ".join(result.lifetime.lines(redact.for_github))
    assert "This file has been edited" in rendered

    # Positive control: an ordinary export marks nothing, so the banner means
    # something when it appears.
    ordinary = evidence.parse(export())
    assert ordinary.lifetime.clamped == ()
    assert "edited" not in " ".join(ordinary.lifetime.lines(redact.for_github))


def test_an_unrepresentable_number_is_marked_rather_than_shown_as_zero():
    """A JSON integer above ~1.8e308 is finite, so `parse_constant` never sees
    it and `float()` raises OverflowError. It used to abort the WHOLE parse as
    "internal_error"; skipping to a silent 0.0 would be worse still, because
    "0 m" reads as a measurement."""
    bad = dict(GOOD_RECORD, final_distance_pixels=10**400)
    result = evidence.parse(export([GOOD_RECORD, bad]))
    assert result.ok and result.reason == ""
    assert result.record_count == 2
    assert result.latest.clamped == ("final_distance_pixels",)
    assert "This file has been edited" in " ".join(
        result.latest.detail_lines(redact.for_github)
    )


def test_a_value_outside_the_catalogue_is_shown_as_unrecognised():
    """`unrecognised` was computed on every record and rendered nowhere — so a
    value the consumer does not understand was presented as though it did,
    which is precisely what the module's own docstring says it must not do."""
    odd = dict(GOOD_RECORD, difficulty_id="nightmare", terminal_outcome="ascended")
    result = evidence.parse(export([odd]))
    assert result.latest.unrecognised == (
        "difficulty_id=nightmare",
        "terminal_outcome=ascended",
    )
    said = [
        line
        for line in result.latest.detail_lines(redact.for_github)
        if "does not recognise" in line
    ]
    assert said and "nightmare" in said[0]

    # Positive control: a catalogued export says nothing.
    assert not [
        line
        for line in evidence.parse(export()).latest.detail_lines(redact.for_github)
        if "does not recognise" in line
    ]


# -- the support feed, same review -------------------------------------------


def a_feed(**overrides) -> str:
    body = {"format": support.FEED_FORMAT, "schema_version": support.SUPPORTED_SCHEMA}
    body.update(overrides)
    return json.dumps(body)


def test_the_feeds_pairs_are_read_by_name_not_by_key_order():
    """`MEASURED` 2026-09-04: `_pairs` did `keys = list(item)` and took the
    first two by insertion order. Today's producer happens to emit `label`
    before `url`, so it rendered correctly — but the contract was silently
    "emit your keys in this order", which JSON does not promise. The natural
    shape would have rendered `- https://…: Official page` under a heading
    that tells the model these are the official links."""
    facts = support.parse(
        a_feed(
            links=[{"url": "https://play.google.com/x", "label": "Opt-in page"}],
            troubleshooting=[{"fix": "check the account", "symptom": "App not available"}],
        )
    )
    assert facts.links == (("Opt-in page", "https://play.google.com/x"),)
    assert facts.troubleshooting == (("App not available", "check the account"),)


def test_an_official_link_must_be_https_and_on_a_known_host():
    """The links block is written into the CHAT SYSTEM PROMPT under "Official
    links (never invent others)". Anything the model is told is official should
    be checkable without trusting the transport or a future producer edit."""
    facts = support.parse(
        a_feed(
            links=[
                {"label": "Official tester link", "url": "https://evil.example/apk"},
                {"label": "js", "url": "javascript:alert(1)"},
                {"label": "plain", "url": "http://play.google.com/x"},
                {"label": "Opt-in page", "url": "https://play.google.com/apps/testing/x"},
            ]
        )
    )
    assert facts.links == (("Opt-in page", "https://play.google.com/apps/testing/x"),)
    assert "evil.example" not in facts.as_prompt_block()


def test_a_boolean_schema_version_is_refused():
    """`True == 1` in Python, so a feed claiming `"schema_version": true` would
    otherwise pin as version 1 and be read as if understood."""
    facts = support.parse(a_feed(schema_version=True))
    assert not facts.live
    assert "needs updating" in facts.problem


def test_the_refresh_interval_is_not_dead_configuration():
    """`refresh()` had a correct lazy guard and exactly one caller, `on_ready`.
    Nothing called it again, so `SUPPORT_FEED_REFRESH_SECONDS` governed nothing
    and a worker up for a week served the build version it booted with."""
    from conftest import make_cfg

    clock = [1000.0]
    cfg = make_cfg()
    object.__setattr__(cfg, "support_feed_url", "https://example/feed.json")
    object.__setattr__(cfg, "support_feed_refresh_s", 3600)
    feed = support.SupportFeed(cfg, now=lambda: clock[0])

    assert feed.due, "nothing fetched yet"
    feed._facts = support.SupportFacts(source=support.Source.FEED)
    feed._fetched_at = clock[0]
    assert not feed.due, "just fetched"
    clock[0] += 3600
    assert feed.due, "the interval the owner set is the one that governs"

    # Positive control: with no URL configured there is nothing to be due for.
    object.__setattr__(cfg, "support_feed_url", "")
    assert not support.SupportFeed(cfg, now=lambda: clock[0]).due


def test_cached_support_facts_survive_a_continuing_outage():
    """Codex, spider-bot#3, 2026-09-04: the branch tested `live`, and a CACHED
    fact is not live — so the first failure demoted the real feed to cached and
    the SECOND replaced it with the empty built-in block. A continuing outage
    kept useful facts for exactly one retry."""
    import asyncio

    from conftest import make_cfg

    cfg = make_cfg()
    object.__setattr__(cfg, "support_feed_url", "https://example/feed.json")
    feed = support.SupportFeed(cfg)
    feed._facts = support.SupportFacts(source=support.Source.FEED, build_version="0.45.0")

    async def dead():
        return None, "HTTP 500"

    feed._fetch = dead
    for _ in range(5):
        facts = asyncio.run(feed.refresh())
    assert facts.source is support.Source.CACHED
    assert facts.build_version == "0.45.0"
    assert "HTTP 500" in facts.staleness()

    # Positive control: with nothing ever fetched there is nothing to keep, and
    # the built-in block is the honest answer.
    fresh = support.SupportFeed(cfg)
    fresh._fetch = dead
    assert asyncio.run(fresh.refresh()).source is support.Source.BUILT_IN


def test_a_malformed_two_hundred_does_not_discard_a_good_cache():
    """The last-known-good fix handled `raw is None` only. Codex, spider-bot#3,
    2026-09-04: a 200 carrying malformed JSON or an unknown schema still made
    `parse()` return the built-in block, and that assignment threw the valid
    cache away — a feed that starts serving nonsense is the same class of
    failure as one that stops answering."""
    import asyncio

    from conftest import make_cfg

    cfg = make_cfg()
    object.__setattr__(cfg, "support_feed_url", "https://example/feed.json")
    feed = support.SupportFeed(cfg)
    feed._facts = support.SupportFacts(source=support.Source.FEED, build_version="0.45.0")

    async def nonsense():
        return "{ this is not json", ""

    feed._fetch = nonsense
    facts = asyncio.run(feed.refresh(force=True))
    assert facts.source is support.Source.CACHED
    assert facts.build_version == "0.45.0"

    # Positive control: a GOOD 200 replaces the cache, which is the whole point.
    good = json.dumps({
        "format": support.FEED_FORMAT,
        "schema_version": support.SUPPORTED_SCHEMA,
        "build_version": "0.46.0",
    })

    async def fine():
        return good, ""

    feed._fetch = fine
    assert asyncio.run(feed.refresh(force=True)).build_version == "0.46.0"


def test_an_empty_support_feed_url_turns_the_feed_off():
    """The documented way to select the built-in block. Codex, spider-bot#3,
    2026-09-04: `_env` replaced an empty value with the default, so it could
    not be selected."""
    import os

    from spiderbot import config

    before = dict(os.environ)
    try:
        os.environ.update(
            DISCORD_BOT_TOKEN_SPIDERBOT="x" * 60, GUILD_ID="1", SUPPORT_FEED_URL=""
        )
        assert config.load().support_feed_url == ""
        # Positive control: unset means the default, not empty.
        del os.environ["SUPPORT_FEED_URL"]
        assert config.load().support_feed_url.startswith("https://")
    finally:
        os.environ.clear()
        os.environ.update(before)
