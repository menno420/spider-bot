"""The only modules in this package that mutate Discord — and the shadow twin
that structurally cannot.

**Modes are a type split, not a flag.** The usual way to build shadow mode is a
boolean checked before each side effect, and it fails the same way every time:
someone adds a seventh action and forgets the seventh check. Here `off` and
`shadow` are served by `ShadowExecutor`, which **holds no Discord handle at
all**. There is no `if enforcing:` anywhere in this file, because there is
nothing for such a branch to guard: the shadow class has nothing to call.

That also makes the invariant testable rather than asserted. `ShadowExecutor`
is constructed with no arguments, so a test that it cannot act is a test that
its constructor takes no guild — not a test that a flag was read correctly in
seven places.

Every operation is typed and every one returns an `Outcome` rather than raising.
A moderation action that raises inside a listener takes the gateway with it,
and this package exists to be more reliable than the thing it replaces.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Protocol

import discord

from spiderbot.moderation.contracts import Operation

log = logging.getLogger("spiderbot.moderation.ops")

#: How long each timeout class lasts. Data, so the mod console can print it and
#: a policy change does not need a code change to alter the meaning of "short".
#: Discord's own maximum is 28 days (`discord/member.py:1072`).
TIMEOUT_DURATIONS: dict[Operation, dt.timedelta] = {
    Operation.TIMEOUT_SHORT: dt.timedelta(minutes=10),
    Operation.TIMEOUT_LONG: dt.timedelta(hours=24),
}

WARNING_TEMPLATE = (
    "{mention} — a moderator will look at this. Please keep it civil; the "
    "server rules are in the rules channel."
)


@dataclass(frozen=True)
class Outcome:
    performed: Operation
    ok: bool
    detail: str = ""

    @classmethod
    def did(cls, operation: Operation, detail: str = "") -> Outcome:
        return cls(operation, True, detail)

    @classmethod
    def failed(cls, detail: str) -> Outcome:
        return cls(Operation.NOTHING, False, detail)

    @classmethod
    def nothing(cls, detail: str = "") -> Outcome:
        return cls(Operation.NOTHING, True, detail)


class Executor(Protocol):
    """Performs a decided operation. Two implementations, one contract."""

    @property
    def enforcing(self) -> bool: ...

    async def perform(
        self, operation: Operation, *, message=None, subject=None, reason: str = ""
    ) -> Outcome: ...


class ShadowExecutor:
    """Records what would have happened. Cannot do anything else.

    Takes no guild, no channel, no message handle and no client. There is no
    argument that could be passed to make it act, which is a stronger statement
    than any amount of branching: shadow mode is not a mode this class is in,
    it is what this class is.
    """

    @property
    def enforcing(self) -> bool:
        return False

    async def perform(
        self, operation: Operation, *, message=None, subject=None, reason: str = ""
    ) -> Outcome:
        return Outcome.nothing(f"shadow: would have done {operation}")


class EnforcingExecutor:
    """The real one. Every branch here is a Discord mutation.

    The gate has already refused anything this should not attempt, so a failure
    here is genuinely unexpected — a permission revoked between the check and
    the call, a member who left, a message already deleted — and each one
    degrades to a recorded failure.
    """

    @property
    def enforcing(self) -> bool:
        return True

    async def perform(
        self, operation: Operation, *, message=None, subject=None, reason: str = ""
    ) -> Outcome:
        try:
            if operation is Operation.NOTHING:
                return Outcome.nothing()
            if operation is Operation.FLAG_FOR_REVIEW:
                # A flag is not a Discord mutation - the case record IS the flag.
                return Outcome.did(operation, "recorded for staff review")
            if operation is Operation.DELETE_MESSAGE:
                if message is None:
                    return Outcome.failed("no message to delete")
                await message.delete()
                return Outcome.did(operation, "message deleted")
            if operation is Operation.WARN:
                if message is None:
                    return Outcome.failed("no channel to warn in")
                mention = getattr(subject, "mention", "") or ""
                await message.channel.send(
                    WARNING_TEMPLATE.format(mention=mention),
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False,
                        roles=False,
                        users=[subject] if subject is not None else False,
                        replied_user=False,
                    ),
                )
                return Outcome.did(operation, "warning posted")
            if operation in TIMEOUT_DURATIONS:
                if subject is None:
                    return Outcome.failed("no subject to time out")
                await subject.timeout(TIMEOUT_DURATIONS[operation], reason=reason[:400])
                return Outcome.did(
                    operation, f"timed out for {TIMEOUT_DURATIONS[operation]}"
                )
            # KICK and BAN are unreachable from the automatic path: no default
            # policy rule produces them (`policy.py`). They exist here for the
            # STAFF path, where a human pressed a button and is the actor.
            if operation is Operation.KICK:
                if subject is None:
                    return Outcome.failed("no subject to kick")
                await subject.kick(reason=reason[:400])
                return Outcome.did(operation, "kicked")
            if operation is Operation.BAN:
                if subject is None:
                    return Outcome.failed("no subject to ban")
                await subject.ban(reason=reason[:400], delete_message_seconds=0)
                return Outcome.did(operation, "banned")
        except discord.Forbidden:
            return Outcome.failed("Discord refused: the bot lacks the permission")
        except discord.NotFound:
            return Outcome.failed("the target no longer exists")
        except discord.HTTPException as exc:
            log.warning("moderation op %s failed: %s", operation, exc)
            return Outcome.failed(f"Discord error: {exc}")
        except Exception:  # a listener must never take the gateway down
            log.exception("moderation op %s raised unexpectedly", operation)
            return Outcome.failed("unexpected error")
        return Outcome.failed(f"unhandled operation {operation}")


def executor_for(mode: str) -> Executor:
    """`enforce` gets the real executor; everything else gets shadow.

    The default is shadow, so an unrecognised or misspelled mode string cannot
    silently enable enforcement — the failure direction of a typo is "does
    nothing", never "acts".
    """
    return EnforcingExecutor() if mode == "enforce" else ShadowExecutor()
