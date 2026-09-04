"""Home - the one front door, built from the route registry.

Follows superbot's hub doctrine (`disbot/views/community/hub.py`): the panel
never hardcodes its own children, it renders whatever the registry says this
viewer may use, and one factory (`build_home`) is shared by every entry point
so the slash command and the pinned panel can never drift apart.

Restructured from the donor in two ways: children come from one typed registry
instead of two untyped ones, and authority is resolved from live Discord state
on every press rather than only at render - opening a panel never authorises
what happens after.
"""

from __future__ import annotations

import logging

import discord

from spiderbot import audit, cohort, presets, redact, roster, style
from spiderbot.moderation import cases as case_module
from spiderbot.ui.base import Panel, bind_message
from spiderbot.ui.forms import (
    AskModal,
    BugReportModal,
    ComplaintModal,
    FeedbackModal,
    IdeaModal,
)
from spiderbot.ui.routes import (
    ROUTES_BY_KEY,
    Audience,
    audience_for,
    visible_routes,
)
from spiderbot.ui.safe import safe_edit, safe_followup

log = logging.getLogger("spiderbot.ui.home")

NO_MENTIONS = discord.AllowedMentions.none()



def _my_report_line(report) -> str:
    """One line for the reporter. Their own words, escaped; no staff detail."""
    where = ""
    if report.github_issue_url:
        where = f" — [tracked]({report.github_issue_url})"
    elif report.status.value.startswith("publish"):
        where = " — waiting to be filed"
    state = {
        "published": "filed",
        "resolved": "resolved",
        "stored": "saved",
        "publish_pending": "filing",
        "publish_failed": "queued",
        "duplicate": "already known",
    }.get(report.status.value, report.status.value)
    title = redact.for_discord(report.title or "(no title)", limit=70)
    return f"`{report.id}` **{title}** — {state}{where}"


def _staff_report_line(report) -> str:
    private = "" if report.is_public_safe else " · private"
    issue = f" · #{report.github_issue_number}" if report.github_issue_number else ""
    title = redact.for_discord(report.title or "(no title)", limit=60)
    return f"`{report.id}` [{report.category}] {title} — {report.status}{issue}{private}"


def health_lines(bot) -> list[str]:
    """Bot health, shared by `/status` and Home's Bot-health button."""
    from spiderbot import __version__

    cfg = bot.cfg
    ai = "on" if bot.ai.enabled else "OFF (no key or AI_ENABLED=false)"
    return [
        f"Spider Bot v{__version__}",
        f"AI: {ai} | model `{cfg.ai_model}` | effort `{cfg.ai_effort}`",
        f"Initiative channels: {', '.join(cfg.initiative_channels) or '(none)'} "
        f"| cooldown {cfg.initiative_cooldown_s}s | cap {cfg.initiative_hourly_cap}/h",
        f"Resolved channels: {', '.join(sorted(bot.channels)) or '(none)'}",
    ]


def build_home_embed(
    routes,
    audience: Audience,
    *,
    timeout: float | None = Panel.DEFAULT_TIMEOUT,
    icon_url: str | None = None,
) -> discord.Embed:
    """Describe exactly the buttons this viewer can see - generated, never typed."""
    intro = (
        "Everything Spider Bot can do, one press away."
        if audience >= Audience.MOD
        else "Welcome to the web. Pick what you need:"
    )
    lines = [intro, ""]
    lines += [f"{r.emoji} **{r.label}** - {r.purpose}" for r in routes]
    if audience >= Audience.MOD:
        lines.append("")
        lines.append("*The bottom row is staff-only; members do not see it.*")
    return style.embed(
        title=f"{style.SPIDER} Spider Bot",
        description="\n".join(lines),
        color=style.BRAND,
        footer=style.panel_footer(timeout),
        icon_url=icon_url,
    )


class _RouteButton(discord.ui.Button):
    def __init__(
        self,
        route,
        panel: HomePanel,
        *,
        persistent: bool = False,
        prefix: str = "spiderbot:home:",
    ) -> None:
        super().__init__(
            label=route.label,
            emoji=route.emoji,
            row=route.row,
            # A pinned panel must survive restarts, and Discord matches its
            # buttons back to the view by custom_id - so they must be stable.
            custom_id=f"{prefix}{route.key}" if persistent else None,
            style=(
                discord.ButtonStyle.secondary
                if route.audience >= Audience.MOD
                else discord.ButtonStyle.primary
            ),
        )
        self.route = route
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.panel.handle(self.route.key, interaction)


class HomePanel(Panel):
    """The front door. Buttons are whatever the registry allows this viewer."""

    def __init__(
        self,
        bot,
        member,
        audience: Audience,
        *,
        public: bool = False,
        persistent: bool = False,
        routes=None,
        prefix: str = "spiderbot:home:",
        back=None,
    ) -> None:
        super().__init__(
            member,
            public=public,
            timeout=None if persistent else Panel.DEFAULT_TIMEOUT,
            back=back,
        )
        self.bot = bot
        self.cfg = bot.cfg
        self.audience = audience
        for route in (visible_routes(audience) if routes is None else routes):
            self.add_item(
                _RouteButton(route, self, persistent=persistent, prefix=prefix)
            )

    async def handle(self, key: str, interaction: discord.Interaction) -> None:
        """Dispatch one button press, re-checking authority first."""
        route = ROUTES_BY_KEY.get(key)
        if route is None:
            await interaction.response.send_message(
                "That button is no longer available - open a fresh `/home`.", ephemeral=True
            )
            return
        if audience_for(interaction.user, self.cfg) < route.audience:
            await interaction.response.send_message(
                "That one is staff-only.", ephemeral=True
            )
            return
        handler = getattr(self, f"_do_{key}", None)
        if handler is None:  # a route with no handler must not render as dead
            log.error("route %r has no handler", key)
            await interaction.response.send_message(
                "That is not wired up yet - tell Menno.", ephemeral=True
            )
            return
        await handler(interaction)

    # -- member actions ----------------------------------------------------

    async def _do_join(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=presets.steps_embed(self.cfg, icon_url=style.avatar_url(self.bot)),
            ephemeral=True,
        )
        audit.stdout_event("jointest_used", user=str(interaction.user), via="home")

    async def _do_optedin(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        already = any(
            getattr(r, "name", None) == self.cfg.tester_role_name
            for r in getattr(member, "roles", ()) or ()
        )
        if already:
            await interaction.response.send_message(
                f"You already have the **{self.cfg.tester_role_name}** role - you are "
                f"counted. Thank you for staying opted in!",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=style.embed(
                title=f"{style.OK} Noted - Menno will verify it",
                description=(
                    "Thank you! Menno checks the opt-in list in Play Console and "
                    "hands out the tester role by hand, so it may take a little "
                    "while. Keep the game installed in the meantime."
                ),
                color=style.SUCCESS,
                icon_url=style.avatar_url(self.bot),
            ),
            ephemeral=True,
        )
        await audit.modlog_event(
            self.bot.channels.get("mod-log"),
            "Possible new tester",
            f"{member.display_name} pressed **I've opted in** on the Home panel.\n"
            f"Verify the opted-in count moved in Play Console, then run "
            f"`/tester add` with user `{member.display_name}`.",
            style.WARNING,
        )
        audit.stdout_event("opted_in_claim", user=str(member), via="home")

    async def _do_feedback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(FeedbackModal(self.bot))

    async def _do_bug(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(BugReportModal(self.bot))

    async def _do_ask(self, interaction: discord.Interaction) -> None:
        if not self.bot.ai.enabled:
            await interaction.response.send_message(
                "My brain is switched off right now - ask in #general and a human "
                "will pick it up.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(AskModal(self.bot))

    async def _do_idea(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(IdeaModal(self.bot))

    async def _do_myreports(self, interaction: discord.Interaction) -> None:
        """What this person reported, and what happened to it.

        A PULL surface, deliberately. The bot never DMs first (invariant 6) and
        the two deliberate pings are spelled out and spoken for (invariant 20),
        so "your bug was fixed" cannot be pushed - it has to be somewhere a
        person can go and look. This is that place.
        """
        service = getattr(self.bot, "intake", None)
        if service is None:
            await interaction.response.send_message(
                "Reports are not switched on yet.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        mine = [
            report
            for report in await service.all_reports()
            if report.reporter and report.reporter.user_id == interaction.user.id
        ][:15]
        if not mine:
            body = (
                "You have not reported anything yet. Use **Report a bug**, "
                "**Send feedback** or **Share an idea** - or just tell me in the "
                "channel and I will offer to write it down."
            )
        else:
            body = "\n".join(_my_report_line(report) for report in mine)
        await interaction.followup.send(
            embed=style.embed(
                title=f"{style.CHART} Your reports",
                description=body[:3900],
                color=style.NEUTRAL,
                icon_url=style.avatar_url(self.bot),
            ),
            ephemeral=True,
        )

    async def _do_tell(self, interaction: discord.Interaction) -> None:
        """The private route, and the reason a "complaint" button is not enough.

        "I think this player is harassing me" and "the game is way too hard"
        arrive through the same door and only one of them may ever be public,
        so this door is private for BOTH and a human separates them afterwards.
        The alternative - asking the member to classify their own complaint
        before they type it - puts the privacy decision on the person least
        able to make it and most harmed if it is wrong.
        """
        await interaction.response.send_modal(ComplaintModal(self.bot))

    # -- staff actions -----------------------------------------------------

    async def _do_clock(self, interaction: discord.Interaction) -> None:
        role = discord.utils.get(interaction.guild.roles, name=self.cfg.tester_role_name)
        if role is None:
            await interaction.response.send_message(
                f"Role **{self.cfg.tester_role_name}** not found.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        status, readable = await roster.cohort_status(interaction.guild, role)
        lines = cohort.report_lines(status)
        if not readable:
            lines.append(
                "*Grant dates unavailable: the bot cannot read this audit log. "
                "Give it **View Audit Log** to see day counts.*"
            )
        await interaction.followup.send(
            embed=style.embed(
                title=f"{style.CHART} Closed-test status",
                description="\n".join(lines)[:3900],
                color=style.NEUTRAL,
                icon_url=style.avatar_url(self.bot),
            ),
            ephemeral=True,
        )
        audit.stdout_event(
            "cohort_reported", by=str(interaction.user), roster=status.roster,
            qualified=status.qualified, unknown=status.unknown_dates, via="home",
        )

    async def _do_post(self, interaction: discord.Interaction) -> None:
        embed, picker = build_preset_picker(self.bot, interaction.user)
        picker.message = await safe_followup(
            interaction, embed=embed, view=picker, ephemeral=True
        )

    async def _do_reports(self, interaction: discord.Interaction) -> None:
        """The owner's queue: what came in, and what is waiting to be filed."""
        service = getattr(self.bot, "intake", None)
        if service is None:
            await interaction.response.send_message(
                "Intake is not configured - no state channel.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        reports = await service.all_reports()
        pending = await service.pending_publication()
        waiting = await service.awaiting_approval()
        stuck = await service.stuck()
        lines = [
            f"**{len(reports)}** reports · **{len(waiting)}** waiting for you to "
            f"publish · **{len(pending)}** approved and queued for GitHub"
            + (f" · **{len(stuck)}** stuck" if stuck else ""),
            "",
        ]
        if stuck:
            # Codex, spider-bot#5: a permanent publish failure (a 404 from a
            # tracker the token cannot see, issues disabled, a 422) left both
            # queues, and this panel never asked `stuck()` — so the one report
            # that needed a person was an undifferentiated line among the
            # latest twelve, and then aged out.
            lines.append(
                "**Stuck — a retry can never fix these; the reason is the fix.** "
                "Fix the cause, then `/publish <id>` tries again by hand."
            )
            oldest_first = sorted(stuck, key=lambda r: r.submitted_at)
            lines += [
                f"· {_staff_report_line(r)} — {redact.for_discord(r.publish_failure, limit=80)}"
                for r in oldest_first[:12]
            ]
            if len(oldest_first) > 12:
                # Codex, spider-bot#5 round 2: the newest six left the rest as
                # a bare count. Oldest first, twelve, and the remainder COUNTED
                # — not named; the twelve shown are the ones to act on first,
                # and acting on them surfaces the next. Full pagination is
                # deliberately not built at this server's volume.
                lines.append(f"· … and {len(oldest_first) - 12} more, oldest shown first")
            lines.append("")
        if waiting:
            lines.append(
                "**Waiting for your decision** — `/publish <id>` shows you the "
                "exact issue text before anything is posted"
            )
            lines += [f"· {_staff_report_line(r)}" for r in waiting[:6]]
            lines.append("")
        for report in reports[:12]:
            lines.append(_staff_report_line(report))
        lines += [
            "",
            "*Nothing reaches the public tracker until you publish it, and "
            "`/publish` renders the whole issue body first. The classifier "
            "sorts this list; it decides nothing, and it cannot read every "
            "language this server speaks.*",
        ]
        if pending:
            lines.append(
                "*Approved reports retry automatically — a GitHub outage delays "
                "them, it does not lose them.*"
            )
        await interaction.followup.send(
            embed=style.embed(
                title=f"{style.BUG} Reports",
                description="\n".join(lines)[:3900],
                color=style.NEUTRAL,
                icon_url=style.avatar_url(self.bot),
            ),
            ephemeral=True,
        )
        audit.stdout_event(
            "reports_viewed", by=str(interaction.user), total=len(reports),
            pending=len(pending),
        )

    async def _do_cases(self, interaction: discord.Interaction) -> None:
        """What the classifier decided, and whether a moderator agreed.

        This is the surface shadow mode exists for: without somewhere to read
        the decisions and mark them, a shadow corpus is a log rather than a
        falsification loop.
        """
        service = getattr(self.bot, "moderation", None)
        if service is None:
            await interaction.response.send_message(
                "Moderation is not configured.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        cases = await service.cases(limit=12)
        tally = case_module.review_tally(cases)
        lines = list(service.describe())
        lines += ["", f"**{len(cases)}** recent decisions"]
        if cases:
            would = sum(1 for c in cases if c.would_have_acted)
            lines.append(
                f"· {would} would have acted · {tally['unreviewed']} not yet reviewed"
            )
            lines.append("")
            lines += [c.summary_line() for c in cases]
            lines += [
                "",
                "*Mark a decision with `/case review <id> <verdict>`. Until "
                "decisions are reviewed, there is no evidence for turning any "
                "enforcement class on.*",
            ]
        await interaction.followup.send(
            embed=style.embed(
                title=f"{style.SIREN} Moderation",
                description="\n".join(lines)[:3900],
                color=style.NEUTRAL,
                icon_url=style.avatar_url(self.bot),
            ),
            ephemeral=True,
        )
        audit.stdout_event("cases_viewed", by=str(interaction.user), shown=len(cases))

    async def _do_health(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=style.embed(
                title=f"{style.GEAR} Bot health",
                description="\n".join(health_lines(self.bot)),
                color=style.NEUTRAL,
                icon_url=style.avatar_url(self.bot),
            ),
            ephemeral=True,
        )


class _PresetSelect(discord.ui.Select):
    def __init__(self, panel: PresetPanel) -> None:
        super().__init__(
            placeholder="Pick a message...",
            options=[
                discord.SelectOption(
                    label=p.label, value=p.key, emoji=p.emoji, description=p.purpose[:100]
                )
                for p in presets.PRESETS
            ],
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.panel.preview(interaction, self.values[0])


class PresetPanel(Panel):
    """Choose a canned message; nothing is sent until it is previewed and confirmed."""

    def __init__(self, bot, member, *, back=None) -> None:
        super().__init__(member, back=back)
        self.bot = bot
        self.add_item(_PresetSelect(self))

    async def preview(self, interaction: discord.Interaction, key: str) -> None:
        preset = presets.PRESETS_BY_KEY.get(key)
        if preset is None:
            await interaction.response.send_message("Unknown message.", ephemeral=True)
            return
        text = presets.render(preset, self.bot.cfg)
        where = f"Posts to #{preset.channel}"
        if preset.pings_testers:
            where += f" and pings @{self.bot.cfg.tester_role_name}"
        embed = style.embed(
            title=f"{style.ANNOUNCE} Preview - {preset.label}",
            description=text[:4000],
            color=style.WARNING,
        )
        embed.set_footer(text=f"{where} - {style.FOOTER_BASE}")
        confirm = ConfirmPost(
            self.bot, self.author, preset, back=_rebuild_picker(self.bot)
        )
        # Same message, new view: inherit the handle so the confirm step can
        # expire itself too, instead of leaving a live "Post it" button behind.
        confirm.message = self.message
        await safe_edit(interaction, embed=embed, view=confirm)
        await bind_message(confirm, interaction)


class ConfirmPost(Panel):
    """The last step before anything reaches the server."""

    def __init__(self, bot, member, preset, *, back=None) -> None:
        super().__init__(member, timeout=120, back=back)
        self.bot = bot
        self.preset = preset

    @discord.ui.button(label="Post it", style=discord.ButtonStyle.success, emoji="\N{OUTBOX TRAY}")
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if audience_for(interaction.user, self.bot.cfg) < Audience.MOD:
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        channel = self.bot.channels.get(self.preset.channel)
        if channel is None:
            await interaction.response.edit_message(
                content=f"#{self.preset.channel} was not found - nothing was posted.",
                embed=None, view=None,
            )
            return
        content = presets.render(self.preset, self.bot.cfg)
        mentions = NO_MENTIONS
        role = discord.utils.get(
            getattr(interaction.guild, "roles", ()) or (), name=self.bot.cfg.tester_role_name
        )
        if self.preset.pings_testers and role is not None:
            content = f"{role.mention} {content}"
            mentions = discord.AllowedMentions(
                everyone=False, roles=[role], users=False, replied_user=False
            )
        await channel.send(content[:1900], allowed_mentions=mentions)
        await interaction.response.edit_message(
            content=f"Posted to #{self.preset.channel}.", embed=None, view=None
        )
        await audit.modlog_event(
            self.bot.channels.get("mod-log"),
            "Preset posted",
            f"**{self.preset.label}** posted to #{self.preset.channel} by "
            f"{interaction.user.display_name}"
            + (" (testers pinged)" if self.preset.pings_testers else ""),
            style.SUCCESS,
        )
        audit.stdout_event(
            "preset_posted", preset=self.preset.key, by=str(interaction.user),
            channel=self.preset.channel, pinged=self.preset.pings_testers,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content="Nothing was posted.", embed=None, view=None
        )


def build_home(bot, member, *, public: bool = False) -> tuple[discord.Embed, HomePanel]:
    """The single way to open Home. Every entry point goes through here."""
    audience = audience_for(member, bot.cfg)
    routes = visible_routes(audience)
    panel = HomePanel(bot, member, audience, public=public)
    embed = build_home_embed(
        routes, audience, timeout=panel.timeout, icon_url=style.avatar_url(bot)
    )
    return embed, panel


def build_pinned_home(bot) -> tuple[discord.Embed, HomePanel]:
    """The always-there panel for a public channel.

    Member routes only - a pinned panel is read by everyone, so staff actions
    have no business being on it - and no timeout, so it keeps working after a
    deploy. Authority is still re-checked on every press.
    """
    routes = visible_routes(Audience.EVERYONE)
    panel = HomePanel(
        bot, None, Audience.EVERYONE, public=True, persistent=True
    )
    embed = build_home_embed(
        routes, Audience.EVERYONE, timeout=None, icon_url=style.avatar_url(bot)
    )
    return embed, panel


# -- back targets ------------------------------------------------------------
# A Back press rebuilds its parent from live state rather than restoring a
# snapshot, so authority is re-resolved on the way back too (invariant 15).


def _rebuild_home(bot):
    async def rebuild(interaction):
        return build_home(bot, interaction.user)

    return rebuild


def _rebuild_picker(bot):
    async def rebuild(interaction):
        return build_preset_picker(bot, interaction.user)

    return rebuild


def build_preset_picker(bot, member) -> tuple[discord.Embed, PresetPanel]:
    """The one way to open the preset picker - Home and Back both come here."""
    embed = style.embed(
        title=f"{style.ANNOUNCE} Post a ready-made message",
        description=(
            "Pick one below. You will see exactly what gets posted, and where, "
            "before anything goes out."
        ),
        color=style.NEUTRAL,
        footer=style.panel_footer(Panel.DEFAULT_TIMEOUT),
        icon_url=style.avatar_url(bot),
    )
    return embed, PresetPanel(bot, member, back=_rebuild_home(bot))


# -- the welcome -------------------------------------------------------------

WELCOME_PREFIX = "spiderbot:welcome:"


def build_welcome_panel(bot) -> HomePanel:
    """The one button that matters, carried by the greeting itself.

    Deliberately not a copy of Home: a newcomer gets exactly one next step
    (plan §4, Grok's sequence). It rides on the welcome message rather than
    waiting for a pinned panel, so it works whether or not `/panel` has been
    run - and needs no command, which is the point.

    Persistent custom_ids under their own prefix so the button still works
    after a deploy without colliding with the pinned panel's buttons.
    """
    return HomePanel(
        bot,
        None,
        Audience.EVERYONE,
        public=True,
        persistent=True,
        routes=(ROUTES_BY_KEY["join"],),
        prefix=WELCOME_PREFIX,
    )


def build_welcome(bot, where: str) -> tuple[discord.Embed, HomePanel]:
    """Greeting plus the single next step. Nothing to learn, nothing to type."""
    join = ROUTES_BY_KEY["join"]
    embed = style.embed(
        title=f"{style.WEB} Welcome to the web",
        description=(
            "Slingy Spider is an Android game Menno is building, and it needs "
            "testers before Google will let it launch.\n\n"
            f"**One thing to do:** press **{join.label}** below. It takes about "
            "three minutes, and it is the single most useful thing you can do "
            "here.\n\n"
            f"Everything else lives in {where}. Questions? Just ask in this "
            "channel - a human reads it."
        ),
        color=style.BRAND,
        icon_url=style.avatar_url(bot),
    )
    return embed, build_welcome_panel(bot)
