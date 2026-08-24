"""Panel base classes - the interaction-lifecycle contract every panel obeys.

Ported from superbot `disbot/views/base.py` (BaseView / HubView), restructured:
superbot resolves standard nav by importing `views.navigation` *inside*
`__init__` to dodge a circular import, and carries panel metadata in untyped
dicts. Here nav is data (`spiderbot/ui/routes.py`) handed to the panel, so the
layering stays one-directional:

    cogs -> ui -> (presets, cohort, config)      ui never imports cogs.

Rules kept from the donor, verbatim in intent:
- a panel belongs to the person who opened it unless it is explicitly public;
- timing out disables the buttons, it never strips the view off the message;
- authority is re-checked when a button is pressed, never trusted from the
  moment the panel was opened;
- a failing callback logs with context and shows one generic ephemeral.
"""

from __future__ import annotations

import logging

import discord

log = logging.getLogger("spiderbot.ui")

# Discord renders at most five components per action row.
BUTTONS_PER_ROW = 5


async def handle_panel_error(
    panel: discord.ui.View,
    interaction: discord.Interaction,
    error: Exception,
    item: discord.ui.Item,
) -> None:
    """Standard panel error handler: context-rich log, one generic ephemeral.

    `response_done` separates "raised before we answered Discord" from "raised
    after a defer" - the first is a missing defer, the second a real bug.
    """
    response_done = interaction.response.is_done()
    log.error(
        "panel error | panel=%s item=%s custom_id=%r label=%r user=%s channel=%s done=%s",
        type(panel).__name__,
        type(item).__name__,
        getattr(item, "custom_id", None),
        getattr(item, "label", None),
        getattr(interaction.user, "id", None),
        interaction.channel_id,
        response_done,
        exc_info=error,
    )
    if response_done:
        return
    try:
        await interaction.response.send_message(
            "Something went wrong with that button. Try again, or ask Menno.",
            ephemeral=True,
        )
    except discord.HTTPException:  # the panel must never raise into the gateway
        log.debug("panel error notice could not be delivered")


async def bind_message(panel: Panel, interaction: discord.Interaction) -> None:
    """Give a panel the message handle its own `on_timeout` needs.

    Without this `on_timeout` hits `self.message is None` and returns, so the
    buttons stay clickable forever and do nothing - a dead surface the user
    cannot tell from a live one. `/home` bound it from the start; the panels
    underneath it did not, which is the defect this closes.

    Ephemeral messages can only be edited through the interaction token, so the
    handle must come from `original_response()`, never `interaction.message`.
    """
    if panel.message is not None:
        return
    try:
        panel.message = await interaction.original_response()
    except (discord.HTTPException, AttributeError) as exc:
        # Best-effort: an unbound panel still works, it just cannot grey out.
        log.debug("%s could not bind its message: %s", type(panel).__name__, exc)


class BackButton(discord.ui.Button):
    """One step back, rebuilt at click time - never a replayed snapshot.

    `rebuild` is an async callable taking the interaction and returning the
    parent's `(embed, panel)`. Rebuilding rather than restoring is what keeps
    a Back press honest: the parent re-resolves the presser's standing from
    live Discord state, so going back can never hand someone a panel built
    for the authority they had a minute ago.

    No emoji: the locked vocabulary has no back arrow, and inventing a
    twelfth is how a closed set stops being closed.
    """

    def __init__(self, rebuild, *, row: int = 4) -> None:
        super().__init__(label="Back", style=discord.ButtonStyle.secondary, row=row)
        self.rebuild = rebuild

    async def callback(self, interaction: discord.Interaction) -> None:
        from spiderbot.ui.safe import safe_edit

        embed, panel = await self.rebuild(interaction)
        # Same Discord message, new view: carry the handle across so the
        # rebuilt panel can still expire itself.
        panel.message = getattr(self.view, "message", None)
        await safe_edit(interaction, embed=embed, view=panel)
        await bind_message(panel, interaction)


class Panel(discord.ui.View):
    """Base for every Spider Bot panel.

    `public=True` lets anyone press the buttons (for a panel pinned in a
    channel); the default locks the panel to whoever opened it.
    """

    DEFAULT_TIMEOUT = 180
    EXPIRED_NOTICE = (
        "This panel expired - open a new one with `/home`, or use the pinned panel."
    )

    def __init__(
        self,
        author: discord.abc.User | None,
        *,
        public: bool = False,
        timeout: float | None = DEFAULT_TIMEOUT,
        back=None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.author = author
        self.public = public
        self.message: discord.Message | None = None
        self.back = back
        if back is not None:
            # Row 4 keeps Back at the bottom whatever a subclass adds after us.
            self.add_item(BackButton(back))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.public or self.author is None:
            return True
        if interaction.user.id == self.author.id:
            return True
        await interaction.response.send_message(
            "That panel belongs to someone else - open your own with `/home`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is None:
            return
        try:
            await self.message.edit(content=self.EXPIRED_NOTICE, view=self)
        except discord.HTTPException as exc:  # message deleted, perms changed
            log.debug("%s.on_timeout could not disable: %s", type(self).__name__, exc)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        await handle_panel_error(self, interaction, error, item)


class PinnedPanel(Panel):
    """A panel meant to live in a channel forever.

    No timeout and no invoker lock, so every member can press it, and the
    buttons still work after a restart - which means every callback must
    rebuild its state from live Discord data rather than from anything the
    panel captured when it was created.
    """

    def __init__(self) -> None:
        super().__init__(None, public=True, timeout=None)
