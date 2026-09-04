# Session logs

Per-session logs live here as `<date>-<slug>.md`, newest first.

**Why this directory exists (2026-09-04).** Every other actively-worked repo in
the estate keeps one; spider-bot did not, and fleet-manager measured the cost on
2026-08-26: this repo had **20 commits in two days and no `.sessions/` at all**,
so no session working here could ever appear in the estate-wide activity index
([`docs/activity/`](https://github.com/menno420/fleet-manager/blob/main/docs/activity/README.md)).
A session that leaves no card is invisible to the next one.

## The card

Create the log as the session's **first** commit with a born-red status
(`> **Status:** \`in-progress\``) so in-flight work is visible, then flip it to
`complete` as the deliberate **last** step once the close-out is written. A
half-done session must never read as finished.

Header block, above the first `##`:

```
- **📊 Model:** <family-level model> · <effort> · <task class>
- **📍 Venue:** <local-desktop | local-cli | cloud-container | codex-cloud | chatgpt-work | other>
- **🔗 Session:** [<id>](https://claude.ai/code/<id>) · "<title>"
```

The model segment is the **family-level** name your own harness reports
(`opus-5`, `sonnet-5`, `fable-5`) — never a dated model ID, never copied from an
external surface. Omit a line rather than guess: an honest absence is readable,
a wrong value is not.

Then, in the body: what the session was about, what was done, the verification
with **real exit codes**, and anything the next session must not re-derive.

## What this repo adds to the estate grammar

**Every card that changes anything under `spiderbot/` states the deployment
outcome.** Push to `main` deploys straight to production, and Railway's
`SUCCESS` alone proves nothing about which code is running — the card records
the deployment's `meta.commitHash` and whether it equals the merged HEAD, or
states plainly that the change was docs/tests-only and the watch patterns
deliberately did not deploy.
