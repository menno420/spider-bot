"""spiderbot/ui/* and spiderbot/presets.py - the app-like surface.

The contracts that matter here are the ones superbot learned the hard way:
a rendered button always has a handler, a viewer is never shown an action
they may not take, authority is re-checked when the button is pressed rather
than trusted from when the panel was opened, and nothing reaches the server
until it has been previewed and confirmed.
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import (
    FakeAI,
    FakeBot,
    FakeChannel,
    FakeGuild,
    FakeInteraction,
    FakeMember,
    FakeMessageHandle,
    FakeRole,
    forbidden,
    make_cfg,
)

from spiderbot import presets
from spiderbot.ai.gateway import AIResult
from spiderbot.ui.base import BUTTONS_PER_ROW, Panel
from spiderbot.ui.forms import AskModal, BugReportModal, FeedbackModal
from spiderbot.ui.home import (
    ConfirmPost,
    HomePanel,
    PresetPanel,
    build_home,
    build_home_embed,
    build_pinned_home,
    health_lines,
)
from spiderbot.ui.routes import (
    ROUTES,
    Audience,
    audience_for,
    validate,
    visible_routes,
)

TESTER_ROLE = FakeRole(1, "Slingy Tester")


def build(ai=None, **channels):
    bot = FakeBot(make_cfg(), ai or FakeAI())
    bot.channels["mod-log"] = FakeChannel(name="mod-log")
    bot.channels["general"] = FakeChannel(name="general")
    bot.channels["start-here"] = FakeChannel(name="start-here")
    bot.channels["announcements"] = FakeChannel(name="announcements")
    bot.channels.update(channels)
    return bot


def member(*, mod=False, tester=False, name="Alice", id=7):
    return FakeMember(id, name, roles=[TESTER_ROLE] if tester else [], mod=mod)


def panel_for(bot, who):
    return HomePanel(bot, who, audience_for(who, bot.cfg))


def texts(interaction):
    """Everything the interaction said, embeds flattened in."""
    parts = list(interaction.replies)
    parts += [e.description or "" for e in interaction.embeds]
    return "\n".join(p for p in parts if p)


# -- the registry ------------------------------------------------------------


def test_registry_is_valid_at_boot():
    assert validate() == [], "boot validation must be clean on the shipped registry"


def test_no_row_exceeds_the_discord_button_budget():
    for row in {r.row for r in ROUTES}:
        assert sum(1 for r in ROUTES if r.row == row) <= BUTTONS_PER_ROW


def test_every_route_has_a_handler():
    # A route with no handler renders as a button that apologises - the exact
    # dead-surface the plan forbids.
    home = panel_for(build(), member(mod=True))
    missing = [r.key for r in ROUTES if not hasattr(home, f"_do_{r.key}")]
    assert missing == []


@pytest.mark.parametrize(
    ("who", "expected"),
    [
        (dict(mod=True), Audience.MOD),
        (dict(tester=True), Audience.TESTER),
        (dict(), Audience.EVERYONE),
    ],
)
def test_audience_resolves_from_live_state(who, expected):
    assert audience_for(member(**who), make_cfg()) is expected


def test_members_never_see_staff_routes():
    keys = {r.key for r in visible_routes(Audience.EVERYONE)}
    assert "clock" not in keys and "post" not in keys
    assert "join" in keys


def test_mods_see_everything():
    assert len(visible_routes(Audience.MOD)) == len(ROUTES)


def test_home_embed_describes_only_visible_routes():
    routes = visible_routes(Audience.EVERYONE)
    text = build_home_embed(routes, Audience.EVERYONE).description
    assert "Test status" not in text
    assert "How do I join?" in text


def test_member_panel_has_no_staff_buttons():
    home = panel_for(build(), member())
    labels = {getattr(b, "label", None) for b in home.children}
    assert "Test status" not in labels
    assert "How do I join?" in labels


# -- authority is re-checked at press time -----------------------------------


def test_member_pressing_a_staff_route_is_refused(audit_events):
    bot = build()
    # Panel built as a mod, then pressed by a plain member: the render is not
    # the authorisation.
    home = HomePanel(bot, member(mod=True), Audience.MOD)
    interaction = FakeInteraction(FakeGuild(), user=member(name="Sneaky", id=8))
    asyncio.run(home.handle("clock", interaction))
    assert "staff-only" in texts(interaction)
    assert audit_events == []


def test_unknown_route_key_is_handled_not_crashed():
    home = panel_for(build(), member(mod=True))
    interaction = FakeInteraction(FakeGuild())
    asyncio.run(home.handle("nonexistent", interaction))
    assert "no longer available" in texts(interaction)


def test_panel_belongs_to_the_person_who_opened_it():
    owner = member(name="Owner", id=1)
    p = Panel(owner)
    interaction = FakeInteraction(FakeGuild(), user=member(name="Other", id=2))
    assert asyncio.run(p.interaction_check(interaction)) is False
    assert "belongs to someone else" in texts(interaction)


def test_public_panel_is_open_to_everyone():
    p = Panel(member(id=1), public=True)
    interaction = FakeInteraction(FakeGuild(), user=member(id=2))
    assert asyncio.run(p.interaction_check(interaction)) is True


# -- member actions ----------------------------------------------------------


def test_join_button_shows_the_official_links(audit_events):
    bot = build()
    interaction = FakeInteraction(FakeGuild())
    asyncio.run(panel_for(bot, member()).handle("join", interaction))
    assert bot.cfg.optin_url in texts(interaction)
    assert [e["kind"] for e in audit_events] == ["jointest_used"]


def test_opted_in_button_notifies_staff_and_grants_nothing(audit_events):
    bot = build()
    who = member()
    interaction = FakeInteraction(FakeGuild(), user=who)
    asyncio.run(panel_for(bot, who).handle("optedin", interaction))
    assert who.roles == [], "the role is human-granted only (invariant 5)"
    assert bot.channels["mod-log"].sent, "staff must be told to verify"
    assert [e["kind"] for e in audit_events] == ["opted_in_claim"]


def test_opted_in_button_is_a_no_op_for_existing_testers(audit_events):
    bot = build()
    who = member(tester=True)
    interaction = FakeInteraction(FakeGuild(), user=who)
    asyncio.run(panel_for(bot, who).handle("optedin", interaction))
    assert bot.channels["mod-log"].sent == []
    assert audit_events == []
    assert "already" in texts(interaction)


@pytest.mark.parametrize(
    ("key", "modal"), [("feedback", FeedbackModal), ("bug", BugReportModal)]
)
def test_intake_buttons_open_their_form(key, modal):
    bot = build()
    interaction = FakeInteraction(FakeGuild())
    asyncio.run(panel_for(bot, member()).handle(key, interaction))
    assert isinstance(interaction.response.modals[0], modal)


def test_ask_opens_a_form_when_the_ai_is_on():
    bot = build(ai=FakeAI(AIResult("hi", "ok"), enabled=True))
    interaction = FakeInteraction(FakeGuild())
    asyncio.run(panel_for(bot, member()).handle("ask", interaction))
    assert isinstance(interaction.response.modals[0], AskModal)


def test_ask_degrades_to_a_human_when_the_ai_is_off():
    bot = build(ai=FakeAI(enabled=False))
    interaction = FakeInteraction(FakeGuild())
    asyncio.run(panel_for(bot, member()).handle("ask", interaction))
    assert interaction.response.modals == []
    assert "#general" in texts(interaction)


# -- staff actions -----------------------------------------------------------


def test_clock_button_reports_the_cohort(audit_events):
    bot = build()
    guild = FakeGuild(roles=[TESTER_ROLE])
    interaction = FakeInteraction(guild, user=member(mod=True))
    asyncio.run(panel_for(bot, member(mod=True)).handle("clock", interaction))
    assert "No verified testers yet" in texts(interaction)
    assert [e["kind"] for e in audit_events] == ["cohort_reported"]


def test_health_button_never_leaks_a_secret():
    bot = build()
    lines = "\n".join(health_lines(bot))
    assert bot.cfg.discord_token not in lines
    assert "Spider Bot v" in lines


# -- presets -----------------------------------------------------------------


def test_every_preset_renders_and_fits_in_a_message():
    cfg = make_cfg()
    for preset in presets.PRESETS:
        body = presets.render(preset, cfg)
        assert "{" not in body, f"{preset.key} has an unfilled placeholder"
        assert len(body) < 1900, f"{preset.key} is too long for one message"


def test_preset_links_come_from_config():
    cfg = make_cfg()
    body = presets.render(presets.PRESETS_BY_KEY["join-steps"], cfg)
    assert cfg.group_url in body and cfg.optin_url in body


def test_preset_keys_are_unique():
    keys = [p.key for p in presets.PRESETS]
    assert len(keys) == len(set(keys))


def test_preset_channels_are_ones_the_bot_resolves():
    known = {"general", "start-here", "announcements", "feedback", "mod-log", "bug-reports"}
    for preset in presets.PRESETS:
        assert preset.channel in known, preset.key


# -- posting a preset: preview, then confirm ---------------------------------


def test_preview_shows_the_text_and_the_destination():
    bot = build()
    who = member(mod=True)
    picker = PresetPanel(bot, who)
    interaction = FakeInteraction(FakeGuild(), user=who)
    asyncio.run(picker.preview(interaction, "stay-opted-in"))
    embed = interaction.embeds[0]
    assert "Leave the beta" in embed.description
    assert "pings" in embed.footer.text and "general" in embed.footer.text


def test_preview_posts_nothing_by_itself():
    bot = build()
    who = member(mod=True)
    asyncio.run(PresetPanel(bot, who).preview(FakeInteraction(FakeGuild(), user=who), "recruit"))
    assert bot.channels["general"].sent == []


def test_confirm_posts_to_the_presets_own_channel(audit_events):
    bot = build()
    who = member(mod=True)
    confirm = ConfirmPost(bot, who, presets.PRESETS_BY_KEY["join-steps"])
    interaction = FakeInteraction(FakeGuild(roles=[TESTER_ROLE]), user=who)
    asyncio.run(confirm.post.callback(interaction))
    assert bot.channels["start-here"].sent, "join-steps posts to #start-here"
    assert bot.channels["general"].sent == []
    assert [e["kind"] for e in audit_events] == ["preset_posted"]


def test_confirm_pings_testers_only_when_the_preset_says_so():
    bot = build()
    who = member(mod=True)
    guild = FakeGuild(roles=[TESTER_ROLE])
    quiet = ConfirmPost(bot, who, presets.PRESETS_BY_KEY["recruit"])
    asyncio.run(quiet.post.callback(FakeInteraction(guild, user=who)))
    _args, kwargs = bot.channels["general"].sent[-1]
    assert kwargs["allowed_mentions"].roles in (False, [], None)

    loud = ConfirmPost(bot, who, presets.PRESETS_BY_KEY["stay-opted-in"])
    asyncio.run(loud.post.callback(FakeInteraction(guild, user=who)))
    _args, kwargs = bot.channels["general"].sent[-1]
    assert kwargs["allowed_mentions"].roles == [TESTER_ROLE]


def test_confirm_refuses_a_non_mod(audit_events):
    bot = build()
    confirm = ConfirmPost(bot, member(mod=True), presets.PRESETS_BY_KEY["recruit"])
    interaction = FakeInteraction(FakeGuild(), user=member(name="Sneaky", id=9))
    asyncio.run(confirm.post.callback(interaction))
    assert bot.channels["general"].sent == []
    assert audit_events == []


def test_confirm_degrades_when_the_channel_is_missing(audit_events):
    bot = build()
    bot.channels.pop("start-here")
    who = member(mod=True)
    confirm = ConfirmPost(bot, who, presets.PRESETS_BY_KEY["join-steps"])
    interaction = FakeInteraction(FakeGuild(), user=who)
    asyncio.run(confirm.post.callback(interaction))
    assert "nothing was posted" in str(interaction.response.edits).lower()
    assert audit_events == []


def test_cancel_posts_nothing():
    bot = build()
    who = member(mod=True)
    confirm = ConfirmPost(bot, who, presets.PRESETS_BY_KEY["stay-opted-in"])
    asyncio.run(confirm.cancel.callback(FakeInteraction(FakeGuild(), user=who)))
    assert bot.channels["general"].sent == []


# -- the pinned panel --------------------------------------------------------


def test_pinned_panel_survives_restarts_and_hides_staff_actions():
    _embed, view = build_pinned_home(build())
    assert view.timeout is None, "a pinned panel must not expire"
    assert view.public is True
    ids = {b.custom_id for b in view.children}
    assert all(i and i.startswith("spiderbot:home:") for i in ids), "needs stable custom_ids"
    assert not any(getattr(b, "label", "") == "Test status" for b in view.children)


def test_build_home_is_the_shared_factory():
    bot = build()
    embed, view = build_home(bot, member(mod=True))
    assert isinstance(view, HomePanel)
    assert "Test status" in embed.description


# -- panels can expire themselves (Phase 0 repair) ---------------------------
# `on_timeout` disables the buttons and edits the message - but it returns
# early when `message` is None. An unbound panel therefore keeps live-looking
# buttons that do nothing, which is exactly what a member cannot distinguish
# from a working one. /home bound it; the panels underneath it did not.


def test_the_post_menu_binds_its_message_and_expires():
    bot = build()
    who = member(mod=True)
    interaction = FakeInteraction(FakeGuild(), user=who)
    asyncio.run(panel_for(bot, who).handle("post", interaction))

    _content, kwargs = interaction.response.messages[0]
    picker = kwargs["view"]
    assert isinstance(picker, PresetPanel)
    assert picker.message is not None, "unbound: on_timeout would be a no-op"

    asyncio.run(picker.on_timeout())
    assert all(item.disabled for item in picker.children)
    assert picker.message.edits, "the timeout must actually reach Discord"


def test_the_confirm_step_binds_its_message_and_expires():
    bot = build()
    who = member(mod=True)
    picker = PresetPanel(bot, who)
    interaction = FakeInteraction(FakeGuild(), user=who)
    asyncio.run(picker.preview(interaction, "recruit"))

    confirm = interaction.response.edits[0]["view"]
    assert isinstance(confirm, ConfirmPost)
    assert confirm.message is not None

    asyncio.run(confirm.on_timeout())
    assert all(item.disabled for item in confirm.children)
    assert confirm.message.edits


def test_the_confirm_step_reuses_the_pickers_own_message():
    # Preview and confirm are the same Discord message with a different view,
    # so the confirm step must inherit the handle rather than fetch a second.
    bot = build()
    who = member(mod=True)
    picker = PresetPanel(bot, who)
    picker.message = FakeMessageHandle(id=4242)
    interaction = FakeInteraction(FakeGuild(), user=who)
    asyncio.run(picker.preview(interaction, "recruit"))
    assert interaction.response.edits[0]["view"].message is picker.message


def test_an_unbindable_panel_still_works():
    # Binding is best-effort: losing the handle must degrade to "cannot grey
    # out", never to a raising callback.
    bot = build()
    who = member(mod=True)
    interaction = FakeInteraction(FakeGuild(), user=who)

    async def gone():
        raise forbidden()  # what losing the handle actually looks like

    interaction.original_response = gone
    asyncio.run(panel_for(bot, who).handle("post", interaction))
    picker = interaction.response.messages[0][1]["view"]
    assert picker.message is None
    asyncio.run(picker.on_timeout())  # must not raise
    assert all(item.disabled for item in picker.children)
