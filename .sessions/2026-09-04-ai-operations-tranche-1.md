# 2026-09-04 — Spider Bot becomes the AI operations bot: intake, moderation, evidence

> **Status:** `in-progress` — branch `claude/spider-bot-ai-ops-sthix0`, born red.
> Flipped to `complete` as the deliberate LAST step, after CI is green, the
> adversarial review is answered and the deployed SHA is verified.

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

<!-- filled at close -->

## Deployment outcome

<!-- filled at close: meta.commitHash vs merged HEAD, or "docs/tests only, watch
     patterns deliberately did not deploy" -->

## Next session

<!-- filled at close -->
