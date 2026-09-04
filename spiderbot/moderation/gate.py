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


#: What counts as staff, defined ONCE and imported by `prechecks`. Two lists
#: were maintained separately until 2026-09-04 and had drifted: the precheck
#: exempted three permissions, the gate protected four, so a helper whose only
#: elevated permission was `kick_members` or `manage_messages` was analysed AND
#: actable — measured end-to-end, that helper could be timed out by the bot
#: while a `ban_members` helper was protected. The set is the wider reading on
#: purpose: an automoderator acting against anyone the owner trusted with a
#: moderation permission is the failure this check exists to prevent, and the
#: cost of being too wide is only that a staff message is not judged.
STAFF_PERMISSIONS: tuple[str, ...] = (
    "administrator",
    "manage_guild",
    "moderate_members",
    "ban_members",
    "kick_members",
    "manage_messages",
    "manage_roles",
)


def is_staff(member) -> bool:
    """True when this member holds any moderation-shaped permission.

    A member object with no `guild_permissions` at all (a `discord.User`
    rather than a `Member`, or a partial from a raw event) is NOT staff — it
    is an unknown, and the gate's other checks still apply to it.
    """
    perms = getattr(member, "guild_permissions", None)
    if perms is None:
        return False
    return any(getattr(perms, name, False) for name in STAFF_PERMISSIONS)



def check(operation: Operation, *, guild, subject, me=None, actor=None) -> GateResult:
    """May this operation be performed on this member, by this bot, right now?

    `me` defaults to `guild.me`. `subject` is the member the operation would
    act on; for `DELETE_MESSAGE` that is the message's author.

    `actor` is the human asking, on the staff path. When given, the bot lends
    nothing the actor does not already hold: they must have the permission the
    operation needs, and they may not act on someone at or above their own
    highest role. `MEASURED` 2026-09-04: `/modact` is gated at
    `default_permissions(moderate_members=True)` and its choice list includes
    kick and ban, and nothing downstream looked at the actor at all — so a
    junior helper given only Timeout Members could ban anyone the BOT could
    ban. The autonomous path passes no actor and is unchanged.
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
    if is_staff(subject):
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

    # -- the human asking, when there is one. Last, because "you cannot do this
    # yourself" is only worth saying once the action was otherwise possible.
    if actor is not None:
        if needed:
            actor_perms = getattr(actor, "guild_permissions", None)
            if actor_perms is None or not (
                getattr(actor_perms, needed, False)
                or getattr(actor_perms, "administrator", False)
            ):
                return GateResult.deny(
                    f"you do not have the {needed.replace('_', ' ')} permission "
                    "yourself, and the bot does not lend its own",
                    missing_permission=needed,
                )
        # The guild owner outranks everyone and has no role above them, so the
        # hierarchy check would otherwise lock the owner out of their own tools.
        is_owner = getattr(actor, "id", None) == getattr(guild, "owner_id", object())
        if not is_owner and _top_role_position(subject) >= _top_role_position(actor):
            return GateResult.deny("that member is at or above your own highest role")
    return GateResult.allow()
