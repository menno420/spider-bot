"""spiderbot/ui/safe.py - Discord's hard limits, enforced in one place.

The failure this guards against is not an exception: one component over its
limit makes Discord reject the *whole* message with 400, and inside a panel
edit that means the edit silently never lands. So every test here asserts on
what would be sent, not on what was raised.
"""

from __future__ import annotations

import asyncio

import discord
from conftest import FakeGuild, FakeInteraction, FakeUser, make_cfg

from spiderbot import presets
from spiderbot.ui import safe
from spiderbot.ui.home import build_home_embed
from spiderbot.ui.routes import Audience, visible_routes


def run(coro):
    return asyncio.run(coro)


# -- clamp_embed -------------------------------------------------------------


def test_a_healthy_embed_passes_through_untouched():
    embed = discord.Embed(title="Title", description="Body")
    embed.add_field(name="Days active", value="8/14", inline=True)
    before = embed.to_dict()
    assert safe.clamp_embed(embed).to_dict() == before


def test_an_oversized_field_value_is_clamped_not_rejected():
    # The 1024 field cap is the one nobody truncates by hand - and the plan's
    # own two-column status fields are exactly where it will bite.
    embed = discord.Embed(title="t", description="d")
    embed.add_field(name="n", value="x" * 2000)
    safe.clamp_embed(embed)
    assert len(embed.fields[0].value) == safe.FIELD_VALUE_LIMIT
    assert embed.fields[0].value.endswith("\N{HORIZONTAL ELLIPSIS}")


def test_oversized_title_and_description_are_clamped():
    # Sized so the per-component caps alone stay under the 6000 total, which
    # is what isolates this from the total-budget pass below.
    embed = discord.Embed(title="t" * 500, description="d" * 5000)
    safe.clamp_embed(embed)
    assert len(embed.title) == safe.TITLE_LIMIT
    assert len(embed.description) == safe.DESCRIPTION_LIMIT


def test_an_oversized_footer_is_clamped():
    embed = discord.Embed(title="t", description="d")
    embed.set_footer(text="f" * 3000)
    safe.clamp_embed(embed)
    assert len(embed.footer.text) == safe.FOOTER_LIMIT


def test_components_at_their_own_caps_still_respect_the_total():
    # 256 + 4096 + 2048 = 6400: every part legal, the sum is not.
    embed = discord.Embed(title="t" * 500, description="d" * 5000)
    embed.set_footer(text="f" * 3000)
    safe.clamp_embed(embed)
    assert len(embed.description) < safe.DESCRIPTION_LIMIT, "description gives way first"
    assert safe.embed_length(embed) <= safe.TOTAL_LIMIT


def test_fields_past_discords_cap_are_dropped():
    embed = discord.Embed(title="t")
    for i in range(30):
        embed.add_field(name=f"f{i}", value="v")
    safe.clamp_embed(embed)
    assert len(embed.fields) == safe.MAX_FIELDS


def test_the_six_thousand_total_is_enforced_even_when_each_part_fits():
    # Every component below is individually legal; the sum is not. This is the
    # incident that cost the donor a panel's Back button.
    embed = discord.Embed(title="t", description="d" * 3000)
    for i in range(5):
        embed.add_field(name=f"f{i}", value="y" * 1000)
    assert safe.embed_length(embed) > safe.TOTAL_LIMIT
    safe.clamp_embed(embed)
    assert safe.embed_length(embed) <= safe.TOTAL_LIMIT


def test_clamping_never_chokes_on_a_non_embed():
    sentinel = object()
    assert safe.clamp_embed(sentinel) is sentinel


def test_every_shipped_embed_already_fits():
    # A guard, not a clamp test: if an embed the bot actually sends ever grows
    # past the budget, this fails here rather than silently in production.
    cfg = make_cfg()
    shipped = [
        build_home_embed(visible_routes(Audience.EVERYONE), Audience.EVERYONE),
        build_home_embed(visible_routes(Audience.MOD), Audience.MOD),
        presets.steps_embed(cfg),
    ]
    for embed in shipped:
        assert safe.embed_length(embed) <= safe.TOTAL_LIMIT
        assert len(embed.description or "") <= safe.DESCRIPTION_LIMIT


# -- the interaction helpers -------------------------------------------------


def test_safe_defer_is_idempotent():
    interaction = FakeInteraction(FakeGuild())
    assert run(safe.safe_defer(interaction, ephemeral=True)) is True
    assert interaction.response.deferred is True
    assert run(safe.safe_defer(interaction)) is True  # already done: no-op, not an error


def test_safe_followup_clamps_before_sending():
    interaction = FakeInteraction(FakeGuild())
    embed = discord.Embed(title="t", description="x" * 9000)
    run(safe.safe_followup(interaction, embed=embed, ephemeral=True))
    _content, kwargs = interaction.response.messages[0]
    assert len(kwargs["embed"].description) == safe.DESCRIPTION_LIMIT


def test_safe_followup_returns_the_handle_a_panel_needs():
    interaction = FakeInteraction(FakeGuild())
    handle = run(safe.safe_followup(interaction, "hi", ephemeral=True))
    assert handle is interaction.original


def test_safe_followup_routes_through_followup_once_responded():
    interaction = FakeInteraction(FakeGuild())
    run(safe.safe_defer(interaction, ephemeral=True))
    run(safe.safe_followup(interaction, "after", ephemeral=True))
    assert interaction.followup.messages, "a deferred interaction must use followup"
    assert interaction.response.messages == []


def test_safe_edit_clamps_and_edits_in_place():
    interaction = FakeInteraction(FakeGuild(), user=FakeUser(1))
    embed = discord.Embed(title="t", description="z" * 9000)
    assert run(safe.safe_edit(interaction, embed=embed)) is True
    assert len(interaction.response.edits[0]["embed"].description) == safe.DESCRIPTION_LIMIT


def test_safe_edit_uses_the_original_response_once_deferred():
    interaction = FakeInteraction(FakeGuild())
    run(safe.safe_defer(interaction, ephemeral=True))
    assert run(safe.safe_edit(interaction, content="updated")) is True
    assert interaction.response.edits[0]["content"] == "updated"
