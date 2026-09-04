"""The permission and risk gate — refusing before Discord does.

Sits between a decision and an operation. Everything it checks, Discord would
also enforce; the point of checking first is that a refusal here is *legible*.
"Spider Bot did not time out that member because its own role sits below
theirs" is an answer a moderator can act on. A 403 in a log is not.

Three classes of check, and they are different kinds of thing:

- **Capability** — does the bot hold the Discord permission this operation
  needs? Missing permission is the single most common cause of an autonomous
  action failing, and the bot is deliberately least-privilege, so this is
  expected rather than exceptional.
- **Hierarchy** — Discord refuses any role or moderation action against a member
  whose top role is at or above the actor's. Checking it here turns a hard
  failure into a recorded refusal.
- **Who the subject is** — the guild owner, a moderator, the bot itself and
  other bots are never valid subjects of an autonomous action. This is the
  check that stops the worst outcome an automoderator can produce: being talked
  into acting against staff.

The gate never mutates anything and never calls Discord. It reads state that
was already fetched and returns a verdict, so it is exhaustively testable.
"""

from __future__ import annotations

from dataclasses import dataclass

from spiderbot.moderation.contracts import Operation

#: Which Discord permission each operation needs, by the attribute name on
#: `discord.Permissions`. Taken from discord.py 2.7.1's own documented
#: requirements: Member.timeout -> moderate_members (`member.py:1072`),
#: Guild.kick -> kick_members (`guild.py:3972`), Guild.ban -> ban_members
#: (`guild.py:3997`), Message.delete for another author -> manage_messages
#: (`message.py:1271`).
REQUIRED_PERMISSION: dict[Operation, str | None] = {
    Operation.NOTHING: None,
    Operation.FLAG_FOR_REVIEW: None,
    Operation.DELETE_MESSAGE: "manage_messages",
    Operation.WARN: "send_messages",
    Operation.TIMEOUT_SHORT: "moderate_members",
    Operation.TIMEOUT_LONG: "moderate_members",
    Operation.KICK: "kick_members",
    Operation.BAN: "ban_members",
}


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    reason: str = ""
    missing_permission: str = ""

    @classmethod
    def allow(cls) -> GateResult:
        return cls(True)

    @classmethod
    def deny(cls, reason: str, *, missing_permission: str = "") -> GateResult:
        return cls(False, reason, missing_permission)


def _top_role_position(member) -> int:
    top = getattr(member, "top_role", None)
    if top is not None:
        return getattr(top, "position", 0)
    roles = getattr(member, "roles", ()) or ()
    return max((getattr(r, "position", 0) for r in roles), default=0)


def _is_staff(member) -> bool:
    perms = getattr(member, "guild_permissions", None)
    return bool(
        perms is not None
        and (
            getattr(perms, "manage_guild", False)
            or getattr(perms, "administrator", False)
            or getattr(perms, "moderate_members", False)
            or getattr(perms, "ban_members", False)
        )
    )


def check(operation: Operation, *, guild, subject, me=None) -> GateResult:
    """May this operation be performed on this member, by this bot, right now?

    `me` defaults to `guild.me`. `subject` is the member the operation would
    act on; for `DELETE_MESSAGE` that is the message's author.
    """
    if operation in (Operation.NOTHING, Operation.FLAG_FOR_REVIEW):
        return GateResult.allow()  # no side effect to gate

    me = me if me is not None else getattr(guild, "me", None)
    if me is None:
        return GateResult.deny("the bot's own guild member could not be resolved")
    if subject is None:
        return GateResult.deny("no subject to act on")

    # -- who the subject is. Checked before capability, because "we would not
    # do this to a moderator" is a better answer than "we lack the permission".
    if getattr(subject, "id", None) == getattr(me, "id", object()):
        return GateResult.deny("the subject is the bot itself")
    if getattr(subject, "bot", False):
        return GateResult.deny("the subject is a bot")
    owner_id = getattr(guild, "owner_id", None)
    if owner_id is not None and getattr(subject, "id", None) == owner_id:
        return GateResult.deny("the subject is the server owner")
    if _is_staff(subject):
        return GateResult.deny(
            "the subject is a moderator; an automoderator never acts against staff"
        )

    # -- hierarchy
    if _top_role_position(subject) >= _top_role_position(me):
        return GateResult.deny(
            "the subject's highest role is at or above the bot's own, so Discord "
            "would refuse this"
        )

    # -- capability
    needed = REQUIRED_PERMISSION.get(operation)
    if needed:
        perms = getattr(me, "guild_permissions", None)
        if perms is None or not getattr(perms, needed, False):
            return GateResult.deny(
                f"the bot does not have the {needed.replace('_', ' ')} permission",
                missing_permission=needed,
            )
    return GateResult.allow()
