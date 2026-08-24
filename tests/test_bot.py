"""spiderbot/bot.py - intents stay least-privilege and mention noise stays silent."""

from __future__ import annotations

import asyncio

from conftest import make_cfg
from discord.ext import commands

from spiderbot.bot import _EXTENSIONS, SpiderBot
from spiderbot.ui import routes
from spiderbot.ui.home import build_pinned_home


def test_intents_are_exactly_what_the_portal_grants():
    bot = SpiderBot(make_cfg())
    assert bot.intents.members is True  # welcome-on-join (portal-enabled)
    assert bot.intents.message_content is True  # owner-approved 2026-08-24
    assert bot.intents.presences is False  # never requested - stays off


def test_default_allowed_mentions_are_none():
    bot = SpiderBot(make_cfg())
    am = bot.allowed_mentions
    assert not am.everyone and not am.users and not am.roles and not am.replied_user


def test_command_not_found_is_silenced():
    # when_mentioned parses every plain mention as a command; the chat cog
    # owns that path, so CommandNotFound must stay quiet.
    bot = SpiderBot(make_cfg())
    asyncio.run(bot.on_command_error(None, commands.CommandNotFound("nope")))


def test_every_extension_loads_without_a_connection():
    """The real cogs, against a real bot object - no fakes, no network.

    Fakes cannot catch a bad decorator, a missing import or a cog whose
    __init__ touches the client. This is the "boots without Discord" gate.
    """
    bot = SpiderBot(make_cfg())

    async def load():
        for ext in _EXTENSIONS:
            await bot.load_extension(ext)
        return sorted(bot.cogs)

    assert asyncio.run(load()) == [
        "AdminCog",
        "ChatCog",
        "CommunityCog",
        "HomeCog",
        "RosterCog",
    ]


def test_pinned_panel_registers_as_a_persistent_view():
    """discord.py only accepts a persistent view when every item has a stable
    custom_id and the view never times out - the exact contract that keeps a
    pinned panel alive across a deploy."""
    bot = SpiderBot(make_cfg())
    _embed, view = build_pinned_home(bot)
    bot.add_view(view)  # raises if the contract is broken
    assert view.timeout is None


def test_route_registry_is_clean_at_boot():
    assert routes.validate() == []
