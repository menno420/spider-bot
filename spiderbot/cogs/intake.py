"""Conversational filing: someone says they found a bug, and it gets written down.

The owner's direction is that people should be able to *"talk naturally"* to
Spider Bot about bugs, feedback, complaints and ideas — not learn a command or a
form name. This cog is that route, and it goes through the same
`IntakeService` as every button, so there is one implementation and many doors.

**The sequence is the brief's own, and every step of it is deliberate:**

1. someone writes something that reads like a report;
2. the bot offers, once, quietly, in the channel;
3. it SHOWS the structured summary it intends to save, before saving anything;
4. they press **Save it** or **No thanks**;
5. only then does it file, and it hands back the durable reference.

**The draft survives a deploy.** Push to `main` deploys straight to production
here, so a draft held in a view's memory would evaporate mid-conversation and
the button would go dead. Drafts are written to the store and the confirm
button is a `discord.ui.DynamicItem` carrying the draft id in its `custom_id`,
so it is reconstructed from the id on click — which is also why the handler
re-resolves who is pressing rather than trusting the panel.

**The offer never nags.** One offer per person per cooldown, only in the
channels initiative is already allow-listed for, and never when the person is
already talking to a human about it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

from spiderbot import audit, evidence, ids, redact, style
from spiderbot.intake.models import Category, Reporter
from spiderbot.ui.base import Panel
from spiderbot.ui.forms import (
    BotProblemModal,
    BugReportModal,
    ComplaintModal,
    FeedbackModal,
    IdeaModal,
)
from spiderbot.ui.safe import safe_defer, safe_edit, safe_followup

log = logging.getLogger("spiderbot.cogs.intake")

DRAFTS = "intake_drafts"

#: Phrases that read like someone reporting something, mapped to the category
#: they most likely mean. Deterministic and deliberately narrow: this decides
#: only whether to OFFER, and the person decides everything after that. A false
#: offer costs one dismissable message; a missed one costs a lost report, so
#: the bar is set low on purpose.
#: A problem with the bot itself, said in chat. Two patterns that must BOTH
#: match — the subject is the bot, and something is wrong — checked before
#: `SIGNALS`, because the first bug rule matches "doesn't work" on its own and
#: would offer a game bug for "the Spider Bot button doesn't work". Codex,
#: spider-bot#5: that draft, once approved, could only ever be published to the
#: game's tracker — the cross-tracker path the routing exists to close. This
#: still decides only what is OFFERED; the member confirms the category, and
#: `Report.target` reads the category, never the text.
BOT_SUBJECT = re.compile(
    # `(?<![\w/])` rather than `\b`: a word boundary cannot precede a slash,
    # so `\b/tester` never matched a command name at the start of a message.
    # `[ -]?`: "spider-bot" is how people type it too. Gemini (free-key review
    # of round 2, 2026-09-04): the hyphenated form matched neither this nor
    # the game exclusion's lookahead, so "the spider-bot is broken" read as a
    # game bug.
    r"(?<![\w/])(spider[ -]?bot|the bot|bot'?s|/(?:home|report|publish|jointest|tester)|"
    r"the panel|home panel)(?!\w)",
    re.IGNORECASE,
)
BOT_TROUBLE = re.compile(
    r"\b(does(?:n'?t| not) work|didn'?t work|isn'?t working|not working|broken|"
    r"nothing happen(?:s|ed)|no response|not respond(?:ing)?|ignores? me|"
    r"error|failed|timed out|stuck|crash(?:es|ed|ing)?|froze|freezes|frozen|"
    r"hangs?|hung|glitch(?:ed|ing|es)?)\b",
    re.IGNORECASE,
)
#: The game, named. Inside one clause the game outranks the bot: "the bot said
#: the game is broken" is about the game.
GAME_SUBJECT = re.compile(
    r"\b(the game|the app|slingy spider|the level|the bird|the silk|the web|"
    r"the spider(?![ -]?bot))\b",
    re.IGNORECASE,
)
#: Where one thought ends and the next begins, for the purpose above. Codex,
#: spider-bot#5 round 2: two searches over the whole message let "I asked the
#: bot for help because the game doesn't work anymore" read as a bot problem —
#: the subject and the trouble were in different clauses.
CLAUSE_BREAK = re.compile(
    r"[.!?;\n]|\b(?:because|but|although|though|while|so that)\b", re.IGNORECASE
)


def _reads_as_bot_problem(text: str) -> bool:
    """A clause that names the bot, names trouble, and does not name the game."""
    return any(
        BOT_SUBJECT.search(clause)
        and BOT_TROUBLE.search(clause)
        and not GAME_SUBJECT.search(clause)
        for clause in CLAUSE_BREAK.split(text)
    )

SIGNALS: tuple[tuple[re.Pattern[str], Category], ...] = (
    (
        re.compile(
            r"\b(found a bug|is a bug|it crashed|game crashed|it froze|game froze|"
            r"crashes when|freezes when|stuck in|fell through|glitch(?:ed|ing)?|"
            r"does(?:n'?t| not) work|didn'?t work|broken)\b",
            re.IGNORECASE,
        ),
        Category.BUG,
    ),
    (
        re.compile(
            r"\b(can'?t (?:install|join|opt|get)|app not available|"
            r"not available (?:for|on)|won'?t install|opt[ -]?in .*(?:fail|not work)|"
            r"google group)\b",
            re.IGNORECASE,
        ),
        Category.TESTING_PROBLEM,
    ),
    (
        re.compile(
            r"\b(would be (?:better|nice|cool) if|you should add|it needs a|"
            r"idea[: ]|suggestion[: ]|what if the|why not (?:add|make))\b",
            re.IGNORECASE,
        ),
        Category.IDEA,
    ),
    (
        re.compile(
            r"\b(feels? too (?:weak|slow|fast|hard|easy)|way too (?:hard|difficult)|"
            r"too difficult|impossible (?:around|after|at)|keeps? killing me|"
            r"unfair|(?:feels|feel) (?:floaty|sluggish|off))\b",
            re.IGNORECASE,
        ),
        Category.GAMEPLAY_FEEDBACK,
    ),
)

#: How much of the issue body goes in one preview message. Discord's embed
#: description limit is 4096; the rest is a margin for the heading.
PREVIEW_PAGE = 3500

#: One lock per draft id, minted on demand. Process-local, which is right: a
#: draft belongs to one member and one panel, and two presses of the same
#: button reach the same process.
_DRAFT_LOCKS: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _draft_lock(draft_id: str) -> asyncio.Lock:
    return _DRAFT_LOCKS[draft_id]


OFFER_COOLDOWN_S = 900
MIN_LENGTH = 25



def detect(text: str) -> Category | None:
    """The likely category, or None. Deterministic; no model call."""
    if len(text.strip()) < MIN_LENGTH:
        return None
    if _reads_as_bot_problem(text):
        return Category.BOT_PROBLEM
    for pattern, category in SIGNALS:
        if pattern.search(text):
            return category
    return None


def summarise(text: str) -> tuple[str, str]:
    """A title and a description from what they actually wrote.

    Their words, not a paraphrase: the summary shown before saving has to be
    something they can recognise and correct, and a generated title they never
    said is harder to check than a trimmed version of their own sentence.
    """
    cleaned = " ".join(text.split())
    first = re.split(r"(?<=[.!?])\s+", cleaned)[0]
    title = first if len(first) <= 90 else first[:87] + "..."
    return title, cleaned


class ConfirmFiling(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"sbintake:(?P<draft>[A-Z0-9-]{8,64})",
):
    """Save it. Persistent, so it still works after a deploy.

    `DynamicItem` reconstructs this from the `custom_id` on click, so the draft
    is read from the store rather than from anything the view was holding when
    it was created.
    """

    def __init__(self, draft_id: str) -> None:
        self.draft_id = draft_id
        super().__init__(
            discord.ui.Button(
                label="Save it",
                style=discord.ButtonStyle.success,
                emoji=style.OK,
                custom_id=f"sbintake:{draft_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["draft"])

    async def callback(self, interaction: discord.Interaction) -> None:
        # Defer FIRST. This button is a `DynamicItem` precisely so it survives
        # a deploy — and after a deploy the store index is cold, so the very
        # first press pays a 2000-message history scan before anything touches
        # the interaction. Discord kills the token at 3 seconds; the member
        # then sees "This interaction failed" on a button that is working.
        if not await safe_defer(interaction, ephemeral=True):
            return
        bot = interaction.client
        service = getattr(bot, "intake", None)
        backing = getattr(service, "_store", None)
        if service is None or backing is None:
            await safe_followup(interaction, "I cannot save that right now.", ephemeral=True)
            return
        draft = await backing.get(DRAFTS, self.draft_id)
        if draft is None:
            await safe_followup(
                interaction,
                "That offer has expired - tell me again and I will write it down.",
                ephemeral=True,
            )
            return
        # Authority is re-resolved at press time: the draft names whose report
        # this is, and only they may save it. A panel in a public channel is
        # pressable by anyone, so this is not optional.
        if draft.get("user_id") != interaction.user.id:
            await safe_followup(
                interaction, "That is someone else's report.", ephemeral=True
            )
            return
        # The draft is consumed, not just read. This item is rebuilt from its
        # custom_id on every press, so any second click that reaches the
        # gateway before the edit lands - a double tap, a stale client, a
        # flaky connection - would otherwise file the same report twice.
        # Serialised per draft. Codex, spider-bot#3, 2026-09-04: the consume
        # check below handles a SEQUENTIAL retry, and a real double-click sends
        # two interactions that both read the draft before either writes — so
        # both saw no `filed_report_id` and both filed. The lock makes the
        # read-check-write one step; the check inside it then answers both.
        async with _draft_lock(self.draft_id):
            await self._file_once(interaction, service, backing, draft, bot)

    async def _file_once(self, interaction, service, backing, draft, bot) -> None:
        draft = await backing.get(DRAFTS, self.draft_id) or draft
        already = draft.get("filed_report_id")
        if already and await service.get(already) is not None:
            await safe_followup(
                interaction, f"Already saved as `{already}`.", ephemeral=True
            )
            return
        if already:
            # Claimed, but the report is not there: the process stopped between
            # the claim and the file, or the report write failed. Codex,
            # spider-bot#3, 2026-09-04: the claim was treated as proof that
            # filing had completed, so this window LOST the report and told the
            # member it was saved. The claim is a reservation, not a receipt —
            # resume it under the same id, which is what makes the id worth
            # claiming in the first place.
            log.warning("intake: resuming interrupted filing of %s", already)
        try:
            category = Category(draft.get("category", "general"))
        except ValueError:
            category = Category.GENERAL
        # The id is minted and CLAIMED here, before the report exists. Codex,
        # spider-bot#3, 2026-09-04: the lock closes concurrent presses only
        # while this process lives, and the marker was written AFTER filing —
        # so a restart in between, or a failed marker write, left the button
        # pointing at an unconsumed draft whose next press filed a second
        # report. Claiming first makes the draft the record of intent: a
        # restart finds it already claimed, and the id is stable, so filing
        # again would overwrite one report rather than mint two.
        report_id = already or ids.report_id()
        # A resume already holds the claim — re-writing an identical record
        # costs a Discord message against the store's fixed history for nothing.
        claimed = already == report_id or await backing.append(
            DRAFTS, self.draft_id, {**draft, "filed_report_id": report_id}
        )
        if not claimed:
            await safe_followup(
                interaction,
                "I could not write that down just now — try again in a minute, "
                "or use the buttons on `/home`.",
                ephemeral=True,
            )
            return
        outcome = await service.file(
            report_id=report_id,
            category=category,
            title=str(draft.get("title", ""))[:120],
            description=str(draft.get("description", ""))[:4000],
            reporter=Reporter(
                user_id=interaction.user.id,
                display_name=getattr(interaction.user, "display_name", ""),
                channel_id=draft.get("channel_id"),
                message_id=draft.get("message_id"),
            ),
            correlation_id=str(draft.get("correlation_id", "")),
            evidence_summary=tuple(
                str(x) for x in (draft.get("evidence_summary") or [])
            ),
            evidence_format=str(draft.get("evidence_format", "")),
            # The offer panel says it before the member presses Save it.
            reporter_cleared=True,
        )
        await safe_edit(
            interaction,
            content=None,
            embed=style.embed(
                title=f"{style.OK} Written down",
                description=outcome.reporter_message,
                color=style.SUCCESS if outcome.ok else style.ALARM,
                icon_url=style.avatar_url(bot),
            ),
            view=None,
        )
        # Deliberately NOT published here. It cannot be — `may_publish` needs a
        # named human approver — and that is the point: the conversational path
        # is the easiest one for a member to steer, so it is the last one that
        # should reach a public tracker unattended.


class DismissFiling(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"sbnothanks:(?P<draft>[A-Z0-9-]{8,64})",
):
    """No thanks. Also persistent, so a stale offer is never a dead button.

    Ownership is checked here for exactly the reason it is checked on the
    Save button: the offer is posted in a PUBLIC channel and every button on
    it is pressable by every member. `MEASURED` 2026-09-04: without this,
    any member could press "No thanks" on somebody else's crash report and
    the offer was edited away for everyone, with nothing recorded and no way
    for the reporter to tell it had happened.
    """

    def __init__(self, draft_id: str) -> None:
        self.draft_id = draft_id
        super().__init__(
            discord.ui.Button(
                label="No thanks",
                style=discord.ButtonStyle.secondary,
                custom_id=f"sbnothanks:{draft_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["draft"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        backing = getattr(getattr(interaction.client, "intake", None), "_store", None)
        draft = await backing.get(DRAFTS, self.draft_id) if backing else None
        if draft is None and backing is not None and not await _store_readable(backing):
            # `get` returns None for BOTH "no such draft" and "the store could
            # not be read". Codex, spider-bot#3, 2026-09-04: during a transient
            # read failure — a cold-scan timeout after a restart — another
            # member pressing this public button passed the ownership check and
            # removed someone else's live offer. When absence cannot be
            # distinguished from unavailability, refuse.
            await safe_followup(
                interaction,
                "I cannot check whose report that is right now - try again in a "
                "moment.",
                ephemeral=True,
            )
            return
        # A draft that has genuinely expired out of a READABLE store is nobody's
        # to protect and the button is dead anyway, so it stays dismissible: the
        # failure direction is a stale panel someone can tidy, not a live report
        # someone else can silence.
        if draft is not None and draft.get("user_id") != interaction.user.id:
            await safe_followup(
                interaction, "That is someone else's report.", ephemeral=True
            )
            return
        await safe_edit(interaction, content="No problem.", embed=None, view=None)
        audit.stdout_event("intake_offer_declined", user=str(interaction.user))


async def _store_readable(backing) -> bool:
    """Whether the store can answer at all right now.

    `Store.get` returns None for a missing key and for a failed read alike, so
    a caller that must tell those apart asks the store to load a collection: a
    healthy store returns a dict (possibly empty) and an unreachable one is
    caught by `DiscordChannelStore` itself and comes back empty after logging.
    The distinguishing signal is `available` plus a successful index build.
    """
    if not getattr(backing, "available", True):
        return False
    ensure = getattr(backing, "_ensure_index", None)
    if ensure is None:
        return True  # an in-memory store is always readable
    try:
        return bool(await ensure())
    except Exception:  # noqa: BLE001 — a probe must never break a callback
        return False


async def read_evidence(message) -> tuple[tuple[str, ...], str]:
    """A run-evidence export attached to this message, reduced to summary lines.

    The entry point the evidence reader never had. Codex, spider-bot#3,
    2026-09-04: `evidence.parse` had no production caller at all — only tests,
    the docs and the synthetic walkthrough — so the documented journey where a
    tester attaches an export could not happen, and the JSON would have been
    stored as ordinary truncated prose.

    Bounded before it is read: the attachment's declared size is checked
    against `evidence.MAX_BYTES` first, so an oversized file costs a comparison
    rather than a download. Never raises: an unreadable attachment is simply
    not evidence, and the report is still filed without it.
    """
    for attachment in getattr(message, "attachments", ()) or ():
        name = (getattr(attachment, "filename", "") or "").lower()
        if not name.endswith(".json"):
            continue
        if (getattr(attachment, "size", 0) or 0) > evidence.MAX_BYTES:
            log.info("intake: evidence attachment %s is too large to read", name)
            continue
        try:
            raw = await attachment.read()
        except Exception:  # noqa: BLE001 — a download failure must not lose the report
            log.info("intake: could not read evidence attachment %s", name)
            continue
        result = evidence.parse(raw)
        if not result.ok:
            log.info("intake: attachment %s is not run evidence: %s", name, result.reason)
            continue
        return tuple(result.summary_lines(redact.for_github)), evidence.SUPPORTED_FORMAT
    return (), ""


def build_offer(bot, draft_id: str, category: Category, title: str, description: str):
    """The panel that shows what would be saved, before anything is saved."""
    from spiderbot.intake.models import CATEGORY_LABELS

    # Codex, spider-bot#5 round 2: this said "the game's public issue tracker"
    # for every category, and pressing Save it recorded consent for a
    # destination a bot-problem reporter was never told about.
    tracker = (
        "Spider Bot's own public issue tracker"
        if category is Category.BOT_PROBLEM
        else "the game's public issue tracker"
    )
    embed = style.embed(
        title=f"{style.SPEECH} Want me to write that down?",
        description=(
            f"I would save this as **{CATEGORY_LABELS[category]}**:\n\n"
            f"> {style.escape_name(title)}\n\n"
            f"Menno reads these, and he may put it on {tracker} "
            "— never your name or anything private, and never until he "
            "presses publish himself. If I have got it wrong, press "
            "**No thanks** and use the buttons on `/home` instead."
        ),
        color=style.BRAND,
        icon_url=style.avatar_url(bot),
    )
    view = discord.ui.View(timeout=None)
    view.add_item(ConfirmFiling(draft_id))
    view.add_item(DismissFiling(draft_id))
    return embed, view


def body_digest(report) -> str:
    """A digest of the exact bytes that would be published."""
    return hashlib.sha256(report.public_body().encode("utf-8")).hexdigest()


class PublishPreview(Panel):
    """The exact issue, shown to the approver before it exists.

    **Why this is a panel and not a one-line command.** Publication used to be
    a keyword classifier: "no signal found" meant "safe", the vocabulary was
    English on a Dutch server, and a plain complaint naming a member published
    verbatim. The fix was to require a named human — and an adversarial review
    then pointed out what that fix had actually bought: `/publish` approved by
    report ID and the staff queue showed a 60-character title, so it replaced a
    classifier publishing unseen content with a PERSON publishing unseen
    content. Every obfuscation attack survives that, because the approver was
    never shown the thing under attack.

    So the approver reads `public_title()` and `public_body()` — the real
    strings, rendered exactly as the issue will carry them — and then presses.
    Authority and publishability are re-resolved at press time, not trusted
    from when the panel was built.
    """

    def __init__(self, author, report_id: str, digest: str) -> None:
        super().__init__(author, timeout=180)
        self.report_id = report_id
        #: SHA-256 of the exact body shown to this approver. Codex,
        #: spider-bot#3, 2026-09-04: the preview was truncated at 3,000
        #: characters and the button published the whole body, so
        #: member-controlled text in the tail reached GitHub unread — the same
        #: defect as approving by id, one layer in. The body is now shown in
        #: full across as many messages as it needs, and the press is refused
        #: if what would be published is not what was read.
        self.digest = digest

    @discord.ui.button(label="Publish it", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        service = getattr(interaction.client, "intake", None)
        if service is None:
            await safe_followup(interaction, "Intake is not configured.", ephemeral=True)
            return
        report = await service.get(self.report_id)
        # Re-checked here, not carried from the preview: the report may have
        # been published, resolved or reclassified since the panel was drawn.
        if report is None or not report.is_public_safe:
            await safe_edit(
                interaction,
                content=f"`{self.report_id}` is no longer publishable.",
                embed=None,
                view=None,
            )
            return
        if body_digest(report) != self.digest:
            await safe_edit(
                interaction,
                content=(
                    f"`{self.report_id}` has changed since you read it. "
                    "Run `/publish` again to see the current text."
                ),
                embed=None,
                view=None,
            )
            return
        approved = await service.approve(report.id, by=str(interaction.user))
        result = await service.publish(approved.id)
        for item in self.children:
            item.disabled = True
        await safe_edit(
            interaction,
            content=result.reporter_message or f"Could not publish: {result.failure}",
            embed=None,
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Keep it private", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button) -> None:
        await safe_edit(
            interaction,
            content=f"`{self.report_id}` was not published.",
            embed=None,
            view=None,
        )
        self.stop()


class IntakeCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.cfg = bot.cfg
        self._last_offer: dict[int, float] = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Offer to write it down. Never files anything on its own."""
        service = getattr(self.bot, "intake", None)
        if service is None or message.guild is None or message.author.bot:
            return
        channel_name = getattr(message.channel, "name", "") or ""
        # Same allow-list as AI initiative: unconfigured is silent, and a
        # channel the owner did not name is not a channel to speak in.
        if channel_name not in self.cfg.initiative_channels:
            return
        content = message.content or ""
        if content.startswith(("/", "!")):
            return
        category = detect(content)
        if category is None:
            return
        now = time.time()
        if now - self._last_offer.get(message.author.id, 0.0) < OFFER_COOLDOWN_S:
            return
        # Armed HERE, before the durable write and before the reply — not after
        # a successful delivery. `MEASURED` 2026-09-04: arming it last meant a
        # channel where the bot cannot post (no Send Messages, no Embed Links —
        # an ordinary configuration) never armed it at all, so every single
        # message from one member wrote another draft into the shared store
        # channel. 2000 messages produced 2000 store writes and zero offers,
        # and the store is read to a fixed horizon on a cold start, so those
        # writes push real reports out of every panel. The cooldown protects
        # the store, so it must not depend on the part that can fail.
        self._last_offer[message.author.id] = now

        title, description = summarise(content)
        evidence_summary, evidence_format = await read_evidence(message)
        draft_id = ids.report_id()
        backing = getattr(service, "_store", None)
        stored = backing is not None and await backing.append(
            DRAFTS,
            draft_id,
            {
                "user_id": message.author.id,
                "category": str(category),
                "title": title,
                "description": description,
                "channel_id": message.channel.id,
                "message_id": message.id,
                "correlation_id": ids.correlation_id(),
                "created_at": now,
                "evidence_summary": list(evidence_summary),
                "evidence_format": evidence_format,
            },
        )
        if not stored:
            return  # no durable draft, no offer: the button would be dead

        embed, view = build_offer(self.bot, draft_id, category, title, description)
        try:
            await message.reply(
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
                mention_author=False,
            )
        except discord.HTTPException:
            log.debug("intake offer could not be delivered in #%s", channel_name)
            return
        audit.stdout_event(
            "intake_offered",
            user=str(message.author),
            category=str(category),
            draft=draft_id,
            channel=channel_name,
        )

    # -- staff commands ------------------------------------------------------

    @app_commands.command(name="report", description="Report a bug, an idea, or a problem")
    @app_commands.describe(kind="What kind of report is this?")
    @app_commands.choices(
        kind=[
            app_commands.Choice(name="a bug", value="bug"),
            app_commands.Choice(name="an idea", value="idea"),
            app_commands.Choice(name="how the game feels", value="gameplay_feedback"),
            app_commands.Choice(name="a problem with Spider Bot", value="bot_problem"),
            app_commands.Choice(name="something private", value="complaint"),
        ]
    )
    async def report(
        self, interaction: discord.Interaction, kind: app_commands.Choice[str]
    ) -> None:
        modal = {
            "bug": BugReportModal,
            "idea": IdeaModal,
            "gameplay_feedback": FeedbackModal,
            "bot_problem": BotProblemModal,
            "complaint": ComplaintModal,
        }[kind.value]
        await interaction.response.send_modal(modal(self.bot))

    @app_commands.command(
        name="publish", description="Put a saved report on the game's issue tracker"
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(report_id="The reference, e.g. SB-R-...")
    async def publish(self, interaction: discord.Interaction, report_id: str) -> None:
        """The publication gate, and it is a person WHO HAS READ THE TEXT.

        A keyword classifier cannot be the last thing between a member's words
        and a public page — it could not read this server's own language. So
        the classifier sorts and a human decides. **And the human is shown what
        they are deciding about**: this command used to approve by report id
        and publish in the same breath, which meant every obfuscation attack
        the classifier missed sailed past the human too, because the human was
        never shown the body either.
        """
        service = getattr(self.bot, "intake", None)
        if service is None:
            await interaction.response.send_message(
                "Intake is not configured.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        report = await service.get(report_id.strip())
        if report is None:
            await interaction.followup.send(
                f"No report `{redact.one_line(report_id, limit=40)}`.", ephemeral=True
            )
            return
        if not report.is_public_safe:
            await interaction.followup.send(
                f"`{report.id}` is marked **private** and will not be published.\n"
                f"Why: {report.sensitivity_reason}",
                ephemeral=True,
            )
            return
        if report.github_issue_number is not None:
            await interaction.followup.send(
                f"`{report.id}` is already on the tracker: "
                f"{report.github_issue_url or f'#{report.github_issue_number}'}",
                ephemeral=True,
            )
            return
        # The REAL strings, ALL of them: a preview that paraphrases is the same
        # failure as a title-only queue, and a preview that truncates is that
        # failure moved into the tail.
        body = report.public_body()
        pages = [body[i : i + PREVIEW_PAGE] for i in range(0, len(body), PREVIEW_PAGE)] or [""]
        for number, page in enumerate(pages, start=1):
            await interaction.followup.send(
                embed=style.embed(
                    title=(
                        f"{style.WARN} Publish this to {service.repo_for(report)}?"
                        if number == 1
                        else f"…continued ({number} of {len(pages)})"
                    ),
                    description=(
                        f"**Issue title**\n{report.public_title()}\n\n"
                        f"**Issue body — this is exactly what goes on the internet**\n"
                        if number == 1
                        else ""
                    )
                    + page,
                    color=style.WARNING,
                    icon_url=style.avatar_url(self.bot),
                ),
                ephemeral=True,
            )
        panel = PublishPreview(interaction.user, report.id, body_digest(report))
        sent = await interaction.followup.send(
            content=(
                f"Labels: {', '.join(report.labels())}\n"
                f"That is the whole body ({len(body)} characters"
                + (f" over {len(pages)} messages" if len(pages) > 1 else "")
                + "). Publishing is public and permanent."
            ),
            view=panel,
            ephemeral=True,
        )
        panel.message = sent

    @app_commands.command(
        name="retryreports", description="Retry reports that could not reach GitHub"
    )
    @app_commands.default_permissions(manage_guild=True)
    async def retry(self, interaction: discord.Interaction) -> None:
        service = getattr(self.bot, "intake", None)
        if service is None:
            await interaction.response.send_message(
                f"Intake is off — create the private #{self.cfg.ch_intake_state} "
                "channel first.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        outcomes = await service.retry_pending()
        done = sum(1 for o in outcomes if o.published)
        await interaction.followup.send(
            f"Retried {len(outcomes)}; {done} reached GitHub."
            + ("" if done == len(outcomes) else " The rest stay queued."),
            ephemeral=True,
        )


async def setup(bot) -> None:
    bot.add_dynamic_items(ConfirmFiling, DismissFiling)
    await bot.add_cog(IntakeCog(bot))
