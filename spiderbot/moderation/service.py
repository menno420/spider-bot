"""The pipeline, assembled. One path from a message to a case.

    precheck -> classify -> policy -> gate -> executor -> case -> audit

Every stage can decline, and a decline is recorded rather than dropped. That is
what makes shadow mode able to answer the question it exists to ask: a system
that only records what it did cannot be evaluated, because its false positives
are exactly the entries it never wrote.

**Modes.** `off` does not classify at all — no model call, no case, no cost.
`shadow` runs the entire path and holds a `ShadowExecutor`, which has no Discord
handle. `enforce` holds the real one. The mode is read once, at construction; a
running service does not change what it is.

**Correlation.** Every case carries the id minted at the edge, and every audit
event on the journey carries the same one, so

    message -> AI verdict -> moderation action -> case

is one grep rather than a reconstruction from timestamps.
"""

from __future__ import annotations

import logging
import time

from spiderbot import audit, ids, store
from spiderbot.ai import safety
from spiderbot.moderation import gate as gate_module
from spiderbot.moderation import operations, prechecks
from spiderbot.moderation.cases import Case, CaseStatus, Mode, ReviewOutcome, Source
from spiderbot.moderation.contracts import Operation
from spiderbot.moderation.policy import Policy

log = logging.getLogger("spiderbot.moderation")


class ModerationService:
    """Holds the mode, the policy, the classifier, the executor and the store."""

    def __init__(
        self,
        *,
        mode: str,
        classifier,
        policy: Policy,
        backing: store.Store,
        enabled_channels: tuple[str, ...] = (),
        executor: operations.Executor | None = None,
        now=time.time,
    ) -> None:
        self.mode = Mode(mode) if mode in {m.value for m in Mode} else Mode.SHADOW
        self._classifier = classifier
        self._policy = policy
        self._store = backing
        self._enabled_channels = enabled_channels
        # `executor_for` defaults to shadow for anything that is not exactly
        # "enforce", so a misspelled mode does nothing rather than acting.
        self._executor = executor or operations.executor_for(str(self.mode))
        self._now = now

    @property
    def enforcing(self) -> bool:
        return self._executor.enforcing

    @property
    def active(self) -> bool:
        return self.mode is not Mode.OFF and bool(self._enabled_channels)

    def describe(self) -> list[str]:
        """For the mod console: what is on, what it would do, what it will not."""
        lines = [
            f"Mode: **{self.mode}**"
            + ("  (recording only — nothing a member sees changes)" if not self.enforcing else ""),
            f"Channels: {', '.join(self._enabled_channels) or '(none — moderation is off)'}",
            f"Classifier: {'available' if getattr(self._classifier, 'enabled', False) else 'OFF'}",
        ]
        lines += self._policy.describe()
        return lines

    # -- the pipeline ---------------------------------------------------------

    async def handle_message(self, message, *, bot_user_id: int | None) -> Case | None:
        """One message through the whole path. Returns the case, or None if the
        message never entered the pipeline. Never raises."""
        if self.mode is Mode.OFF:
            return None
        precheck = prechecks.should_analyse(
            message,
            bot_user_id=bot_user_id,
            enabled_channels=self._enabled_channels,
        )
        if not precheck.proceed:
            return None

        correlation = ids.correlation_id()
        content = message.content or ""
        channel_name = getattr(message.channel, "name", "") or ""

        author = message.author
        # `speaker_label` rejects a display name carrying newlines, brackets or
        # a reserved role word and falls back to the pseudonym, so a member
        # cannot smuggle an instruction into the payload through their nickname.
        analysis = await self._classifier.analyse(
            content,
            author_label=safety.speaker_label(
                getattr(author, "display_name", "") or "", "a member"
            ),
            channel_name=channel_name,
        )
        decision = self._policy.decide(analysis.verdict)

        case = Case(
            id=ids.case_id(),
            created_at=self._now(),
            mode=self.mode,
            source=Source.AI_MESSAGE_SCAN,
            guild_id=getattr(message.guild, "id", None),
            channel_id=getattr(message.channel, "id", None),
            message_id=getattr(message, "id", None),
            subject_id=getattr(author, "id", None),
            subject_name=getattr(author, "display_name", "") or "",
            correlation_id=correlation,
            verdict=analysis.verdict.as_record() if analysis.verdict else None,
            verdict_rejection=str(analysis.rejection) if analysis.rejection else "",
            decision=decision.as_record(),
            operation=decision.operation,
        )

        audit.stdout_event(
            "moderation_analysed",
            case_id=case.id,
            correlation_id=correlation,
            mode=str(self.mode),
            channel=channel_name,
            verdict=(analysis.verdict.category if analysis.verdict else None),
            confidence=(analysis.verdict.confidence if analysis.verdict else None),
            rejection=case.verdict_rejection or None,
            operation=str(decision.operation),
            model=analysis.model,
            latency_ms=analysis.latency_ms,
            tokens_in=analysis.input_tokens,
            tokens_out=analysis.output_tokens,
        )

        case = await self._resolve(case, decision, message=message, subject=author)
        await self._record(case)
        return case

    async def _resolve(self, case: Case, decision, *, message, subject) -> Case:
        """Gate, then execute. Nothing here is reachable in shadow mode except
        the gate, whose verdict is worth recording either way — knowing that an
        action WOULD have been refused is part of evaluating the policy."""
        if decision.operation is Operation.NOTHING:
            return case.with_(status=CaseStatus.OPEN, performed=Operation.NOTHING)

        verdict = gate_module.check(
            decision.operation, guild=message.guild, subject=subject
        )
        if not verdict.allowed:
            audit.stdout_event(
                "moderation_refused",
                case_id=case.id,
                correlation_id=case.correlation_id,
                operation=str(decision.operation),
                reason=verdict.reason,
                missing_permission=verdict.missing_permission or None,
            )
            return case.with_(status=CaseStatus.REFUSED, refusal_reason=verdict.reason)

        if decision.requires_human:
            return case.with_(
                status=CaseStatus.OPEN,
                performed=Operation.NOTHING,
                notes=(*case.notes, "waiting on a moderator"),
            )

        outcome = await self._executor.perform(
            decision.operation,
            message=message,
            subject=subject,
            reason=f"Spider Bot {case.id}: {decision.rationale}"[:400],
        )
        audit.stdout_event(
            "moderation_action",
            case_id=case.id,
            correlation_id=case.correlation_id,
            intended=str(decision.operation),
            performed=str(outcome.performed),
            ok=outcome.ok,
            detail=outcome.detail,
            enforcing=self.enforcing,
        )
        if not outcome.ok:
            return case.with_(status=CaseStatus.REFUSED, refusal_reason=outcome.detail)
        if not self.enforcing:
            return case.with_(status=CaseStatus.SHADOW_ONLY, performed=Operation.NOTHING)
        return case.with_(status=CaseStatus.ACTED, performed=outcome.performed)

    async def _record(self, case: Case) -> bool:
        written = await self._store.append(store.CASES, case.id, case.as_record())
        if not written:
            # A case that cannot be stored is a decision nobody can review,
            # which is the one thing shadow mode cannot tolerate. Loud.
            log.error("moderation: could not store case %s", case.id)
            audit.stdout_event(
                "moderation_case_unstored",
                case_id=case.id,
                correlation_id=case.correlation_id,
            )
        return written

    # -- staff surface --------------------------------------------------------

    async def cases(self, *, limit: int = 25) -> list[Case]:
        """Newest first. Unreadable records are skipped, never guessed at."""
        records = await self._store.load(store.CASES)
        found = [c for c in (Case.from_record(v) for v in records.values()) if c]
        return sorted(found, key=lambda c: c.created_at, reverse=True)[:limit]

    async def get_case(self, case_id: str) -> Case | None:
        record = await self._store.get(store.CASES, case_id)
        return Case.from_record(record) if record else None

    async def review(
        self, case_id: str, outcome: ReviewOutcome, *, by: str, note: str = ""
    ) -> Case | None:
        """A moderator's judgement of a decision. The evaluation data.

        This is what turns shadow mode from a log into a falsification loop:
        without reviews, a shadow corpus only shows what the model said, never
        whether it was right.
        """
        case = await self.get_case(case_id)
        if case is None:
            return None
        reviewed = case.with_(
            review_outcome=outcome,
            review_note=note.strip()[:500],
            reviewed_by=by,
            reviewed_at=self._now(),
            status=CaseStatus.REVIEWED,
        )
        await self._store.append(store.CASES, reviewed.id, reviewed.as_record())
        audit.stdout_event(
            "moderation_reviewed",
            case_id=reviewed.id,
            correlation_id=reviewed.correlation_id,
            outcome=str(outcome),
            by=by,
        )
        return reviewed

    async def staff_action(
        self,
        operation: Operation,
        *,
        guild,
        subject,
        actor: str,
        reason: str,
        message=None,
    ) -> Case:
        """A moderator's own action, routed through the same typed operations.

        Kick and ban are reachable ONLY here — no policy rule produces them
        (`policy.py`) — so the human is always the actor of record. The gate
        still applies: a moderator asking the bot to act against someone above
        it gets a legible refusal instead of a 403.
        """
        case = Case(
            id=ids.case_id(),
            created_at=self._now(),
            mode=self.mode,
            source=Source.STAFF_ACTION,
            guild_id=getattr(guild, "id", None),
            subject_id=getattr(subject, "id", None),
            subject_name=getattr(subject, "display_name", "") or "",
            actor=actor,
            correlation_id=ids.correlation_id(),
            operation=operation,
        )
        verdict = gate_module.check(operation, guild=guild, subject=subject)
        if not verdict.allowed:
            case = case.with_(status=CaseStatus.REFUSED, refusal_reason=verdict.reason)
        else:
            # A staff action enforces even in shadow mode: shadow is about the
            # AUTONOMOUS path, not about disabling the moderators' own tools.
            outcome = await operations.EnforcingExecutor().perform(
                operation, message=message, subject=subject, reason=reason[:400]
            )
            case = (
                case.with_(status=CaseStatus.ACTED, performed=outcome.performed)
                if outcome.ok
                else case.with_(status=CaseStatus.REFUSED, refusal_reason=outcome.detail)
            )
        audit.stdout_event(
            "moderation_staff_action",
            case_id=case.id,
            correlation_id=case.correlation_id,
            operation=str(operation),
            performed=str(case.performed),
            by=actor,
            refused=case.refusal_reason or None,
        )
        await self._record(case)
        return case
