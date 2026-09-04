# 2026-09-04 — Spider Bot becomes the AI operations bot: intake, moderation, evidence

> **Status:** `complete` — branch `claude/spider-bot-ai-ops-sthix0`, born red and
> flipped here as the deliberate last step: CI green at `204227d`, all 48
> external findings answered, and the residue disclosed on the PR rather than
> left for the reader to discover.
>
> **Deployment is NOT yet verified** — that is the one item this card hands
> forward, and it is written up under *Deployment outcome* below rather than
> claimed.

- **📊 Model:** opus-5 · xhigh · feature build
- **📍 Venue:** cloud-container
- **🔗 Session:** [session_01YCXH5D4omEgguaPYHwVz6d](https://claude.ai/code/session_01YCXH5D4omEgguaPYHwVz6d) · "Spider Bot AI operations bot"

**What this session is about.** The owner gave Spider Bot its purpose, live, and
it is newer than every record in this repo or in fleet-manager:

> *"Spider Bot exists to manage the Slingy Spider server and help during testing
> of the game. It should become a reliable automoderator with heavy AI
> integration. People should be able to talk naturally to it for guidance,
> complaints, bugs, feedback and improvement ideas. Those reports should become
> durable, easy for the developer to find and act on — preferably through GitHub
> or an equally clear developer-facing system."*

Canonical intent and the four rules it generates are recorded in fleet-manager
(`docs/repos/spider-bot/intent.md`, `[D-0042]`). The sharpest of them: **heavy
AI integration does not mean a language model with Discord permissions.** The AI
supplies judgement; deterministic code supplies authority. `CLAUDE.md`
invariant 5 is *refined*, not deleted.

## Baseline before any change — real exit codes, at `bf4d7527`

```
ruff check .                    → 0   (All checks passed)
python -m pytest                → 0   (246 passed)
python -m compileall spiderbot  → 0
```

246, not the 116 this repo's `README.md` claimed and not the 78 fleet-manager's
entry point claimed. Both corrected in this session.

## What was done

**The build**, in dependency order: shared foundations (stable ids, a storage
seam, a GitHub client, typed AI verdict contracts, a policy layer, correlation)
→ the developer feedback loop (one intake service behind every entry point,
conversational filing, privacy classification, store-first, idempotent GitHub
projection, human-approved publication) → the AI moderation foundation (event
logging, classifier, deterministic policy evaluator, shadow mode, one case
model, a staff review surface) → the game-knowledge seam (a versioned support
feed produced by `spider-swing`, consumed with a last-known-good fallback) →
run-evidence import.

**Nothing new enforces on arrival.** `MOD_MODE=off`, autonomy ceiling
`flag_for_review`, GitHub fail-closed until a token exists. Kick and ban are
unreachable from the automatic path — no combination of category, severity and
confidence produces one, which is stronger than a guard clause because there is
nothing to bypass.

| | |
|---|---|
| Commits | 22 |
| Tests | 246 → **669** |
| External findings | **48**, all reproduced here first, all fixed with a test verified to fail when the fix is removed |

**The review is most of what this session was.** Eight independent Opus lanes
plus a synthesis pass returned 41; three Codex rounds returned 15, 17 and 8;
one free-key Gemini pass over round 3's own fixes returned 7, of which four
were real and three were wrong about the library or about this code — recorded
as wrong, with what they were checked against, because filing a hypothesis as a
defect is the same mistake in the other direction.

**The five that mattered, and they are one shape:**

1. `classifier.SYSTEM` reached the model on **no call ever made** — a `mode`
   dispatch bug routed every moderation call down the chat path.
2. The human publication gate **approved by report id**, so requiring a person
   swapped a classifier publishing unseen content for a *person* publishing
   unseen content.
3. A masked link passed both escapers — in markdown, and then in HTML — into a
   public issue under the bot's name.
4. Two members filing at the same instant could leave a report **durably stored
   and invisible to every read path**, after its reporter had been told it was
   saved.
5. `PUBLIC_NOTICE` pushed three modal placeholders past Discord's 100-character
   limit and made the publishable forms unopenable — my own fix, caught by
   round 2. `ComplaintModal` was at 104 characters **before** that change, so
   the private-report form had never opened at all.

Four of the five were **documented** protections. A docstring asserting a
property is the cheapest possible way to stop looking for its absence — and the
second-order version showed up too: three of round 3's eight findings were
consequences of round 2's fixes, and two of Gemini's four were consequences of
round 3's. A fix moves a problem; the new place has not been looked at.

**Invariants**: 21 → **55**. `docs/what-changed.md` is the owner-facing page.

## Deployment outcome

**Not verified — and deliberately not claimed.** The PR was left for the owner
rather than merged: this bot is live in a real server, `main` deploys straight
to production with no gate, and the four things this branch most needs are his
decisions, not mine (two private channels, a GitHub token, and whether to point
moderation at any channel at all).

When it merges, the check is `meta.commitHash` on the new Railway deployment
equalling HEAD — **not** the deploy status. And note the honest complication:
`.railway/railway.ts` sets watch patterns (`spiderbot/**`, `requirements.txt`,
`.python-version`), and this branch touches `spiderbot/`, so it
**will** deploy. Preserved exactly; a docs-only follow-up deliberately will not.

## Next session

1. **Do not go to `enforce`.** Run `shadow` and read `/home` → Cases. Every case
   is markable and that tally is the only honest basis for enabling anything.
   Turning it on because the code works is the failure this design is arranged
   against — and the residue disclosed on the PR says why: **no real model call
   has ever been made**, so the false-positive rate is unmeasured.
2. **Report → fix → tell them.** The private return path is stored and unused.
   When an issue closes, the bot could tell the person who reported it, where
   they reported it. That is the thing that would make people report more.
3. **Gemini's four fixes have had no external review.** They are the newest code
   in the branch.
