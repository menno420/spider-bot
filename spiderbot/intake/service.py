"""The one intake implementation every entry point goes through.

There is exactly one way a report comes into existence — `IntakeService.file` —
and every surface calls it: the bug modal, the feedback modal, a new idea form,
a complaint, and the conversational path where someone just says *"I think I
found a bug"* and confirms a summary the bot shows them. That is the owner's
"one intake implementation, many entry points", expressed as the fact that
`ui/forms.py` and the chat cog both end up here.

**The order is the feature.** Store, then classify, then publish. A report is
durable before any network call leaves the process, so:

- GitHub being down costs a *delay*, never a report;
- the reporter is told the truth at each step — "saved" is only said once it is
  actually saved, and "filed as issue #N" only once there is an N;
- a retry is safe, because publication is keyed on the stored record.

**Publication is serialised per report id.** `asyncio.Lock` per id, held across
the store-read/publish/store-write. It is what makes the three idempotency
mechanisms in `github_sink` sufficient for this deployment rather than merely
good: within one worker, two concurrent publishes of the same report cannot
interleave. Across workers it would not hold — and this service runs on one
Railway worker, which is stated here rather than assumed.

**Failure is never silent.** Every outcome is an `Outcome` carrying what
happened, what the reporter should be told, and whether a retry is worth making.
A caller that only checks `outcome.ok` still cannot tell someone their report
was saved when it was not, because `reporter_message` differs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass

from spiderbot import audit, ids, store
from spiderbot.intake import github_sink, privacy
from spiderbot.intake.models import (
    PUBLISHABLE_STATUSES,
    Category,
    Report,
    Reporter,
    Sensitivity,
    Status,
    Target,
)

log = logging.getLogger("spiderbot.intake")


@dataclass(frozen=True)
class Outcome:
    """What happened, and what to say. Never a bare boolean."""

    report: Report | None
    stored: bool
    published: bool
    reporter_message: str
    failure: str = ""

    @property
    def ok(self) -> bool:
        return self.stored


#: How many reports one member may file in `FILE_WINDOW_S`, across EVERY entry
#: point — the limit lives here rather than in a cog because this service is
#: the one thing all of them go through. Far above honest use at this server's
#: volume (a handful a week across everyone) and far below what it takes to
#: matter. `MEASURED` 2026-09-04: nothing rate-limited filing anywhere, and the
#: store is an append-only Discord channel read to a fixed horizon on a cold
#: start, so ~1000 reports push the oldest records past it and they stop
#: existing as far as every panel and every retry queue is concerned. A member
#: typing quickly could make other people's reports disappear.
FILE_LIMIT = 6
FILE_WINDOW_S = 3600.0


class IntakeService:
    """Reports in, durable records out. Holds a store and a GitHub client.

    Deliberately below `ui/` and `cogs/`: it imports neither, so both layers can
    use it and the layering rule (`cogs -> ui -> lower`) is preserved. It knows
    nothing about panels, modals or interactions — a caller hands it text and
    gets back an outcome.
    """

    def __init__(
        self,
        backing: store.Store,
        github: github_sink.GitHubClient | None = None,
        *,
        bot_github: github_sink.GitHubClient | None = None,
        now=time.time,
    ) -> None:
        self._store = backing
        self._github = github or github_sink.NullGitHubClient()
        #: The tracker for reports about the bot itself (owner, 2026-09-04).
        #: Absent means a bot report is refused BY NAME and stays queued — it
        #: is never quietly sent to the game's tracker instead.
        self._bot_github = bot_github or github_sink.NullGitHubClient(
            "no GitHub client is configured for reports about the bot"
        )
        self._now = now
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        #: report id -> the issue GitHub created when the store write that
        #: should have remembered it failed. Process-local and deliberately
        #: not persisted (persisting is exactly what failed), it stops this
        #: process turning one report into N public issues across N retries.
        self._published_unrecorded: dict[str, github_sink.Published] = {}
        #: reporter user id -> the timestamps of their recent filings.
        self._recent_filings: dict[int, list[float]] = defaultdict(list)

    # -- routing --------------------------------------------------------------

    def client_for(self, report: Report) -> github_sink.GitHubClient:
        """The one client this report may be projected through.

        `Report.target` decides, and it decides from the category alone, so a
        report cannot steer itself to the other tracker by what it says.
        """
        return self._bot_github if report.target is Target.BOT else self._github

    def repo_for(self, report: Report) -> str:
        """The repository a publish would post to, for the human who confirms."""
        # `getattr`: the Protocol names `repo`, but a client is any object with
        # the three publish methods, and the name is for a human's eyes only.
        return getattr(self.client_for(report), "repo", "") or "(no repository configured)"

    @property
    def can_publish(self) -> bool:
        """Whether ANY tracker is reachable — what a retry loop should ask."""
        return self._github.available or self._bot_github.available

    # -- filing ---------------------------------------------------------------

    async def file(
        self,
        *,
        category: Category,
        title: str,
        description: str,
        reporter: Reporter | None = None,
        build_version: str = "",
        device: str = "",
        repro_steps: str = "",
        evidence_summary: tuple[str, ...] = (),
        evidence_format: str = "",
        ai_summary: str = "",
        ai_tags: tuple[str, ...] = (),
        ai_says_private: bool | None = None,
        #: Whether this entry point told the member, BEFORE they typed, that
        #: their report may reach a public tracker. Default False: a caller
        #: that has not said so has not obtained consent, and `may_publish`
        #: cites this field by name.
        reporter_cleared: bool = False,
        correlation_id: str = "",
        #: A caller that has already CLAIMED an id passes it, so a retry files
        #: the same report rather than a second one. Everyone else lets the
        #: service mint one.
        report_id: str = "",
    ) -> Outcome:
        """File one report. Store first; publication is a separate step.

        Returns as soon as the durable write is settled, so the reporter is
        answered promptly and publication does not sit on the interaction.
        """
        correlation_id = correlation_id or ids.correlation_id()
        if reporter is not None and not self._may_file(reporter.user_id):
            audit.stdout_event(
                "report_rate_limited",
                correlation_id=correlation_id,
                category=str(category),
            )
            return Outcome(
                None,
                stored=False,
                published=False,
                failure="rate_limited",
                reporter_message=(
                    "That is a lot of reports in a short time - I have stopped "
                    "writing them down for a bit so the earlier ones do not get "
                    "buried. Try again in an hour, or ping Menno if it is urgent."
                ),
            )
        report = Report(
            id=report_id or ids.report_id(),
            category=category,
            title=title.strip(),
            description=description.strip(),
            submitted_at=self._now(),
            correlation_id=correlation_id,
            reporter=reporter,
            build_version=build_version.strip(),
            device=device.strip(),
            repro_steps=repro_steps.strip(),
            evidence_summary=evidence_summary,
            evidence_format=evidence_format,
            ai_summary=ai_summary.strip(),
            ai_tags=ai_tags,
            reporter_cleared=reporter_cleared,
            status=Status.DRAFT,
        )
        report = privacy.apply(report, ai_says_private=ai_says_private)
        report = report.with_(status=Status.STORED)

        stored = await self._store.append(store.REPORTS, report.id, report.as_record())
        audit.stdout_event(
            "report_filed",
            report_id=report.id,
            correlation_id=correlation_id,
            category=str(report.category),
            sensitivity=str(report.sensitivity),
            stored=stored,
            has_evidence=bool(evidence_summary),
        )
        if not stored:
            # The one thing that must never be quietly wrong. The report is not
            # durable, so the reporter is told exactly that rather than thanked.
            log.error("intake: durable write failed for %s", report.id)
            return Outcome(
                report=report,
                stored=False,
                published=False,
                failure="store_unavailable",
                reporter_message=(
                    "I could not save that properly, so I have not recorded it. "
                    "Please post it in the channel so a human sees it — sorry."
                ),
            )
        return Outcome(
            report=report,
            stored=True,
            published=False,
            reporter_message=self._receipt(report),
        )

    def _receipt(self, report: Report) -> str:
        """What the reporter is told the moment it is saved.

        It no longer PROMISES a GitHub issue. It used to, and that promise was
        made by a keyword classifier that could not read Dutch — so a member
        writing a complaint about someone was told "I will file it on the
        game's issue tracker" and it was true. Menno decides what becomes
        public; the receipt says exactly that.
        """
        base = f"Saved. Your reference is `{report.id}`."
        if report.sensitivity is Sensitivity.PUBLIC_SAFE:
            return (
                f"{base} Menno will see it, and he may put it on the game's "
                "issue tracker so it does not get lost."
            )
        return (
            f"{base} This one stays private — only Menno and the moderators "
            "will see it."
        )

    async def approve(self, report_id: str, *, by: str) -> Report | None:
        """A person clears a report for a PUBLIC issue. The publication gate.

        Nothing else sets `approved_by`, and `Report.may_publish` requires it.
        This is the whole answer to "a regex miss must not mean publish".
        """
        async with self._locks[report_id]:
            return await self._approve_locked(report_id, by=by)

    async def _approve_locked(self, report_id: str, *, by: str) -> Report | None:
        """The body of `approve`, under the same per-report lock `publish` takes.

        Codex, spider-bot#3, 2026-09-04: approval was a read-modify-write
        OUTSIDE that lock, so `/publish` on an already-approved failed report
        racing the new scheduled retry could complete a stale append after the
        retry recorded PUBLISHED — erasing the issue number, re-queueing the
        report, and leaving the marker search as the only thing between that
        and a duplicate public issue.
        """
        report = await self.get(report_id)
        if report is None:
            return None
        if not report.is_public_safe:
            # The classifier's judgement still binds a human here: it is a
            # sorter, and a private report needs re-categorising rather than
            # waving through, so that the reason is recorded either way.
            #
            # It returns the report UNCHANGED, which is a silent no-op to read
            # by accident — so the audit line says what happened and names the
            # condition that failed. Callers reach this only after checking
            # `is_public_safe` themselves (`/publish` says why); a caller that
            # did not check gets a report whose `approved_by` is still empty,
            # and `may_publish` refuses it.
            audit.stdout_event(
                "report_approval_refused",
                report_id=report.id,
                correlation_id=report.correlation_id,
                by=by,
                reporter_cleared=report.reporter_cleared,
                sensitivity=str(report.sensitivity),
            )
            return report
        approved = report.with_(approved_by=by)
        await self._store.append(store.REPORTS, approved.id, approved.as_record())
        audit.stdout_event(
            "report_approved",
            report_id=approved.id,
            correlation_id=approved.correlation_id,
            by=by,
        )
        return approved

    async def awaiting_approval(self) -> list[Report]:
        """Public-safe reports nobody has cleared yet. The owner's queue."""
        return [
            r
            for r in await self.all_reports()
            if r.is_public_safe
            and not r.approved_by
            and r.github_issue_number is None
            and r.status in PUBLISHABLE_STATUSES
        ]

    # -- publication ----------------------------------------------------------

    async def publish(self, report_id: str) -> Outcome:
        """Project a stored report to GitHub, idempotently.

        Safe to call repeatedly. Serialised per report id, so a retry racing
        the original cannot double-create.
        """
        async with self._locks[report_id]:
            record = await self._store.get(store.REPORTS, report_id)
            if record is None:
                return Outcome(None, False, False, "", failure="unknown_report")
            report = Report.from_record(record)
            if report is None:
                return Outcome(None, False, False, "", failure="unreadable_record")

            remembered = self._published_unrecorded.get(report.id)
            if report.github_issue_number is None and remembered is not None:
                # The issue exists; only the record of it is missing, because
                # the store write failed after GitHub had already created it.
                # `MEASURED` 2026-09-04: without this, seven presses of
                # /retryreports produced seven separate public issues for one
                # report, all carrying the same intake id, and the report
                # stayed in the retry queue for the eighth. This memory is
                # process-local and lost on restart — the marker search is the
                # backstop that survives one, and it is a backstop, not a
                # guarantee, which is why this exists as well.
                report = report.with_(
                    status=Status.PUBLISHED,
                    github_issue_number=remembered.number,
                    github_issue_url=remembered.url,
                )
                await self._store.append(store.REPORTS, report.id, report.as_record())
            if report.github_issue_number is not None:
                return Outcome(
                    report,
                    stored=True,
                    published=True,
                    reporter_message=self._published_message(report),
                )
            if not report.may_publish:
                return Outcome(
                    report,
                    stored=True,
                    published=False,
                    failure="not_publishable",
                    reporter_message="",
                )

            pending = report.with_(status=Status.PUBLISH_PENDING)
            await self._store.append(store.REPORTS, report.id, pending.as_record())

            result = await github_sink.publish(self.client_for(pending), pending)
            if isinstance(result, github_sink.Published):
                final = pending.with_(
                    status=Status.PUBLISHED,
                    github_issue_number=result.number,
                    github_issue_url=result.url,
                )
                written = await self._store.append(
                    store.REPORTS, final.id, final.as_record()
                )
                audit.stdout_event(
                    "report_published",
                    report_id=final.id,
                    correlation_id=final.correlation_id,
                    issue=result.number,
                    recorded=written,
                )
                if not written:
                    # The issue exists but we failed to remember it. Hold it in
                    # process memory so this process cannot create a second
                    # one, and say so loudly: after a restart the marker search
                    # is the only thing left standing between a retry and a
                    # duplicate public issue.
                    self._published_unrecorded[final.id] = result
                    log.error(
                        "intake: published %s as #%s but could not record it; "
                        "held in memory for this process, and the marker search "
                        "is the only backstop across a restart",
                        final.id, result.number,
                    )
                return Outcome(
                    final,
                    stored=True,
                    published=True,
                    reporter_message=self._published_message(final),
                )

            failed = pending.with_(
                status=Status.PUBLISH_FAILED,
                publish_failure=result.reason,
                publish_failure_retryable=result.retryable,
            )
            await self._store.append(store.REPORTS, failed.id, failed.as_record())
            audit.stdout_event(
                "report_publish_failed",
                report_id=failed.id,
                correlation_id=failed.correlation_id,
                reason=result.reason,
                retryable=result.retryable,
            )
            return Outcome(
                failed,
                stored=True,
                published=False,
                failure=result.reason,
                reporter_message=(
                    f"Saved as `{failed.id}`. I could not file it on the issue "
                    "tracker just now, so it is queued and I will retry."
                    if result.retryable
                    else f"Saved as `{failed.id}`. I could not put it on the "
                    "issue tracker and retrying will not help, so I have "
                    "flagged it for Menno instead. The report itself is safe."
                ),
            )

    def _may_file(self, user_id: int) -> bool:
        """Record this filing and say whether it is within the limit.

        Deliberately counts ATTEMPTS, not successes: a member whose reports are
        all failing to store is still driving writes at the channel, which is
        the thing being limited. Process-local, like every other cooldown here
        — a restart forgives, which at this volume is the right trade against
        putting a counter in the store this is protecting.
        """
        now = self._now()
        recent = [t for t in self._recent_filings[user_id] if now - t < FILE_WINDOW_S]
        recent.append(now)
        self._recent_filings[user_id] = recent
        return len(recent) <= FILE_LIMIT

    def _published_message(self, report: Report) -> str:
        where = report.github_issue_url or f"issue #{report.github_issue_number}"
        return f"Saved as `{report.id}` and filed: {where}"

    # -- reading --------------------------------------------------------------

    async def get(self, report_id: str) -> Report | None:
        record = await self._store.get(store.REPORTS, report_id)
        return Report.from_record(record) if record else None

    async def all_reports(self) -> list[Report]:
        """Every readable report, newest first. Unreadable records are skipped."""
        records = await self._store.load(store.REPORTS)
        reports = [r for r in (Report.from_record(v) for v in records.values()) if r]
        return sorted(reports, key=lambda r: r.submitted_at, reverse=True)

    async def pending_publication(self) -> list[Report]:
        """Public-safe reports that have not reached GitHub. The retry queue."""
        return [r for r in await self.all_reports() if r.may_publish]

    async def retry_pending(self, *, limit: int = 10) -> list[Outcome]:
        """One pass over the retry queue. Bounded, so a long outage cannot
        turn into a burst against GitHub's secondary rate limit when it ends.

        Oldest first. `pending_publication` is newest-first for the panel, and
        slicing that meant a queue longer than the limit retried the same
        newest `limit` reports every pass while the oldest never moved.
        """
        outcomes = []
        queue = sorted(await self.pending_publication(), key=lambda r: r.submitted_at)
        for report in queue[:limit]:
            if not self.client_for(report).available:
                # Two trackers: the loop runs when EITHER is reachable, so a
                # report whose own tracker is not must be left queued rather
                # than attempted — an attempt writes a `publish_pending` and a
                # `publish_failed` generation against a client that cannot
                # become available without a redeploy, every pass, and those
                # writes eat the store's fixed horizon (Codex, spider-bot#3,
                # round 2 — the same hole, one tracker later).
                continue
            outcomes.append(await self.publish(report.id))
        return outcomes

    async def stuck(self) -> list[Report]:
        """Approved reports a retry can never fix. For the staff panel.

        They are excluded from `pending_publication` by `may_publish`, which
        is what stops the retry loop — so without this they would simply be
        invisible, which is the failure the exclusion was meant to prevent.
        """
        return [
            r
            for r in await self.all_reports()
            if not r.publish_failure_retryable and r.github_issue_number is None
        ]

    async def mark_resolved(self, report_id: str, resolution: str) -> Report | None:
        report = await self.get(report_id)
        if report is None:
            return None
        updated = report.with_(status=Status.RESOLVED, resolution=resolution.strip())
        await self._store.append(store.REPORTS, updated.id, updated.as_record())
        audit.stdout_event(
            "report_resolved", report_id=updated.id, correlation_id=updated.correlation_id
        )
        return updated
