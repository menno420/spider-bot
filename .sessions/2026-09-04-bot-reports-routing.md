# 2026-09-04 — #3 merged at the owner's word and verified live; bot reports get their own tracker

> **Status:** `in-progress` — branch `claude/spider-bot-bot-reports`, born red.
> Flips `complete` after the PR is green, reviewed once, and the deployment of
> this change is verified by hash.

- **📊 Model:** fable-5 · xhigh · feature build
- **📍 Venue:** cloud-container
- **🔗 Session:** [session_01XtUDb1BxPVdjkGryVWCKVu](https://claude.ai/code/session_01XtUDb1BxPVdjkGryVWCKVu) · "Spider Bot PR review and merge"

**What this session is about.** The continuation of the AI-operations tranche:
review spider-bot#3, put its decisions to the owner, act on his word. He
answered four questions live (2026-09-04, ~18:40Z): **merge now** · publication
consent **as built** · a report about the bot goes to **spider-bot's own
tracker** · tester ideas **do** reach spider-swing's tracker, labelled.

## What was done before this branch

- **Review of #3** landed as `8937191` on its own branch: the owner page named
  `#mod-cases` where the bot resolves `#case-state`; `.railway/railway.ts`
  `preserve()`d only the three live variables while Railway IaC is *omit means
  delete* (measured read-only with `railway config plan`); four docs named
  `railway.json`. fleet-manager's card for the review is
  `.sessions/2026-09-04-spider-bot-pr-review.md` there.
- **#3 merged** at his word: merge commit `5a7f8a28`, 2026-09-04T18:42:14Z.
- **Deployment verified by hash**, not by status — measured here at 18:43Z
  (deployment `6f5c7648`, `meta.commitHash == 5a7f8a28 == main`), and written
  into the tranche-1 card's *Deployment outcome* by the build session, still
  running in parallel, minutes later as spider-bot#4 — the same hash and the
  same `ready` line, neither session aware of the other. This PR's own rewrite
  of that section was dropped at the merge in favour of the one that landed.

## What this branch does

His third answer is a code change: **a report about the bot goes to
`menno420/spider-bot`'s tracker.** Built as one category and one routing point:

- `Category.BOT_PROBLEM` + `Target` + `Report.target` (`intake/models.py`) —
  the category alone decides the tracker; the text never does.
- `IntakeService(…, bot_github=…)` with `client_for` / `repo_for` /
  `can_publish` — one client per target; `publish` picks by the report. A
  missing bot tracker refuses a bot report **by name** (`no_credential`) and
  keeps it queued; it is never sent to the game's tracker instead.
- `Config.github_repo_bot` ← `GITHUB_REPO_BOT` (default `menno420/spider-bot`);
  `bot.py` builds both clients under the same two locks
  (`INTAKE_PUBLISH_ENABLED`, `GITHUB_TOKEN`); the retry loop asks
  `intake.can_publish` instead of reaching into a private client.
- `/report` gains *a problem with Spider Bot* → `BotProblemModal` (its own
  form, three fields, carries `PUBLIC_NOTICE`); `/publish` names the tracker
  it is about to post to (`service.repo_for(report)`) instead of assuming the
  game's.
- `privacy.py`: `bot_problem` is publishable under the same person-signal
  rules; the public-safe reason says *about the bot*.
- Invariant **56** in `CLAUDE.md`; `docs/rollout.md` step 1 scopes the token
  to both repositories, step 2 wants the label in both, and the three answered
  questions are marked answered with what was built; `docs/what-changed.md`,
  `README.md`, `.env.example` follow.

**Ships off, like everything else:** no token exists, so a bot report is
stored and queued exactly like a game report. Nothing here changes what the
live worker does until rollout step 3.

## Verification

*(filled at close — real exit codes, the review round, the deployed hash)*

## Deployment outcome

*(filled at close: `meta.commitHash` of the deployment that follows this merge
must equal the merged HEAD)*
