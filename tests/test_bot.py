"""spiderbot/bot.py - intents stay least-privilege and mention noise stays silent."""

from __future__ import annotations

import asyncio

from conftest import make_cfg
from discord.ext import commands

from spiderbot.bot import SpiderBot


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
