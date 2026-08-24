"""Ready-made messages the owner can post in one click.

The point is that running the closed test should not require typing. Every
recurring thing Menno has to say to testers - the join steps, the day-7
"do not leave the beta" nudge, the wrong-Google-account fix, a new build
landing - lives here as a preset, so posting it is a menu choice.

Text only, no Discord objects except the one embed builder: presets are
content, panels are presentation. Placeholders are filled from `Config` at
render time so the official links can never drift out of sync with the bot's
knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass

import discord

from spiderbot import style


@dataclass(frozen=True)
class Preset:
    """One canned message.

    `key` is stable (it is the select-menu value), `label`/`emoji`/`purpose`
    are what the owner sees in the menu, `body` is what gets posted.
    """

    key: str
    label: str
    emoji: str
    purpose: str
    body: str
    channel: str = "general"
    pings_testers: bool = False


PRESETS: tuple[Preset, ...] = (
    Preset(
        key="join-steps",
        label="How to join the test",
        emoji=style.WEB,
        purpose="The four join steps, with the same-account warning.",
        channel="start-here",
        body=(
            "**Become a Slingy Spider tester** - four steps, about 3 minutes.\n\n"
            "**Use the same Google account for every step.** A different account is "
            "the number one reason joining silently fails.\n\n"
            "**Step 0** - Check which Google account your phone's Play Store uses: "
            "Play Store, then your profile picture, top right.\n"
            "**Step 1** - Join the tester group: {group_url}\n"
            "**Step 2** - Wait about 15 minutes, then open the opt-in page signed in "
            "with that same account and tap **Become a tester**: {optin_url}\n"
            "**Step 3** - Install Slingy Spider from the Play link that page shows.\n\n"
            "Then press **{ok} I've opted in** on the pinned Spider Bot panel "
            "in this channel - or just say the words here - and you get the "
            "**{tester_role}** role once Menno has verified it."
        ),
    ),
    Preset(
        key="wrong-account",
        label="Fix: app not available",
        emoji=style.WARN,
        purpose="The wrong-account / not-propagated-yet troubleshooting answer.",
        body=(
            "**\"App not available\" or \"item not found\"?**\n\n"
            "Almost always one of two things:\n"
            "1. **Wrong Google account.** The account that joined the group must be "
            "the account your Play Store is signed into. Check it: Play Store, "
            "profile picture, top right.\n"
            "2. **The group join has not propagated yet.** It can take up to an "
            "hour. Wait, then open the opt-in page again: {optin_url}\n\n"
            "Still stuck after an hour on the right account? Say so here and Menno "
            "will look at it."
        ),
    ),
    Preset(
        key="stay-opted-in",
        label="Reminder: stay opted in",
        emoji=style.CHART,
        purpose="The retention nudge - the one that protects the 14-day clock.",
        pings_testers=True,
        body=(
            "{spider} **Quick reminder for our testers** - the closed test needs "
            "everyone to stay opted in *continuously*, so please:\n\n"
            "- do **not** tap \"Leave the beta\"\n"
            "- do **not** leave the Google group\n"
            "- keep the game installed\n"
            "- play a few times a week (Google checks real engagement, not just "
            "the opt-in)\n\n"
            "If anyone drops out the clock restarts for everyone. Thank you for "
            "sticking with it!"
        ),
    ),
    Preset(
        key="new-build",
        label="New build is live",
        emoji=style.ANNOUNCE,
        purpose="Tell testers an update is out and ask them to update.",
        channel="announcements",
        pings_testers=True,
        body=(
            "{announce} **A new Slingy Spider build is live.**\n\n"
            "Open the Play Store and update, then give it a few swings. If the "
            "update does not show yet, force-close the Play Store and reopen it - "
            "it can lag a few minutes.\n\n"
            "Found something odd? Post it in #bug-reports with your device, "
            "Android version and what you did just before it happened."
        ),
    ),
    Preset(
        key="bug-how-to",
        label="How to report a bug well",
        emoji=style.BUG,
        purpose="What a useful bug report contains.",
        body=(
            "{bug} **Reporting a bug? These four things make it fixable:**\n\n"
            "1. **What happened**, and what you expected instead\n"
            "2. **Your device and Android version** (Settings, About phone)\n"
            "3. **What you did just before it** - the smaller the steps, the better\n"
            "4. **A screenshot or clip** if you can grab one\n\n"
            "One bug per post, please - it keeps each one trackable."
        ),
    ),
    Preset(
        key="thanks-progress",
        label="Thanks + progress update",
        emoji=style.SPIDER,
        purpose="Warm check-in that keeps the cohort engaged.",
        body=(
            "{spider} **Thank you, testers.**\n\n"
            "Every day you stay opted in moves Slingy Spider closer to launch - "
            "the requirement is a full group of testers opted in for 14 days "
            "straight, so this is genuinely a team effort.\n\n"
            "Keep the feedback coming: what feels good, what feels off, what you "
            "wish the spider could do."
        ),
    ),
    Preset(
        key="recruit",
        label="Recruit more testers",
        emoji=style.WEB,
        purpose="Ask the server to bring a friend into the test.",
        body=(
            "{web} **Know someone with an Android phone?**\n\n"
            "Slingy Spider needs a few more testers before it can launch, and "
            "signing up takes about three minutes. Send them here - the steps are "
            "pinned in #start-here, or they can type `/home` and press "
            "**How do I join?**\n\n"
            "No Android phone needed to hang out, but testers are what get the "
            "game out the door."
        ),
    ),
)

PRESETS_BY_KEY: dict[str, Preset] = {p.key: p for p in PRESETS}


def render(preset: Preset, cfg) -> str:
    """Fill a preset's placeholders from config. Unknown fields stay literal."""
    return preset.body.format(
        group_url=cfg.group_url,
        optin_url=cfg.optin_url,
        tester_role=cfg.tester_role_name,
        ok=style.OK,
        spider=style.SPIDER,
        web=style.WEB,
        bug=style.BUG,
        announce=style.ANNOUNCE,
    )


def steps_embed(cfg, *, icon_url: str | None = None) -> discord.Embed:
    """The join steps as an embed (the richer form used by /jointest and Home)."""
    return style.embed(
        title=f"{style.WEB} Become a Slingy Spider tester",
        description=(
            "Four steps, ~3 minutes. **Use the same Google account everywhere** - "
            "a different account is the #1 reason joining silently fails.\n\n"
            "**Step 0** - Check which Google account your phone's Play Store uses: "
            "Play Store -> your profile picture (top right).\n"
            f"**Step 1** - [Join the tester group]({cfg.group_url}) - click *Join group*.\n"
            "**Step 2** - Wait ~15 minutes, then open the opt-in page signed into "
            f"that same account and tap **Become a tester**: [opt-in page]({cfg.optin_url})\n"
            "**Step 3** - Install Slingy Spider from the Play link the page shows.\n\n"
            f"Then press **{style.OK} I've opted in** on the Spider Bot panel "
            f"- or say so in #{cfg.ch_general} - and Menno gives you the "
            f"**{cfg.tester_role_name}** role once he has verified it.\n\n"
            "*Trouble? \"App not available\" almost always means the wrong Google "
            "account, or the group join has not caught up yet - wait an hour and "
            "retry with the Step-0 account.*"
        ),
        color=style.BRAND,
        footer="Stay opted in for the full 14 days - it protects everyone's progress.",
        icon_url=icon_url,
    )
