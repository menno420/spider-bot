# Rollout — what turns on, when, and on what evidence

> **Status:** `binding` for spider-bot · written 2026-09-04.
> **The rule this file exists to enforce:** nothing turns on because the code
> for it exists. Each step below names the evidence that justifies the next one.

Spider Bot is live in a real Discord server and a push to `main` deploys
straight to production. Everything shipped in the AI-operations work is
therefore **off on arrival** and turns on one variable at a time.

## What is off right now

| capability | switch | ships as | what it does while off |
|---|---|---|---|
| GitHub projection | `INTAKE_PUBLISH_ENABLED` | **false** | reports are stored durably and queued; the console shows them as awaiting publication |
| GitHub credential | `GITHUB_TOKEN` | **unset** | every publish is refused **by name** and the report stays queued — nothing invents a credential, nothing pretends to have published |
| AI moderation | `MOD_MODE` | **off** | no classifier call, no case, no cost |
| moderated channels | `MOD_WATCH_CHANNELS` | **empty** | moderation runs nowhere; empty means nowhere, never everywhere |
| enforcement class | `MOD_CEILING` | **flag_for_review** | the whole classifier and policy path runs; nothing a member can see changes |
| report intake | the `#intake-state` channel | **does not exist** | intake is off entirely rather than accepting reports it cannot keep |
| moderation cases | the `#case-state` channel | **does not exist** | moderation stays off, because a decision nobody can review is worse than no decision |

Two independent locks on each of the two risky capabilities, on purpose: a
single variable set by accident should not be able to put a member's words on a
public tracker or restrict their account.

## The sequence

### Step 1 — deploy with everything off

Merge. Verify the **deployed commit**, not Railway's status: the new
deployment's `meta.commitHash` must equal the merged `HEAD`. `SUCCESS` alone
proves nothing about which code is running (the `serviceConnect` trap in
`README.md`). Note that `.railway/railway.ts`'s watch patterns mean a docs- or
tests-only commit deliberately does **not** deploy and the live hash will lag —
that is correct, not a failure.

**Evidence to go on:** `meta.commitHash == HEAD`, the `ready` audit line lists
the resolved channels, `/home` still opens, `/tester count` still answers, the
AI still replies on mention. **Nothing new is exercised at this step** — the
point is that nothing broke.

### Step 2 — turn on intake, still without GitHub

Owner creates the private `#intake-state` channel the bot can read and write.

**Evidence to go on:** a bug filed through `/home → Report a bug` returns a
reference; `/home → Reports` shows it; the record is visible as JSON in
`#intake-state`; `My reports` shows it to the reporter. A report survives a
redeploy — which is the whole difference from what the forms did before.

### Step 3 — turn on the GitHub projection

Owner mints the credential (below) and sets `INTAKE_PUBLISH_ENABLED=true`.

**Nothing publishes itself even then.** A report reaches GitHub only when a
person runs `/publish <id>`, because an adversarial review reproduced four ways
a keyword classifier lets a complaint about a named member through — including
every report written in Dutch. Home → **Reports** lists what is waiting for
that decision.

**Test it with a controlled report before a member does**: file one through the
form, confirm the issue appears on `spider-swing`, confirm the issue body
carries the intake id and **no Discord identity**, then run `/retryreports` and
confirm **no second issue appears**.

**Evidence to go on:** one issue per report, idempotency proven by a retry
against a real issue, and a private report (via *Tell Menno privately*) that
does **not** appear on GitHub.

### Step 4 — moderation in shadow

Owner creates the private `#case-state` channel, sets `MOD_MODE=shadow` and
names `MOD_WATCH_CHANNELS`. `MOD_CEILING` stays at `flag_for_review`.

**Evidence to go on — and this is the step that cannot be rushed.** Shadow
produces a corpus; a corpus is not evidence until it has been *judged*. Use
`/case review <id> <verdict>` on real decisions. What to look for:

- **Are there false positives?** `too_strict` on ordinary frustration with the
  game is the failure mode this server is most exposed to — testers being rude
  about a hard game is the normal state of a playtest.
- **Are there false negatives?** `too_lenient` on something a moderator would
  have acted on.
- **Is the category right?** `wrong_category` points at the classifier;
  `too_strict`/`too_lenient` point at the thresholds.

### Step 5 — raise the ceiling, one class at a time

Each raise is one variable and is reversible. **No raise happens without review
data behind it.**

| raise to | what it enables | the evidence that justifies it |
|---|---|---|
| `delete_message` | scam and fake-tester-link removal only | reviewed shadow decisions in those two categories, with **zero** `too_strict` among them. This class is first because it removes harmful content and penalises nobody |
| `warn` | a public warning message | a body of reviewed decisions where `correct` dominates and no `too_strict` on game-criticism |
| `timeout_short` | a 10-minute restriction | as above, sustained over a longer window, with the owner explicitly comfortable that a false positive at this level is recoverable |
| `timeout_long` | 24 hours | a deliberate separate decision; a day is a long time in a 16-person server |

`kick` and `ban` are **not on this ladder**. No policy rule produces them, so
raising the ceiling cannot reach them. They stay a human action through
`/modact`, and making them autonomous would be a new owner decision and a code
change, not a variable.

## What has to come from the owner

Six things, each one exact and none of which a session can do:

1. **A fine-grained GitHub PAT.** Settings → Developer settings → Personal
   access tokens → Fine-grained. *Repository access* → **Only select
   repositories** → `menno420/spider-swing`. *Repository permissions* →
   **Issues: Read and write**, and nothing else. Set it as `GITHUB_TOKEN` on
   the Railway `spider-bot` worker. Only he can do this: `spider-swing` is a
   User-owned repository, so there is no organisation-approval path and no
   delegated issuance. Set it in the dashboard, not in `.railway/railway.ts`:
   the IaC file already declares it — and every other switch in this document
   — with `preserve()`, so a later `railway config apply` keeps the dashboard
   value instead of deleting a variable the file did not name.
2. **The label `from-spider-bot`** in `menno420/spider-swing` (any colour;
   description "Filed by Spider Bot from the Slingy Spider Discord"). Verified
   absent 2026-09-04 — the repo has thirteen labels and this is not one. If he
   would rather not have it, say so and one line comes out of
   `Report.labels()`; the code already survives its absence by retrying without
   labels rather than losing the issue.
3. **Two private staff channels** the bot can read and write: `#intake-state`
   and `#case-state`. Until they exist the features are silently off, by design.
4. **The bot's permission set** must include `moderate_members` and
   `manage_messages` for any enforcement class above `delete_message` — and
   **never Administrator**. Shadow mode computes the gate anyway, so a missing
   permission shows up as a recorded refusal *before* enforcement rather than
   after.
5. **Discord AutoMod rules**, in Server Settings, for the classes this design
   deliberately does not rebuild: keyword, spam, mention-spam and harmful-link.
   That needs `manage_guild` and is his hands.
6. **`known_issues` in the support feed.** It ships empty because a known issue
   is his call, not an inference from a doc. One edit to
   `spider-swing/support/source.json` and the bot knows within the hour.

## What is still open, and is his to answer

- **Does a public-safe report reach GitHub automatically, or only after the
  reporter presses "yes"?** It currently ships requiring the reporter's own
  clearance for a conversational draft, and treating an explicit form
  submission as clearance. Both are one field.
- **Which repository gets a report about the BOT** rather than the game?
  `spider-swing` owns the game; "the panel button did nothing" is a spider-bot
  issue. Everything currently goes to `spider-swing`.
- **Should tester ideas reach `spider-swing`'s tracker at all?** It holds one
  real issue today against 179 pull requests. A stream of bot-filed ideas
  changes the character of that tracker, and that is a product judgement.
- **Does `#intake-state` need splitting?** Reports, cases and conversational
  DRAFTS share one channel and one 2000-message cold-read window. Nothing older
  than that window is loaded on a restart, and an unloaded report is
  indistinguishable from a deleted one. Two things now stand between that and a
  lost report — a per-member filing limit in `IntakeService` and a per-member
  offer cooldown armed before the write rather than after the reply — and the
  store logs an ERROR the moment a cold read hits the horizon. **That log line
  is the signal to act**: give drafts their own channel, or move the store to a
  real database. Neither is needed at a handful of reports a week; both are
  needed before the window fills, and the bot cannot make that call itself.

- **Who counts as staff?** The moderation gate and the precheck now read one
  shared set — `gate.STAFF_PERMISSIONS`: administrator, manage_guild,
  moderate_members, ban_members, kick_members, manage_messages, manage_roles —
  so anyone the owner trusted with a moderation permission is neither judged
  nor actable. The **panel** still reads `manage_guild` alone, so a moderator
  with only `moderate_members` is protected from the bot but cannot open the
  review queue. That half is still his call: a named Moderator role holding no
  listed permission would be moderatable, and the fix is a role check the bot
  cannot invent for him.
- **May the bot ping a third time** — "your report was fixed"? It assumes
  **no** and uses a pull surface (*My reports*) instead, because invariants 8
  and 20 name exactly two deliberate pings.
