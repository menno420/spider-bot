# 2026-09-04 — #3 merged at the owner's word and verified live; bot reports get their own tracker

> **Status:** `complete` — branch `claude/spider-bot-bot-reports`, born red and
> flipped here as the deliberate last step: spider-bot#5 green at `9c67843`,
> two Codex rounds and one Gemini pass answered, residue disclosed on the PR.
>
> **Deployment of THIS change is not yet verified at the flip** — it cannot be
> before the merge. The follow-up records-only commit writes the hash under
> *Deployment outcome* below, the same way spider-bot#4 did for #3.

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

**Gate at `9c67843`, each read from its own exit code:** `ruff check .` 0 ·
`python -m pytest` 0 (the run's own count (see the gate line), from 669 at the start of the day) ·
`python -m compileall spiderbot` 0 · `python docs/journeys.py` 0. CI `quality`
green on every pushed head.

**External review, three rounds, all answered on the PR with a countable
table each:**

| round | head | returned | disposition |
|---|---|---|---|
| Codex 1 | `d1556b3` | 1 P1 · 5 P2 | 6 fixed, 0 refuted, 0 open |
| Codex 2 | `f38a580` | 3 P1 · 3 P2 — every one against a round-1 fix | 6 fixed, 0 refuted, 0 open |
| Gemini (free key) over round 2's fixes | `26822cf` | 3 | 2 fixed, 1 conceded on wording, 0 open |

Codex's cap for this PR reads three in the guard because one request was
composed in a command whose gate branch failed and never posted; two real
rounds ran. Recorded in fleet-manager's card as the session idea — the guard
counts intent, not posts.

**The five that mattered here, and they are one shape again:** the P1s were
all in the fix I had just written — the conversational pre-sort (two
whole-message searches, then a missing `crash`, then a hyphen), and an offer
that disclosed the wrong tracker before recording consent. A fix moves a
problem; the new place has not been looked at. And the finding I did not
ask for and could not have: `may_publish` refused a permanently-failed report
for ever, so the panel section I added to *show* stuck reports pointed at
nothing anyone could *do*. A person's approval now clears the failure.

**Residue:** the Gemini-round fixes at `9c67843` have had no external review.
No real Discord interaction and no real GitHub call has been made from this
branch.

## Deployment outcome

*(filled at close: `meta.commitHash` of the deployment that follows this merge
must equal the merged HEAD)*
