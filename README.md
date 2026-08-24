# spider-bot

Spider Bot - the AI community bot for the **Slingy Spider** Discord server:
helps testers join the Google Play closed test, answers questions about the
game, collects feedback, and helps manage the server. Created 2026-08-24 under
the estate plan at `fleet-manager/docs/planning/2026-08-21-game-community-bot/`
(GCB) - read that plan before building anything here.

## Status

v0.1.0 live on Railway since 2026-08-24 (community funnel, tester roster, AI
chat). Phase-0 hardening landed 2026-08-24: ruff + a pytest harness under
`tests/` (78 tests at landing) and CI workflow `quality` running lint + tests
+ compileall on every push. **CI is informational only** - pushes to main
still deploy straight to production; making `quality` a required check (and
thus a PR flow) is an open owner call. Still deferred from the GCB Phase-0
list until the bot needs durable state: Postgres/migrations, Docker, config
schema. Estate registration: fleet-manager `docs/ESTATE.md`.

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
- `DATABASE_URL` - future Railway Postgres.
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` - AI providers, future Railway
  variables.

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
