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
    Category,
    Report,
    Reporter,
    Sensitivity,
    Status,
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
        now=time.time,
    ) -> None:
        self._store = backing
        self._github = github or github_sink.NullGitHubClient()
        self._now = now
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

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
        correlation_id: str = "",
    ) -> Outcome:
        """File one report. Store first; publication is a separate step.

        Returns as soon as the durable write is settled, so the reporter is
        answered promptly and publication does not sit on the interaction.
        """
        correlation_id = correlation_id or ids.correlation_id()
        report = Report(
            id=ids.report_id(),
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
        """What the reporter is told the moment it is saved."""
        base = f"Saved. Your reference is `{report.id}`."
        if report.sensitivity is Sensitivity.PUBLIC_SAFE:
            return (
                f"{base} I will file it on the game's issue tracker so Menno "
                "sees it with everything else."
            )
        return (
            f"{base} This one stays private — only Menno and the moderators "
            "will see it."
        )

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

            result = await github_sink.publish(self._github, pending)
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
                    # The issue exists but we failed to remember it. The marker
                    # search is exactly the backstop for this, so say so rather
                    # than leaving a silent inconsistency.
                    log.error(
                        "intake: published %s as #%s but could not record it; the "
                        "marker search will prevent a duplicate on retry",
                        final.id, result.number,
                    )
                return Outcome(
                    final,
                    stored=True,
                    published=True,
                    reporter_message=self._published_message(final),
                )

            failed = pending.with_(status=Status.PUBLISH_FAILED)
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
                ),
            )

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
        turn into a burst against GitHub's secondary rate limit when it ends."""
        outcomes = []
        for report in (await self.pending_publication())[:limit]:
            outcomes.append(await self.publish(report.id))
        return outcomes

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
