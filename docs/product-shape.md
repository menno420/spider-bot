# Product shape

> **Status:** `binding` for spider-bot · agreed with the owner 2026-08-24,
> **reconciled 2026-09-04** against his statement of purpose the same day.
> This is what the bot is *for* and what it should feel like.
> **Nothing below is deleted.** The five jobs were right about the near term
> and are still how the funnel work is ranked; what changed is that they are
> now four responsibilities in a wider frame, and the frame came from him.
> Canonical intent: fleet-manager `docs/repos/spider-bot/intent.md` + `[D-0042]`.
> How it is built: [`architecture.md`](architecture.md).

## What Spider Bot is

**The AI operations bot of the Slingy Spider Discord server.** The owner, live,
2026-09-04:

> *"Spider Bot exists to manage the Slingy Spider server and help during testing
> of the game. It should become a reliable automoderator with heavy AI
> integration. People should be able to talk naturally to it for guidance,
> complaints, bugs, feedback and improvement ideas. Those reports should become
> durable, easy for the developer to find and act on - preferably through GitHub
> or an equally clear developer-facing system."*

**What this file said before, and why it is kept:** *"the closed-test operations
desk for Slingy Spider"*, whose job is to get the game to Google's bar of 12
testers opted in continuously for 14 days. That is still true and still the
ranking rule for funnel work - see the five jobs below, which are unchanged.
It was too narrow in one direction: it described the bot as an instrument of
one deadline, and he has described it as the thing that **runs the server**, of
which the deadline is the current priority.

## The four responsibilities

His four sentences, decomposed into what a session can check work against.

| # | Responsibility | Done when |
|---|---|---|
| **A** | **Server operations** - event logging, member lifecycle, roles, cleanup, tester status, an owner/mod console, moderation, health, safe announcements | Running the server costs the owner less attention than doing it by hand, and every autonomous action can be explained after the fact |
| **B** | **Testing assistant** - how to join, the current process, the current build, how the game works, known issues, troubleshooting, what feedback is useful, where a report went, what has since been fixed | A tester can answer all of those without the owner, and the owner can see what testers are reporting without reading scrollback |
| **C** | **AI community assistant** - natural conversation as a first-class route, not a fallback behind command names | Someone who knows no command names gets a useful answer or is guided into the right durable workflow |
| **D** | **AI-assisted moderation** - *reliable* is his word and it is the acceptance bar | Obvious problems are handled deterministically, uncertain ones stay reviewable, and no member can make the bot punish someone incorrectly |

**Heavy AI integration is not a model with Discord permissions.** The AI
supplies judgement; deterministic code supplies authority. Invariant 5 is
refined, never deleted - the pipeline is in
[`architecture.md`](architecture.md). A verdict is a validated structured
schema or it is nothing, and invalid model output means **no automatic
action**.

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
| 4 | **Harvest feedback** - structured bug intake, close the loop on a fix | **Intake shipped 2026-09-04**: one service behind every form, plus conversational filing and a private route. Durable records with stable ids; GitHub projection fail-closed until a credential exists. The fix-notification loop has its foundation (status on a report, a "My reports" panel) and is not yet driven by issue closure |
| 5 | **Be the owner's console** - where do I stand, what needs me today | **Extended 2026-09-04**: Home gains Reports and Moderation panels beside the clock and health. No daily digest yet |

**The enabler, and the answer changed:** this file said *"the bot has no durable
memory ... jobs 3 and 4 are where Postgres genuinely becomes necessary."* Half
right. Durable memory is now real (`spiderbot/store.py`) and Postgres is still
**not** necessary - the workload is by-id and whole-collection reads over low
hundreds of records for one server, which a bounded channel scan plus an
in-memory index serves without a database, a migration story or a second
failure mode. The seam is what matters: when a query appears that a scan cannot
serve, one file changes. The reasoning, with the numbers it rests on, is in
`spiderbot/store.py`'s own docstring.

## Where this diverges from the GCB plan

**Resolved 2026-09-04, and in this file's favour.** The owner's statement
narrows spider-bot to one server and one game's testing process, so the plan's
multi-game breadth is no longer this bot's destination. fleet-manager's copy of
the plan carries that note, and `[D-0042]` carries the rule. The paragraph below
is kept as the reasoning that turned out to be right.

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

~~**Formally re-sequencing the plan is an estate decision** in the fleet-manager
venue, under that repo's protocol - it has not been done. Until it is, the plan
outranks this file on anything it actually covers.~~ **Done 2026-09-04** in the
fleet-manager venue: the plan is narrowed there, `OQ-GCB-REVIEW-SCOPE` is
closed by the owner's own words, and the plan remains authoritative as
architecture research rather than as the product definition.

## The build plan

How we get from here to there - the phases, the locked visual system, the
consent copy, and the companion site - lives in
[plan-onboarding-ux-and-site.md](plan-onboarding-ux-and-site.md). That file
is ranked against one number: 12 testers for 14 continuous days.

## What happens to what people say

The owner's fourth sentence is a requirement about *durability*, so it gets its
own section rather than a bullet.

| someone says | it becomes |
|---|---|
| "the game froze when I released the silk" | a durable report with a stable id, and a public GitHub issue on `spider-swing` |
| "the reel feels too weak" | the same, as gameplay feedback |
| "I have an idea for the bird" | the same, as an idea |
| "the game is way too hard" | a **private** record - it arrived through the private door, and a human decides whether it is product feedback |
| "I think this player is harassing me" | a **private** record, and never a public issue under any circumstances |
| "is this already a known bug?" | an answer from the support feed, and the issue reference if there is one |
| "can you tell Menno this?" | the private route, with a reference they can quote back |

**Nothing is lost to a GitHub outage.** The report is durable before any
network call; publication retries and cannot duplicate.

**Nothing private becomes public.** The classifier is deterministic-first and
the AI can only make a report *more* private, never less.

## Standing non-goals

Administrator permission. The bot DMing anyone first. **The AI performing side
effects** - unchanged, and refined into the pipeline in
[`architecture.md`](architecture.md) rather than relaxed. Auto-granting the
tester role. Postgres before a query needs it.
Rebuilding what Discord already does well (forums, events, AutoMod).
A second source of Slingy Spider product truth.
