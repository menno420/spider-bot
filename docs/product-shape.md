# Product shape

> **Status:** `binding` for spider-bot · agreed with the owner 2026-08-24.
> This is what the bot is *for* and what it should feel like. The GCB plan
> (fleet-manager `docs/planning/2026-08-21-game-community-bot/`) still owns the
> long arc; this file owns the near one, and says plainly where the two differ.

## What Spider Bot is

**The closed-test operations desk for Slingy Spider.** Its job is to get the
game to Google's bar - 12 testers opted in continuously for 14 days - to
protect that clock once it is running, and to turn testers into feedback the
developer can act on.

## How it should feel - the owner's direction, 2026-08-24

Three sentences, and they outrank taste:

1. **It should function like the original superbot.** superbot is the
   behaviour oracle. Copy it as closely as the situation allows.
2. **Structured, not copied wholesale.** Where superbot's own docs admit a
   pattern hurt (metadata split across two untyped registries, view code
   reaching into cogs), fix it on the way in rather than inheriting it.
3. **App-like: buttons and menus, not typing.** A member should be able to do
   everything without knowing a single command exists. Ship many ready-made
   presets so the owner picks rather than writes.

What that means concretely, and what it forbids:

- `/home` is the front door; `/panel` pins a permanent public one so a new
  arrival needs *no* command at all. Slash commands are shortcuts, never the
  only route.
- Panels render from one registry (`spiderbot/ui/routes.py`). Adding a feature
  is adding a `Route` plus a handler - never hand-editing a panel's layout.
- Nobody is shown a button they may not press, and pressing is re-authorised
  at press time. A dead button that answers "you may not do that" is worse
  than no button.
- Anything that reaches the whole server is previewed first, then confirmed.

## The five jobs, in priority order

| # | Job | State |
|---|---|---|
| 1 | **Get people in** - explain the steps, catch opt-in claims, make joining one press | Panel + presets shipped. Per-person funnel progress (arrived → joined group → claimed → verified → granted) still missing |
| 2 | **Hold the clock** - grant dates, day counts, projected finish, alarm on a broken streak | **Shipped** 2026-08-24 (`cohort.py`, `roster.py`) |
| 3 | **Keep them** - notice a tester gone quiet, weekly standing, timed nudges | Presets exist for the nudges; nothing scheduled or automatic yet |
| 4 | **Harvest feedback** - structured bug intake, close the loop on a fix | Bug + feedback forms shipped; the fix-notification loop is not built |
| 5 | **Be the owner's console** - where do I stand, what needs me today | Partial: the clock and health are on Home; no daily digest |

**The enabler, still deferred honestly:** the bot has no durable memory. Job 2
sidesteps that by reading grant dates out of Discord's own audit log (~45 days
of retention, comfortably more than a 14-day window). Jobs 3 and 4 are where
Postgres genuinely becomes necessary - not before.

## Where this diverges from the GCB plan

The plan describes a **multi-game community platform**: a setup wizard that
provisions servers, build publishing with cohort routing, playtest scheduling,
moderation with appeals for banned members, an AI operator that plans and asks
approval before acting, service principals, an outbox. Its MVP completes after
Phase 6.

That is the right blueprint for a server with hundreds of members and several
games. Slingy Spider has one game, a handful of members, and a deadline shaped
like Google's rule. So the plan is treated as **out of sequence, not wrong**:
its Home/route-registry doctrine and its product acceptance rules are adopted
now (they are what this file's "how it should feel" section implements); its
platform breadth waits.

**Formally re-sequencing the plan is an estate decision** in the fleet-manager
venue, under that repo's protocol - it has not been done. Until it is, the plan
outranks this file on anything it actually covers.

## The build plan

How we get from here to there - the phases, the locked visual system, the
consent copy, and the companion site - lives in
[plan-onboarding-ux-and-site.md](plan-onboarding-ux-and-site.md). That file
is ranked against one number: 12 testers for 14 continuous days.

## Standing non-goals

Administrator permission. The bot DMing anyone first. AI performing side
effects. Auto-granting the tester role. Postgres before a job needs it.
Rebuilding what Discord already does well (forums, events, AutoMod).
