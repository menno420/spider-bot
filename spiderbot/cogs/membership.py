"""Membership memory: remember what a leaver had, hand it back when they return.

superbot has all the plumbing for this - join recognition, audited role writes,
hierarchy preflight - and no memory between the two. This cog is that memory.

The one thing it deliberately will not do is re-grant the tester role. That
role mirrors who is actually opted in on Google Play, verified by a human; a
member who left Discord has not necessarily kept their Play opt-in, so silently
handing it back would inflate the roster against reality (invariant 5). Instead
the owner gets a prompt carrying the record - who, when they were verified,
what was restored - so he decides from evidence rather than memory.

Unconfigured = silent (invariant 4): with no state channel resolved, nothing
here does anything and nothing here complains twice.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from spiderbot import audit, memory, style
from spiderbot.moderation import gate

log = logging.getLogger("spiderbot.membership")

SNAPSHOT = "member_snapshot"


def is_privileged(role) -> bool:
    """True when this role carries a moderation-shaped permission.

    Reads `gate.STAFF_PERMISSIONS`, the one definition of who counts as staff,
    so a permission added there is honoured here without a second edit.
    """
    perms = getattr(role, "permissions", None)
    if perms is None:
        return False
    return any(getattr(perms, name, False) for name in gate.STAFF_PERMISSIONS)


def restorable_roles(member, guild, tester_role_name: str) -> list:
    """The roles it is both safe and possible to hand back.

    Five exclusions, each for a different reason:
    - `@everyone` is not a grantable role;
    - managed roles belong to integrations (bots, boosts) and Discord refuses;
    - anything at or above the bot's own top role would fail on hierarchy;
    - the tester role is a human decision, never an automatic one;
    - **so is a role carrying a moderation permission.** Same reasoning,
      applied where it had not been: a member who left holding Manage Guild
      got it back silently the moment they rejoined, from an account anyone
      who had taken it over would then be moderating with. Handing back
      authority is a human decision too. Withheld roles are reported to the
      owner rather than dropped.
    """
    me = getattr(guild, "me", None)
    ceiling = getattr(me, "top_role", None)
    out = []
    for role in getattr(member, "roles", ()) or ():
        if getattr(role, "is_default", lambda: False)():
            continue
        if getattr(role, "managed", False):
            continue
        if getattr(role, "name", None) == tester_role_name:
            continue
        if is_privileged(role):
            continue
        if ceiling is not None and getattr(role, "position", 0) >= getattr(ceiling, "position", 0):
            continue
        out.append(role)
    return out


class MembershipCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.cfg = bot.cfg

    @property
    def _state(self):
        return self.bot.channels.get(self.cfg.ch_bot_state)

    def _was_tester(self, member) -> bool:
        return any(
            getattr(r, "name", None) == self.cfg.tester_role_name
            for r in getattr(member, "roles", ()) or ()
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Snapshot what they had, the moment before Discord forgets it."""
        if getattr(member, "bot", False) or self._state is None:
            return
        guild = getattr(member, "guild", None)
        keep = restorable_roles(member, guild, self.cfg.tester_role_name)
        record = {
            "kind": SNAPSHOT,
            "user": member.id,
            "name": str(member),
            "roles": [{"id": r.id, "name": r.name} for r in keep],
            "was_tester": self._was_tester(member),
        }
        if await memory.write(self._state, record):
            audit.stdout_event(
                "member_remembered", user=str(member),
                roles=len(keep), was_tester=record["was_tester"],
            )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Hand back the ordinary roles; put the tester role to the owner."""
        if getattr(member, "bot", False) or self._state is None:
            return
        record = await memory.read_latest(self._state, SNAPSHOT, member.id)
        if record is None:
            return  # a genuinely new arrival - the welcome cog has them

        guild = member.guild
        wanted, missing, withheld = [], [], []
        for entry in record.get("roles") or []:
            role = guild.get_role(entry.get("id")) or discord.utils.get(
                getattr(guild, "roles", ()) or (), name=entry.get("name")
            )
            if role is None:
                missing.append(entry.get("name"))
            elif is_privileged(role):
                # Checked again here, not only at snapshot time: a role can be
                # granted a moderation permission while the member is away, and
                # the snapshot would then hand it back as an ordinary role.
                withheld.append(getattr(role, "name", "?"))
            else:
                wanted.append(role)

        restored = []
        if wanted:
            try:
                await member.add_roles(*wanted, reason="Restored on rejoin")
                restored = [r.name for r in wanted]
            except discord.HTTPException:
                log.warning("could not restore roles for %s", member)

        audit.stdout_event(
            "member_returned", user=str(member), restored=restored,
            missing=missing, withheld=withheld,
            was_tester=bool(record.get("was_tester")),
        )
        if withheld:
            # Independent of the tester prompt: a withheld moderation role is
            # the owner's decision to make, and a silent withhold is a role
            # quietly disappearing from someone who had it.
            await self._report_withheld(member, withheld)
        if record.get("was_tester"):
            await self._prompt_reverify(member, restored)

    async def _report_withheld(self, member, withheld: list[str]) -> None:
        """Say which moderation roles were NOT handed back, and why."""
        await audit.modlog_event(
            self.bot.channels.get("mod-log"),
            f"{style.WARN} Moderator role not restored automatically",
            "\n".join(
                [
                    f"**{member.display_name}** rejoined holding "
                    f"{', '.join(withheld)} when they left.",
                    "",
                    "I have **not** given "
                    + ("them" if len(withheld) > 1 else "it")
                    + " back. Handing back a moderation permission is a human "
                    "decision, the same as the tester role: an account that "
                    "left and came back is not proof the same person is on it.",
                    "",
                    "Add "
                    + ("them" if len(withheld) > 1 else "it")
                    + " by hand if that is right.",
                ]
            ),
            style.WARNING,
        )

    async def _prompt_reverify(self, member, restored: list[str]) -> None:
        """Tell the owner what the bot knows - and that it did NOT act on it."""
        lines = [
            f"**{member.display_name}** just rejoined, and held the "
            f"**{self.cfg.tester_role_name}** role when they left.",
            "",
            "I have **not** given it back: the roster mirrors the real Play "
            "cohort, and leaving Discord does not prove they stayed opted in.",
            "",
            f"Check the opt-in count in Play Console, then run `/tester add` "
            f"with user `{member.display_name}`.",
        ]
        if restored:
            lines += ["", f"Their other roles are back: {', '.join(restored)}."]
        await audit.modlog_event(
            self.bot.channels.get("mod-log"),
            f"{style.WARN} Returning tester needs re-verifying",
            "\n".join(lines),
            style.WARNING,
        )


async def setup(bot) -> None:
    await bot.add_cog(MembershipCog(bot))
