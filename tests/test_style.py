"""spiderbot/style.py - the locked visual system, enforced.

A locked palette nobody checks is a preference. These are the guards that make
plan §5 binding: the hex values are the ones the owner locked, no surface
reaches for a stock Discord colour, and no emoji outside the closed vocabulary
appears on anything a member can see.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import make_cfg

from spiderbot import presets, style
from spiderbot.ui.home import build_home_embed
from spiderbot.ui.routes import ROUTES, Audience, visible_routes

SOURCE = sorted(Path("spiderbot").rglob("*.py"))


# -- the palette is the one that was locked ---------------------------------


@pytest.mark.parametrize(
    ("name", "hex_value"),
    [
        ("BRAND", 0x1A8F5C),
        ("SUCCESS", 0x2ECC71),
        ("WARNING", 0xF39C12),
        ("ALARM", 0xE74C3C),
        ("AI", 0x9B59B6),
        ("NEUTRAL", 0x34495E),
        ("ACCENT", 0x00D4AA),
    ],
)
def test_palette_matches_the_locked_hex_values(name, hex_value):
    assert getattr(style, name).value == hex_value


def test_brand_is_green_and_warning_is_orange():
    # The one deliberate correction in plan §5: an orange brand collides with
    # the warning colour on a phone-sized embed stripe, so brand is green and
    # orange is reserved for "you need to do something".
    assert style.BRAND != style.WARNING
    r, g, b = style.BRAND.to_rgb()
    assert g > r and g > b, "the brand colour must read as green"
    r, g, b = style.WARNING.to_rgb()
    assert r > b and g > b, "the warning colour must read as orange"


def test_no_surface_reaches_for_a_stock_discord_colour():
    # discord.Color.green() and friends are how a palette quietly stops being
    # one. The constructor form (discord.Color(0x1A8F5C)) is what style.py uses.
    stock = re.compile(r"discord\.Colou?r\.[a-z_]+\(")
    offenders = [
        f"{path}:{n}"
        for path in SOURCE
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if stock.search(line)
    ]
    assert offenders == [], f"use the palette in spiderbot/style.py: {offenders}"


# -- the emoji vocabulary is closed ----------------------------------------

_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),  # pictographs, emoticons, transport, supplemental
    (0x2600, 0x27BF),  # misc symbols and dingbats
    (0x2B00, 0x2BFF),  # misc symbols and arrows
)


def emoji_chars(text: str) -> set[str]:
    """Every emoji-presentation character in `text`, selectors stripped."""
    return {
        ch
        for ch in text.replace(style.VARIATION_SELECTOR, "")
        if any(lo <= ord(ch) <= hi for lo, hi in _EMOJI_RANGES)
    }


def vocabulary() -> set[str]:
    return {e.replace(style.VARIATION_SELECTOR, "") for e in style.VOCABULARY}


def test_the_vocabulary_is_the_eleven_the_plan_locked():
    assert len(style.VOCABULARY) == 11


def test_no_route_uses_an_emoji_outside_the_vocabulary():
    for route in ROUTES:
        assert emoji_chars(route.emoji) <= vocabulary(), route.key


def test_no_preset_uses_an_emoji_outside_the_vocabulary():
    cfg = make_cfg()
    for preset in presets.PRESETS:
        surface = preset.emoji + preset.label + presets.render(preset, cfg)
        stray = emoji_chars(surface) - vocabulary()
        assert stray == set(), f"{preset.key} uses {stray}"


def test_no_shipped_embed_uses_an_emoji_outside_the_vocabulary():
    cfg = make_cfg()
    shipped = [
        build_home_embed(visible_routes(Audience.EVERYONE), Audience.EVERYONE),
        build_home_embed(visible_routes(Audience.MOD), Audience.MOD),
        presets.steps_embed(cfg),
    ]
    for embed in shipped:
        surface = (embed.title or "") + (embed.description or "") + (embed.footer.text or "")
        stray = emoji_chars(surface) - vocabulary()
        assert stray == set(), f"{embed.title!r} uses {stray}"


def test_no_source_file_carries_a_pasted_emoji():
    # Emoji reach the code as \N{NAME} escapes via style.py, never pasted in.
    # A literal character in a source file is how a twelfth emoji gets in.
    offenders = {}
    for path in SOURCE:
        stray = emoji_chars(path.read_text(encoding="utf-8"))
        if stray:
            offenders[str(path)] = stray
    assert offenders == {}, f"route these through spiderbot/style.py: {offenders}"


# -- the house embed --------------------------------------------------------


def test_every_house_embed_carries_the_same_author_and_a_footer():
    e = style.embed(title="t", description="d")
    assert e.author.name == style.AUTHOR_NAME, "never 'Bot', never 'System'"
    assert e.footer.text, "a public embed always carries the house footer"
    assert e.color == style.BRAND, "brand is the default voice"


def test_the_footer_states_the_panel_lifetime_up_front():
    # A member who was told it expires reads a greyed-out button as "timed out"
    # rather than "broken".
    assert "3 min" in style.panel_footer(180)
    assert "stays put" in style.panel_footer(None)


def test_the_ai_never_speaks_without_purple_and_the_balloon():
    e = style.ai_embed("hello")
    assert e.color == style.AI
    assert e.description.startswith(style.SPEECH)


def test_avatar_url_degrades_before_the_bot_is_connected():
    assert style.avatar_url(object()) is None
