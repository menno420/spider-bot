# Architecture

> **Status:** `binding` for spider-bot · written 2026-09-04 against the owner's
> direction of the same day.
> **Outranked by:** source. When this file and the code disagree, the code is
> right and this file is the thing to fix.
> **Companion:** [`product-shape.md`](product-shape.md) says what the bot is
> *for*; this says how it is built. Canonical intent lives in fleet-manager
> (`docs/repos/spider-bot/intent.md`, `[D-0042]`).

## The one sentence

**The AI supplies judgement. Deterministic code supplies authority. The
repositories supply durable truth.**

Every structural decision below follows from that, and where a decision looks
arbitrary it is usually this sentence being enforced somewhere unobvious.

## The layering

```
cogs/          Discord wiring: listeners, slash commands. Thin.
  |
  v
ui/            Panels, modals, the route registry. Never imports cogs/.
  |
  v
services       intake/ · moderation/ · support · store · evidence · redact · ids
  |            No Discord handle above what it is handed. Testable without a gateway.
  v
primitives     style · audit · config · memory · knowledge · cohort · roster · presets
```

**A correction worth carrying:** `CLAUDE.md` and `README.md` both described the
lower layer as `(presets, roster, cohort, config)`. Measured against real
imports, `ui/` also reaches `audit`, `style` and `ai.safety` directly, and
`config` is imported by **no** `ui/` or `cogs/` file at all — it arrives as
`bot.cfg`, set once in `bot.py`. The rule that actually holds, and the one to
keep, is the direction: **nothing below `ui/` imports `ui/` or `cogs/`.**

## The moderation pipeline

```
Discord event
  → prechecks.should_analyse       deterministic, no model call, no cost
  → classifier.analyse             reads; holds no Discord handle
  → contracts.parse_verdict        typed and validated, or NOTHING
  → policy.decide                  a table of rules, first match wins
  → gate.check                     permission, hierarchy, who the subject is
  → executor.perform               Shadow (cannot act) or Enforcing (can)
  → Discord API
  → cases + audit                  one case per decision, correlation id throughout
```

### Five properties, and why each is structural rather than remembered

**1 · The model cannot reach a mutation.** `classifier.py` imports `contracts`
and the gateway. `operations.py` imports `discord`. Neither imports the other,
and `tests/test_moderation_layering.py` fails the build if that ever changes.
`CLAUDE.md` invariant 5 is not a rule someone has to hold in mind; it is a
property of the import graph.

**2 · Free-form prose is never an action.** `parse_verdict` returns a fully
validated `Verdict` or a **named rejection**. There is no partial verdict, no
lenient default, and `Policy.decide(None)` is the first branch rather than a
fallthrough at the bottom where an edit could slip past it.

**3 · The evidence quote is the anti-hallucination check.** The model must
return the span it judged, verbatim, and it is tested against the exact content
the model was shown. A verdict quoting something the member never wrote is
discarded — which catches an invented message, the wrong message, and an
injected instruction persuading the model to "quote" something. It is the only
defence here that does not depend on the model cooperating.

**4 · Kick and ban are unreachable from the automatic path.** They appear in no
default policy rule, so no combination of category, severity and confidence
produces one. Stronger than a guard clause: there is nothing to bypass. A
moderator can still kick or ban — through `/modact`, as a human action with a
human actor recorded on the case.

**5 · Shadow is a type, not a flag.** `ShadowExecutor` declares no `__init__`,
holds no instance state, and its module holds no Discord handle. There is no `if enforcing:`
in `operations.py` because there is nothing for such a branch to guard.
`executor_for` returns shadow for anything that is not exactly `"enforce"`, so
a typo does nothing rather than acting.

### The policy as data

`DEFAULT_POLICY` is a tuple of frozen rules, printable in the mod console.
`validate()` checks three properties at boot and in tests: no autonomous
kick/ban, no dead rule, no out-of-range threshold. The dead-rule check is
**exact** — does every verdict matching the later rule already match an earlier
one — rather than a severity-ordering heuristic, because a heuristic flagged
two correct rules with disjoint category sets on its first run.

`Policy.ceiling` clamps whatever the table decides. That is the rollout lever:
at `flag_for_review` the entire classifier and policy path runs and nothing a
member can see ever changes.

### What is deliberately NOT built

Discord's own AutoMod handles floods, duplicate spam, mention abuse, invite and
link rules and keyword lists — natively, at the gateway, before delivery.
discord.py 2.7.1 exposes the whole API, so the useful thing this bot does is
**recommend** rules the owner enables (`prechecks.AUTOMOD_RECOMMENDATIONS`),
not duplicate them. The classifier exists for the cases a keyword list cannot
judge: the same words that are harmless or abusive depending on who is saying
them to whom.

## The intake pipeline

```
entry point                          IntakeService              sinks
-----------                          -------------              -----
bug modal        ─┐
feedback modal   ─┤
idea modal       ─┼──►  file() ──►  privacy.apply ──►  store  (durable, private)
private modal    ─┤                                      │
conversation     ─┘                                      └──►  publish() ──► GitHub
```

**One implementation, many entry points.** Every door reaches
`IntakeService.file`. There is no second path that writes a report.

**Store first, publish second, and never the other way round.** A confirmed
report is durable before any network call leaves the process. GitHub is a sink,
not the record: an outage costs a delay, never a report.

```
draft → stored → publish_pending → published
                                 → publish_failed  (retryable, idempotent)
```

**Idempotency takes three mechanisms because GitHub offers none.** There is no
idempotency key and no conditional create for `POST /issues`, so retry-safety
is assembled: the store's own publication record (fast path, authoritative), a
marker search (closes the window where the record was lost after the issue was
created), and `may_publish` going false once an issue number exists. Publication
is serialised per report id. The residual race — two truly concurrent publishes
inside one search window — is stated in `github_sink.py` rather than papered
over; this deployment runs one worker.

**Private by default, at the boundary that matters.** *"The game is way too
hard"* is product feedback; *"this user keeps insulting me"* is not.
Deterministic signals decide, and the AI is a **one-way lever**: it can make a
report private and cannot make one public that the deterministic pass did not
already clear. A model failure arrives as `None` and loosens nothing.
`Sensitivity.UNCLASSIFIED` is the initial value and `is_public_safe` is false
for it — the default is what happens when nothing decides.

**The scanned set is the published set.** `privacy.classify` reads every field
`public_body()` publishes. That equality is a rule, not a coincidence: adding a
field to the body without adding it to the classifier is how an email address
typed into the device box gets published.

**`public_body()` is an allow-list.** It is assembled from named fields rather
than by removing private ones, so a field added later is absent by default
instead of leaked by default.

## Storage

One `Store` protocol; three implementations (`DiscordChannelStore`,
`InMemoryStore`, `NullStore`). Discord is the database: each record is JSON in
a private staff channel, readable by a human without tooling.

**The decision, from the numbers rather than from taste.** The workload is
reports and cases for one server whose target population is 12–16 testers.
Every query is *one record by id* or *all records in one collection*; nothing
needs a join, a range query, an index or a transaction. Postgres would add a
Railway addon, a migration story, a backup story and a second failure mode to a
bot whose operational virtue is having one. **When a query appears that a scan
cannot serve, only `store.py` changes** — that is what the seam is for, and it
is the reason the decision is cheap to revisit.

Two properties worth knowing: an incomplete write is unreadable rather than
readable-as-truncated, and a failed history read is **not** cached as an empty
store — "no reports" and "could not read reports" must not look the same.

## Game truth

`spider-swing` owns the game. This bot **consumes**.

```
spider-swing                                     spider-bot
------------                                     ----------
support/source.json        (curated, by hand)
  → tools/generate_support_feed.py --check       support.SupportFeed
  → support/spider-bot-support-feed.json  ──────►  pins schema 1
     (committed, fail-closed in CI)                refuses what it cannot read
                                                   falls back last-known-good
                                                   then built-in, and SAYS WHICH
```

The consumer half of the pinned-feed contract: pin the version, refuse a shape
you are guessing at, fall back, and never fake data. The staleness line is
non-optional — a model told the build version without being told how fresh it
is states it with the same confidence either way.

## Run evidence

`evidence.py` validates spider-swing's export against that repo's committed
schema and reduces it to a summary. It is untrusted input and treated as such:
size before parse, `NaN`/`Infinity` refused (Python's `json` accepts them by
default), duplicate keys refused (Python's `json` keeps the last silently),
values outside what the game can produce clamped **and** the file marked edited.

Render helpers take a **required** escaper with no default, so leaking
member-controlled markdown into a public issue is a `TypeError` rather than a
forgotten keyword.

## Observability

Every event is one JSON line on stdout, which Railway parses into structured
fields. A **correlation id** minted at the edge travels the whole way, so
`interaction → intake → GitHub issue` and `message → verdict → action → case`
are each one grep. There was no such concept before this work.

Distinguishable failures, each by its own name rather than as a generic error:
AI provider unavailable · classifier output invalid (by rejection kind) ·
policy denied · Discord permission missing · GitHub unavailable · GitHub
credential missing · publication pending · moderation action failed · support
feed unavailable · support schema incompatible · stale cached knowledge · store
unavailable.

## Non-goals

Economy, games inside the bot, XP, casino, a web dashboard without a current
need, general-purpose platform abstractions, arbitrary AI shell or database
tools, autonomous server redesign, rebuilding what Discord does natively, and
**a second source of Slingy Spider product truth**.
