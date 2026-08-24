"""Discord's hard limits, enforced in one place.

Ported from superbot `disbot/core/runtime/interaction_helpers.py` (read via gh
2026-08-24), trimmed: the donor's `help_ctx_shim` has no analogue here (our
panels take their dependencies directly), and its `file`/`attachments` plumbing
is dropped because the plan rules out rendered image cards.

Why this exists: one component over its limit - most often a field `value` past
1024 chars - makes Discord reject the *whole message* with 400 Invalid Form
Body. Inside a panel edit the edit never lands and the panel silently freezes
with no error anywhere. The donor hit that twice in production; one incident
meant a panel's Back button never rendered. Clamping degrades an oversized
payload to a truncated-but-rendered panel instead of a hard failure.

Nothing here raises: every helper reports success as a return value, because a
panel callback must never take the gateway down with it.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

log = logging.getLogger("spiderbot.ui.safe")

# https://discord.com/developers/docs/resources/message#embed-object-embed-limits
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
FIELD_NAME_LIMIT = 256
FIELD_VALUE_LIMIT = 1024
FOOTER_LIMIT = 2048
AUTHOR_LIMIT = 256
MAX_FIELDS = 25
# Discord also caps the SUM of title + description + every field name/value +
# footer + author at 6000. An embed can pass every per-component check above
# and still be rejected whole on this total.
TOTAL_LIMIT = 6000


def clip(text: str, limit: int) -> str:
    """Truncate to `limit` characters, ellipsis included in the count."""
    return text if len(text) <= limit else text[: limit - 1] + "\N{HORIZONTAL ELLIPSIS}"


def embed_length(embed: discord.Embed) -> int:
    """Sum of every string Discord counts toward the 6000-character budget."""
    total = len(embed.title or "") + len(embed.description or "")
    for field in embed.fields:
        total += len(field.name or "") + len(field.value or "")
    total += len(embed.footer.text or "")
    total += len(embed.author.name or "")
    return total


def clamp_embed(embed: discord.Embed) -> discord.Embed:
    """Truncate an embed in place to Discord's limits. Returns the same object.

    Only mutates what actually overflows, so a well-formed embed passes through
    untouched. Order matters: per-component first, then the field-count cap,
    then the 6000 total - trailing fields go before the description, because a
    truncated detail row costs less than a truncated explanation.
    """
    if not isinstance(embed, discord.Embed):
        return embed  # a test double, or a caller mistake: never choke on it

    if embed.title and len(embed.title) > TITLE_LIMIT:
        embed.title = clip(embed.title, TITLE_LIMIT)
    if embed.description and len(embed.description) > DESCRIPTION_LIMIT:
        embed.description = clip(embed.description, DESCRIPTION_LIMIT)

    for idx, field in enumerate(embed.fields):
        name, value = field.name or "", field.value or ""
        if len(name) > FIELD_NAME_LIMIT or len(value) > FIELD_VALUE_LIMIT:
            embed.set_field_at(
                idx,
                name=clip(name, FIELD_NAME_LIMIT),
                value=clip(value, FIELD_VALUE_LIMIT),
                inline=field.inline,
            )

    while len(embed.fields) > MAX_FIELDS:
        embed.remove_field(MAX_FIELDS)

    footer = embed.footer
    if footer.text and len(footer.text) > FOOTER_LIMIT:
        embed.set_footer(text=clip(footer.text, FOOTER_LIMIT), icon_url=footer.icon_url)

    author = embed.author
    if author.name and len(author.name) > AUTHOR_LIMIT:
        embed.set_author(
            name=clip(author.name, AUTHOR_LIMIT), url=author.url, icon_url=author.icon_url
        )

    if embed_length(embed) > TOTAL_LIMIT:
        while embed.fields and embed_length(embed) > TOTAL_LIMIT:
            embed.remove_field(len(embed.fields) - 1)
        if embed.description and embed_length(embed) > TOTAL_LIMIT:
            over = embed_length(embed) - TOTAL_LIMIT
            embed.description = clip(embed.description, max(1, len(embed.description) - over))
    return embed


async def safe_defer(
    interaction: discord.Interaction, *, ephemeral: bool = False, thinking: bool = False
) -> bool:
    """Defer inside Discord's 3-second window, idempotently.

    Returns False only when the token is already dead - in which case every
    later followup will fail too, so the caller should bail out.
    """
    if interaction.response.is_done():
        return True
    try:
        await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)
        return True
    except discord.NotFound:  # 10062 Unknown Interaction - token expired
        log.warning("safe_defer: token expired (user=%s)", getattr(interaction.user, "id", None))
        return False
    except discord.HTTPException as exc:
        log.warning("safe_defer: HTTP error %s", exc)
        return False


async def safe_followup(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    ephemeral: bool = False,
) -> discord.Message | None:
    """Say something back, whether or not the interaction was deferred.

    Returns the sent message so the caller can bind it to a panel's `message`
    (which is what makes `on_timeout` able to grey the buttons out), or None if
    delivery failed.
    """
    kwargs: dict[str, Any] = {}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = clamp_embed(embed)
    if view is not None:
        kwargs["view"] = view
    if ephemeral:
        kwargs["ephemeral"] = True
    try:
        if interaction.response.is_done():
            return await interaction.followup.send(**kwargs)
        await interaction.response.send_message(**kwargs)
        try:
            return await interaction.original_response()
        except discord.HTTPException:
            return None
    except discord.NotFound:
        log.warning("safe_followup: token expired (user=%s)", getattr(interaction.user, "id", None))
        return None
    except discord.HTTPException as exc:
        log.warning("safe_followup: HTTP error %s", exc)
        return None


async def safe_edit(
    interaction: discord.Interaction,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
) -> bool:
    """Edit the panel in place, whether or not the interaction was deferred.

    Panels edit in place and never leave a trail of dead messages (the plan's
    embed rules), so this is the only way a panel should change what it shows.
    """
    kwargs: dict[str, Any] = {}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = clamp_embed(embed)
    if view is not None:
        kwargs["view"] = view
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(**kwargs)
        else:
            await interaction.response.edit_message(**kwargs)
        return True
    except discord.NotFound:
        log.warning("safe_edit: message gone (user=%s)", getattr(interaction.user, "id", None))
        return False
    except discord.HTTPException as exc:
        log.warning("safe_edit: HTTP error %s", exc)
        return False
