"""SpiderBot client: intents, cog loading, guild-scoped command sync,
channel resolution by name at startup."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from spiderbot import audit, store, support
from spiderbot.ai.gateway import Gateway
from spiderbot.intake import github_sink
from spiderbot.intake.service import IntakeService
from spiderbot.moderation import policy as policy_module
from spiderbot.moderation.classifier import Classifier
from spiderbot.moderation.contracts import Operation
from spiderbot.moderation.service import ModerationService
from spiderbot.ui import routes

log = logging.getLogger("spiderbot")

_EXTENSIONS = (
    "spiderbot.cogs.community",
    "spiderbot.cogs.tester",
    "spiderbot.cogs.admin",
    "spiderbot.cogs.chat",
    "spiderbot.cogs.home",
    "spiderbot.cogs.membership",
    "spiderbot.cogs.serverlog",
    "spiderbot.cogs.moderation",
    "spiderbot.cogs.intake",
)


class SpiderBot(commands.Bot):
    def __init__(self, cfg) -> None:
        intents = discord.Intents.default()
        intents.members = True  # welcome-on-join (portal intent enabled)
        intents.message_content = True  # AI initiative (owner-approved 2026-08-24)
        # Ban/unban and audit-log-entry events, for server logging. NOT a
        # privileged intent (discord/flags.py:913-926), so it needs no portal
        # change - it only widens what the gateway sends us.
        intents.moderation = True
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.cfg = cfg
        self.support = support.SupportFeed(cfg)
        # The gateway asks the feed for the game half of its system prompt on
        # every call. `current` never blocks and never returns None — it is
        # whatever was last fetched, or the built-in block — so an unreachable
        # feed costs freshness, never availability.
        self.ai = Gateway(cfg, knowledge_provider=self._game_knowledge)
        self.channels: dict[str, discord.abc.GuildChannel] = {}
        # Built in on_ready, once the state channels are resolved. Until then
        # every one of these is None and every surface that uses one says so
        # rather than failing - unconfigured is silent (invariant 4), and a
        # feature that cannot store anything must not pretend it can.
        self.intake: IntakeService | None = None
        self.moderation: ModerationService | None = None

    def _game_knowledge(self) -> str:
        """The game facts, plus one honest line about where they came from.

        The staleness line is NOT optional and is never omitted: a model told
        the build version without being told how fresh it is will state it with
        the same confidence either way, and "the bot knows the current game
        rather than an old copy of it" is the whole point of the feed.
        """
        facts = self.support.current
        return facts.as_prompt_block() + "\n\nProvenance: " + facts.staleness()

    async def setup_hook(self) -> None:
        problems = routes.validate()
        for problem in problems:  # a bad registry degrades, never blocks boot
            log.error("route registry: %s", problem)
        for ext in _EXTENSIONS:
            await self.load_extension(ext)
        # Re-attach the pinned Home panel: Discord matches its buttons back
        # by custom_id, so without this a pinned panel dies on every deploy.
        from spiderbot.ui.home import build_pinned_home, build_welcome_panel

        self.add_view(build_pinned_home(self)[1])
        # Same reason for the welcome's single button: without this, every
        # greeting posted before the last deploy becomes a dead button.
        self.add_view(build_welcome_panel(self))
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
            self.cfg.ch_bug_reports,
            self.cfg.ch_announcements,
            self.cfg.ch_bot_state,
            self.cfg.ch_intake_state,
            self.cfg.ch_case_state,
        }
        for ch in guild.channels:
            if ch.name in wanted:
                self.channels[ch.name] = ch
        missing = wanted - set(self.channels)
        if missing:
            log.warning("channels not found (features degrade): %s", ", ".join(sorted(missing)))
        self._build_services()
        await self.support.refresh()
        audit.stdout_event(
            "ready",
            user=str(self.user),
            guild=guild.name,
            members=guild.member_count,
            channels=sorted(self.channels),
            ai=self.ai.enabled,
            intake=self.intake is not None,
            github=bool(self.cfg.github_token) and self.cfg.intake_publish_enabled,
            moderation=self.cfg.mod_mode,
            moderation_channels=list(self.cfg.mod_watch_channels),
            support_feed=self.support.current.source,
        )
        log.info(
            "ready as %s in %s; AI=%s intake=%s moderation=%s",
            self.user, guild.name, self.ai.enabled,
            self.intake is not None, self.cfg.mod_mode,
        )

    def _build_services(self) -> None:
        """Assemble intake and moderation from whatever is actually configured.

        Every dependency is optional and every absence degrades to something
        honest: no state channel means no intake service at all (the panels say
        so); no GitHub token, or publication not enabled, means a client that
        refuses every publish by name and leaves the report queued.
        """
        cfg = self.cfg
        intake_channel = self.channels.get(cfg.ch_intake_state)
        if intake_channel is not None:
            client: github_sink.GitHubClient
            if not cfg.intake_publish_enabled:
                client = github_sink.NullGitHubClient(
                    "INTAKE_PUBLISH_ENABLED is false"
                )
            elif not cfg.github_token:
                client = github_sink.NullGitHubClient("GITHUB_TOKEN is not set")
            else:
                client = github_sink.HttpGitHubClient(cfg.github_token, cfg.github_repo)
            self.intake = IntakeService(
                store.DiscordChannelStore(intake_channel), client
            )
        else:
            log.warning(
                "#%s not found: reports have nowhere durable to go, so intake "
                "stays off rather than accepting reports it cannot keep",
                cfg.ch_intake_state,
            )

        case_channel = self.channels.get(cfg.ch_case_state)
        if cfg.mod_mode != "off" and case_channel is not None:
            try:
                ceiling = Operation(cfg.mod_ceiling)
            except ValueError:
                log.error(
                    "MOD_CEILING=%r is not an operation; falling back to "
                    "flag_for_review", cfg.mod_ceiling,
                )
                ceiling = Operation.FLAG_FOR_REVIEW
            problems = policy_module.validate()
            for problem in problems:
                log.error("moderation policy: %s", problem)
            self.moderation = ModerationService(
                mode=cfg.mod_mode,
                classifier=Classifier(self.ai),
                policy=policy_module.Policy(ceiling=ceiling),
                backing=store.DiscordChannelStore(case_channel),
                enabled_channels=cfg.mod_watch_channels,
            )
        elif cfg.mod_mode != "off":
            log.warning(
                "MOD_MODE=%s but #%s not found: moderation stays off, because a "
                "decision nobody can review is worse than no decision",
                cfg.mod_mode, cfg.ch_case_state,
            )
