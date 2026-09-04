# spider-bot

Spider Bot - the AI community bot for the **Slingy Spider** Discord server:
helps testers join the Google Play closed test, answers questions about the
game, collects feedback, and helps manage the server. Created 2026-08-24 under
the estate plan at `fleet-manager/docs/planning/2026-08-21-game-community-bot/`
(GCB) - read that plan before building anything here.

## What it is for

**The AI operations bot of the Slingy Spider Discord server** - owner
direction, 2026-09-04: manage the server, help during testing, be a reliable
automoderator, let people talk to it naturally, and turn what they say into
durable reports the developer can find and act on. Read
[`docs/product-shape.md`](docs/product-shape.md) for the product and
[`docs/architecture.md`](docs/architecture.md) for how it is built. Canonical
intent lives in fleet-manager (`docs/repos/spider-bot/intent.md`, `[D-0042]`).

**The one sentence the design follows from:** the AI supplies judgement,
deterministic code supplies authority, the repositories supply durable truth.

## Status

v0.1.0 live on Railway since 2026-08-24. Shipped: the community funnel, the
human-only tester roster, the closed-test clock (`/tester count` reads grant
timestamps out of the guild audit log rather than a database, and alarms in
#mod-log the moment a tester loses the role or leaves), membership memory, the
app-like UI layer, and AI chat.

**2026-09-04 - the AI-operations tranche**, all of it **off on arrival** and
turned on one variable at a time ([`docs/rollout.md`](docs/rollout.md)):
one intake service behind every entry point including conversational filing;
durable report storage with stable ids; idempotent GitHub projection into
`spider-swing`; deterministic-first privacy classification; the AI moderation
pipeline with shadow mode, a policy table and one case model; the run-evidence
reader; and the versioned game-support feed consumer.

`tests/` holds **501 tests**; CI workflow `quality` runs ruff + pytest +
compileall on every push. **CI is informational only** - pushes to main still
deploy straight to production; making `quality` a required check (and thus a
PR flow) is an open owner call. Estate registration: fleet-manager
`docs/ESTATE.md`.

## The app surface

Spider Bot is button-driven: `/home` opens a panel with everything the presser
is allowed to do, and `/panel` posts a permanent public one (pin it in
#start-here) so a new arrival needs no command at all. The panel survives
deploys - its buttons carry stable `custom_id`s and `setup_hook` re-registers
the view.

- `spiderbot/ui/routes.py` - the route registry. One frozen `Route` per
  surface with an audience floor; Home renders from it and boot validates it.
  **Adding a feature = adding a `Route` + a `_do_<key>` handler.**
- `spiderbot/ui/base.py` - the panel lifecycle (invoker lock, disable on
  timeout, standard error handling).
- `spiderbot/ui/home.py` - the Home panel, the preset picker, preview/confirm.
- `spiderbot/ui/forms.py` - the modals (feedback, bug report, ask the AI).
- `spiderbot/presets.py` - ready-made messages the owner posts in one click,
  so running the test needs no typing.

Layering is one-directional: `cogs -> ui -> (presets, roster, cohort, config)`.
The UI layer never imports a cog.

## Discord identity (public ids - not secrets)

- Application: **Spider Bot**, app id `1541449715932205187`
  (private app - only the owner can install it; guild-install only)
- Server: **Slingy Spider**, guild id `1541447750628147351`
- Privileged intents enabled in the Developer Portal: Server Members,
  Message Content. Presence stays off.
- Invited with a least-privilege management permission set plus Pin Messages
  (a separate permission since 2025) - **never Administrator** (standing
  estate rule). Its managed role sits above `Slingy Tester`.

## Stack ruling (from the estate plan - do not re-litigate casually)

Python 3.12 + discord.py + Postgres on Railway, one deployable worker
service. `superbot` (live) is the behavior/UX oracle; `superbot-next`
(parked) is the architecture donor - lift its AI kernel contracts
(`sb/kernel/ai/`), config discipline and Railway deployment shape per the
plan's `source-review.md`. Neither source repo is modified. Every reuse gets
an extraction-ledger entry (source repo + commit + file/symbol + decision).

## Secrets - names only, never values

- `DISCORD_TOKEN` - the bot token. Lives in: the Discord Developer Portal,
  the owner's laptop as user env var `DISCORD_BOT_TOKEN_SPIDERBOT` (used for
  API-driven server setup), and (future) a Railway service variable.
  If ever exposed: Portal -> Bot -> Reset Token, then update Railway.
  GitHub secret scanning auto-invalidates Discord tokens pushed to public
  repos - one more reason this repo stays public.
- `APP_ID` = 1541449715932205187 and `GUILD_ID` = 1541447750628147351
  (public values, future Railway variables).
- `ANTHROPIC_API_KEY` - the AI provider.
- `GITHUB_TOKEN` - a **fine-grained** PAT scoped to `menno420/spider-swing`
  with *Issues: Read and write* and nothing else. Absent = every publish is
  refused by name and the report stays queued; nothing is lost and nothing
  pretends to have been filed. Only the account owner can mint it
  (`spider-swing` is a User-owned repository).
- No `DATABASE_URL`. There is no database, on purpose - see
  `spiderbot/store.py` for the reasoning and the numbers it rests on.

Every setting, with what its absence does, is in
[`.env.example`](.env.example); the staged rollout is
[`docs/rollout.md`](docs/rollout.md).

No secret value ever enters this repo. `.env.example` ships names with empty
values only.

## Deployment (live since 2026-08-24)

Railway project **spider-bot** (`f519761e-a71d-4f4b-8cf6-1dbce06ececf`),
service **worker** (`a7f17dde-34f6-4ee0-89ca-785cf61aaca1`), environment
`production`, region europe-west4, NIXPACKS build, start command
`python -m spiderbot`, app sleeping off. Variables set: `DISCORD_TOKEN`,
`ANTHROPIC_API_KEY`, `GUILD_ID` (names here, values only in Railway).

Deploy trap, measured 2026-08-24: `serviceInstanceDeployV2` rebuilds the
service's stored snapshot - it does NOT pull the latest commit until the
branch is armed via `serviceConnect(input: {repo, branch: "main"})`. After
pushing, verify the deployment's `meta.commitHash` matches HEAD; deploy
status SUCCESS alone proves nothing about which code is running.

## Invariants

1. Never Administrator; least-privilege everywhere.
2. Intents and permissions degrade gracefully - the bot boots and reports
   what it lacks rather than refusing.
3. AI initiative only in explicitly allow-listed channels; every AI decision
   leaves exactly one audit row (the superbot-next `nl_engine` pattern).
4. The bot never DMs members first - server rule 4 applies to the bot too.
5. `Slingy Tester` is granted on verified Play opt-in only - the role roster
   mirrors the real closed-test cohort that must hold 12+ for 14 days.
