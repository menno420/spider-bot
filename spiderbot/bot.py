"""SpiderBot client: intents, cog loading, guild-scoped command sync,
channel resolution by name at startup."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from spiderbot import audit
from spiderbot.ai.gateway import Gateway

log = logging.getLogger("spiderbot")

_EXTENSIONS = (
    "spiderbot.cogs.community",
    "spiderbot.cogs.tester",
    "spiderbot.cogs.admin",
    "spiderbot.cogs.chat",
)


class SpiderBot(commands.Bot):
    def __init__(self, cfg) -> None:
        intents = discord.Intents.default()
        intents.members = True  # welcome-on-join (portal intent enabled)
        intents.message_content = True  # AI initiative (owner-approved 2026-08-24)
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.cfg = cfg
        self.ai = Gateway(cfg)
        self.channels: dict[str, discord.abc.GuildChannel] = {}

    async def setup_hook(self) -> None:
        for ext in _EXTENSIONS:
            await self.load_extension(ext)
        guild = discord.Object(id=self.cfg.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("synced %d guild commands", len(synced))

    async def on_command_error(self, ctx, error) -> None:
        # when_mentioned makes every mention parse as a prefix command; a
        # plain chat mention then raises CommandNotFound. That path is
        # handled by the chat cog - silence the noise, surface the rest.
        from discord.ext import commands as _c

        if isinstance(error, _c.CommandNotFound):
            return
        log.error("command error: %s", error)

    async def on_ready(self) -> None:
        guild = self.get_guild(self.cfg.guild_id)
        if guild is None:
            log.error("bot is not in guild %s - invite it first", self.cfg.guild_id)
            return
        wanted = {
            self.cfg.ch_start_here,
            self.cfg.ch_general,
            self.cfg.ch_mod_log,
            self.cfg.ch_feedback,
            self.cfg.ch_announcements,
        }
        for ch in guild.channels:
            if ch.name in wanted:
                self.channels[ch.name] = ch
        missing = wanted - set(self.channels)
        if missing:
            log.warning("channels not found (features degrade): %s", ", ".join(sorted(missing)))
        audit.stdout_event(
            "ready",
            user=str(self.user),
            guild=guild.name,
            members=guild.member_count,
            channels=sorted(self.channels),
            ai=self.ai.enabled,
        )
        log.info("ready as %s in %s; AI=%s", self.user, guild.name, self.ai.enabled)
