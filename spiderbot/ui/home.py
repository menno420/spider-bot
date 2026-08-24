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

from spiderbot import audit, cohort, presets, roster, style
from spiderbot.ui.base import Panel, bind_message
from spiderbot.ui.forms import AskModal, BugReportModal, FeedbackModal
from spiderbot.ui.routes import (
    ROUTES_BY_KEY,
    Audience,
    audience_for,
    visible_routes,
)
from spiderbot.ui.safe import safe_edit, safe_followup

log = logging.getLogger("spiderbot.ui.home")

NO_MENTIONS = discord.AllowedMentions.none()


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
    def __init__(self, route, panel: HomePanel, *, persistent: bool = False) -> None:
        super().__init__(
            label=route.label,
            emoji=route.emoji,
            row=route.row,
            # A pinned panel must survive restarts, and Discord matches its
            # buttons back to the view by custom_id - so they must be stable.
            custom_id=f"spiderbot:home:{route.key}" if persistent else None,
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
    ) -> None:
        super().__init__(
            member,
            public=public,
            timeout=None if persistent else Panel.DEFAULT_TIMEOUT,
        )
        self.bot = bot
        self.cfg = bot.cfg
        self.audience = audience
        for route in visible_routes(audience):
            self.add_item(_RouteButton(route, self, persistent=persistent))

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
        picker = PresetPanel(self.bot, interaction.user)
        picker.message = await safe_followup(
            interaction,
            embed=style.embed(
                title=f"{style.ANNOUNCE} Post a ready-made message",
                description=(
                    "Pick one below. You will see exactly what gets posted, and "
                    "where, before anything goes out."
                ),
                color=style.NEUTRAL,
            ),
            view=picker,
            ephemeral=True,
        )

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

    def __init__(self, bot, member) -> None:
        super().__init__(member)
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
        confirm = ConfirmPost(self.bot, self.author, preset)
        # Same message, new view: inherit the handle so the confirm step can
        # expire itself too, instead of leaving a live "Post it" button behind.
        confirm.message = self.message
        await safe_edit(interaction, embed=embed, view=confirm)
        await bind_message(confirm, interaction)


class ConfirmPost(Panel):
    """The last step before anything reaches the server."""

    def __init__(self, bot, member, preset) -> None:
        super().__init__(member, timeout=120)
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
            mentions = discord.AllowedMentions(roles=[role])
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
