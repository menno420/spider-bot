# Plan — onboarding, UX, and the companion site

> **Status:** `plan` · written 2026-08-24 · owner-directed.
> **Scope:** what Spider Bot and a new companion website should become, in what
> order, and why. Ranked against one number (below).
> **Outranked by:** the repo's `README.md` and `CLAUDE.md` on how things
> currently work, and `docs/product-shape.md` on what the bot is *for*.
> Source code wins over this file. When they disagree, fix this file.

---

## 1. The one number

**Google will not let Slingy Spider leave closed testing until 12 testers stay
opted in for 14 continuous days.**

Today: **1 holder of the Slingy Tester role, and it is the owner's own test
account.** Zero real testers. The 14-day clock has not started.

Everything below is ranked by whether it moves that number. A feature that does
not is not wrong — it is *later*.

## 2. What two independent design reviews said

Gemini and Grok were each given the same brief, separately, and asked for the
single biggest mistake we were about to make. They did not see each other's
answers and returned the same finding:

> **Grok:** *"Treating the consent + daily AI scan as a core product feature
> instead of a quiet background tool… If the first interactive experience after
> joining is a consent question about AI reading their messages, you will lose
> people before they ever press the 'I've opted in' button that actually matters
> for the Google 12×14 requirement."*

> **Gemini:** *"Over-engineering an AI chat scraper for a 3-person server instead
> of solving the 12-person Google Play funnel… your single existential failure
> mode: testers dropping out on day 9 because their Google account mismatched."*

The consequence is **sequence, not cancellation**. The AI conversation review
stays in the plan. It moves last.

The game repo reached the same conclusion independently, months earlier, about
distribution:

> *"That filters by 'has a GitHub account', not by 'would enjoy this game.'"*
> — `spider-swing/docs/ideas/distribution-and-first-contact-2026-08-01.md`

and, the line this whole plan hangs on:

> *"The deeper someone got into the funnel, the better the signal became."*

## 3. The four surfaces and who owns what

| Surface | Where | Job |
|---|---|---|
| **The game** | `menno420/spider-swing` (public) | The product. Also the **content library** — design docs, spider biology, zone art, the approved store listing. The site sources from here; it does not re-write it. |
| **The bot** | `menno420/spider-bot` (public) | In-server onboarding, feedback capture, the closed-test clock, the AI face. |
| **The server** | Discord guild `1541447750628147351` | Where the cohort actually lives. Structure is the owner's, not this plan's. |
| **The site** | new, `spider-bot/site/` | **Recruitment** (top of funnel), orientation, and closing the loop back to testers. |

**Hard boundary, copied from superbot:** the site never imports the bot's code and
only ever reads a public data file. *"Even a fully-compromised public site cannot
reach the bot, its database, or any secret."*

---

## 4. Phases

### Phase 0 — repairs (hours)

Three defects verified live in the current build. Do these first; they are cheap
and two of them are user-visible today.

1. **Dead sub-panels.** `PresetPanel` and `ConfirmPost` are sent without binding
   `view.message`, so `on_timeout` is a no-op — after three minutes the buttons
   stay clickable and do nothing. `/home` already binds it correctly. *Two lines.*
2. **The AI can barge into someone else's conversation.** The initiative path in
   `#general` fires on a keyword match with no "is this addressed to someone
   else?" check, and strips only the bot's *own* mention, leaving other users'
   `<@id>` tokens in the text so the model can narrate a ping that never happened.
   This is superbot's **BUG-0019 #1**, still open there, and it is the single item
   blocking their AI feature from certification. Fix: skip when a message mentions
   another user or bot and not us; strip all mention tokens; pass explicit
   "you were not mentioned" framing.
3. **No embed clamping.** A field over 1024 chars, or 6000 total, makes Discord
   reject the *whole message* with a 400. Inside a panel edit the edit never lands
   and the UI freezes with no error. superbot hit this twice in production; one
   incident meant a panel's Back button never rendered. Port `safe_defer` /
   `safe_edit` / `safe_followup` / `clamp_embed` (~150 lines, no dependencies).

### Phase 1 — onboarding and the visual system

This is the phase the owner named as the focus.

- **Back-navigation.** Port `attach_back_button` + `carry_back` + `chain_back` and
  auto-attach in `Panel.__init__`. ~180 lines, no database, no state. Back-nav is
  *rebuild at click time*, never replay of a snapshot. Closes the dead-end found
  by walking the server as a real member.
- **Expiry copy.** On timeout, disable *and* say so: "This panel expired — open a
  new one with `/home`." Also state the lifetime up front in the footer.
- **Pin the panel.** Run `/panel` in `#start-here`. Today nothing tells a newcomer
  the bot exists. *(Owner action — one command.)*
- **Fix the contradiction.** `#start-here` tells people to *type* "opted in";
  the panel offers a *button* for the same thing. The pinned text must point at
  the button.
- **Welcome flow.** On join: a warm greeting that offers exactly one next step.
  Grok's sequence: *welcome → the one button that matters → tester role → and only
  then, or after 24h, the consent panel, once.*
- **The visual system** (§5) applied everywhere, plus the guard tests:
  every route reachable in ≤2 clicks; every panel exposes at least one real
  action (superbot fails CI on `instruction_only` panels); no dead ends.

### Phase 2 — the companion site

See §7. This is the recruitment surface and therefore the highest-leverage work
after Phase 1.

### Phase 3 — the consent panel

Surface only, no scanning yet. Button-driven, shown *after* the tester role or
after 24 hours, once, never repeated unless the role is removed. Copy in §6.

### Phase 4 — the daily conversation scan and digest

Last. Only worth building when enough people have opted in for it to produce
anything. Grok's warning, recorded so it is not a surprise: with 12 testers most
days only 3–4 will have opted in, so *"the system will look broken even when it is
working as designed."* The staff digest must therefore carry a visible
**"partial context"** flag.

---

## 5. Visual identity — locked

Both reviews produced palettes. They are reconciled here, with one deliberate
correction: Gemini proposed an **orange** brand colour alongside a **gold**
warning colour. On a thin embed stripe on a phone those read as the same signal,
so the brand colour is green and orange is reserved for "you need to do
something".

| Role | Hex | Used for |
|---|---|---|
| Brand / primary | `#1A8F5C` | The mascot's voice, the happy path, primary buttons |
| Success | `#2ECC71` | Confirmed, opted in, feedback sent |
| Warning | `#F39C12` | Needs your attention, something is unset |
| Alarm | `#E74C3C` | Tester lost, streak broken, error |
| AI | `#9B59B6` | Every message the AI authored — no exceptions |
| Structural / neutral | `#34495E` | Hubs, settings, read-only status |
| Accent (rare) | `#00D4AA` | Mascot glow, milestone moments only |

**Fixed emoji vocabulary. Nothing outside this set appears on a public surface.**

`🕷️` the bot itself · `🕸️` webs, links, joining · `✅` success/confirmed ·
`⚠️` warning · `🚨` alarm · `🐛` bug report · `💬` feedback / AI speaking ·
`❓` question / help · `📊` status / the clock · `📢` announcement / preset ·
`⚙️` settings / staff tools

**Embed rules — what makes it look designed rather than default:**

- Every public embed carries the **same author name and icon** (the spider) and
  the **same footer format**. Never "Bot" or "System".
- Success embeds start with a **verb**: "Opted in", "Feedback sent". Never
  "Success!".
- Disabled buttons state **why in the label** — "Cooling down 1m" — not just grey.
- Panels **edit in place** and never leave a trail of dead messages.
- Colour is semantic only. Green is never decorative.
- **The AI never speaks without the purple accent and `💬`**, so a reader can tell
  at a glance who is talking.
- **Inline two-column fields** for paired data (`Days active: 8/14 | Status:
  Holding`) — never stacked singles, which look broken on a phone.
- Route status and admin cards to **dedicated read-only channels** so they are not
  buried when the server grows; keep member-facing `/home` output ephemeral so 50
  people troubleshooting Google accounts do not spam the channel.

**Tone.** A nimble, scrappy arcade spider — not a butler and not a log emitter.
Mascot voice is reserved for **greetings, milestones and AI replies**. Operational
output (the clock, status, tester logs) stays concise and clean. The line between
charming and annoying is **unprompted messages**.

**Rendered welcome image cards: not now.** Both reviews and superbot's own welcome
unit (which ships its card **off by default**) point the same way — blurry on
high-DPI phones, bandwidth per join, breaks when an avatar URL fails, and does not
respect dark mode. Invest in embed craft instead. Revisit if the server grows.

**Reaction roles: not adopted.** They cannot re-check permission on click, have no
disabled or loading state, cannot edit in place, and are easy to miss on a phone.
The need they were meant to serve — an always-available, low-friction opt-in that
does not expire — is **already met by the pinned `/panel`**. This is a
simplification of the original plan, not a loss of capability.

---

## 6. Consent — locked copy and mechanics

**Copy** (Grok's, lightly edited; fits one phone screen without scrolling):

> **Help us improve Slingy Spider**
>
> We read public chat once a day to find real feedback about the game.
> Only if you say yes.
>
> Your messages get sent to an AI. It writes a short summary for the team.
> We never keep the original messages.
>
> You can turn this off any time.
> Age: you must be 16 or older to say yes.
>
> `✅ Yes, you can read my chat`   `❌ No thanks`
>
> *This is optional. You can still play and give feedback other ways.*

No "learn more" link that dumps legalese. No jargon. That is the whole string set.

**Mechanics**

- Opt-in is a **role**, so it is visible and self-revocable.
- Granted by **button** (the source of truth — it records the exact moment and
  shows the full wording). Not by reaction.
- Shown **after** the tester role, or after 24 hours — never as the first
  interactive experience.
- **Redact non-consenters** (owner decision): only opted-in members' own messages
  are sent; anyone else's line becomes `[a member who hasn't opted in]` with the
  words removed. Both reviews confirmed the two alternatives are worse. Both also
  warned it degrades summary quality — hence the "partial context" flag.
- **Process and discard.** Only the derived summary is kept. Raw messages are
  never stored.
- **Channel allow-list.** Named public channels only. Never staff channels,
  never DMs.
- **Withdrawal is instant, not retroactive** — and the UI says so plainly.

---

## 7. The companion site

### 7.1 What it is for, in priority order

1. **Recruit testers.** The bottleneck. Make a stranger want to play, then give
   them a one-tap route into the closed test.
2. **Orient them.** The four join steps, the wrong-Google-account trap, and
   troubleshooting — in a linkable place that is not buried in Discord history.
3. **Close the loop.** Show what testers said and what changed. This is where the
   daily digest surfaces.

Plus the reason people stay: **the game itself** — spiders, zones, upgrades,
screenshots, video, tutorials.

### 7.2 Content map — sourced, not invented

Almost all of this already exists in the public game repo. The site is a
**curation** job.

| Page | Source |
|---|---|
| Home / what is this | `docs/product/play-store-listing.md` (owner-approved copy) + gameplay video |
| Meet the spiders | `docs/product/spider-biology-folio.md` (21 KB — the spiders are built on real biology) + `assets/runtime/characters/*` (garden, ballooner, trapdoor, burrowing, magnolia jumper) |
| The world | `docs/product/zone-progression.md` + `assets/runtime/zone-art/*` (ashen hollow, deep mist, ruined arboretum, forest, bramble canopy) |
| Upgrades | `docs/product/economy-model.md`, `docs/product/upgrade-and-difficulty-research-2026-08-02.md` |
| How to play / tutorial | `docs/game-design/Spider-Swing-GDD-v2.0.md`, the tutorial session notes |
| Join the test | The four steps + the account trap. Same copy as the bot's preset, so they cannot drift |
| Feedback | What testers said, what changed — fed by the digest |
| Privacy | §8 |

### 7.3 Architecture

Deliberately boring, because the owner is a non-coder and the site must stay
editable.

- **Lives in `spider-bot/site/`**, mirroring superbot's `botsite/`. One repo, one
  mental model.
- **Its own Railway service**, with **watch paths** so the bot service only
  redeploys on `spiderbot/**` and the site only on `site/**`. Without this, a
  daily digest commit would redeploy the live bot every day. *(This is a real
  trap — it is why watch paths are not optional.)*
- **Static HTML and CSS, no build step.** superbot's own owner-facing doc makes
  the case: plain JavaScript means "just open `index.html`" works, while the React
  design-system needs building. A no-build site is far kinder to maintain.
- **Content in markdown files** the owner can edit directly in GitHub's web UI.
- **One small server endpoint** for the email form (below). Everything else static.
- **Crawlable HTML with real Open Graph tags — not a client-rendered SPA.** This
  is the one place we must NOT copy the donor: its site is hash-routed and renders
  client-side, with a single static title and description for the entire site. Our
  recruitment mechanism *is* someone sharing a link, so the landing page must be
  real HTML with per-page titles and an OG image, or every share shows a blank
  card. Reserve client-side rendering for the digest only.
- **Never name an asset folder `static/`.** The estate's root `.gitignore` ignores
  it; a service once referenced a `static/` mount that existed locally, was never
  committed, and crashed on boot.
- **Copy the safe-load seam.** The donor's `data_loader.py` (~90 lines, stdlib) is
  the single best file to lift: a missing or corrupt data file returns a populated
  empty shape rather than raising, so every page can rely on the keys existing.
- **Designed so a playable web demo can drop in later** without a rebuild — see
  §9.

### 7.4 Email capture

The site's most important interactive element: *"tell me when there's a slot."*

- Form posts to the site service. The service validates, rate-limits, and forwards
  to a **private Discord channel via a webhook held server-side**. The webhook URL
  is never in the page.
- **No database.** Discord is the store — the same philosophy the bot already uses
  for the tester clock.
- **Copy the form hardening stack, minus the database**: a hidden honeypot field,
  a per-IP sliding-window rate limit, a *pure* validator (returns data and touches
  nothing, so it is unit-testable), length caps, a control-character strip, and a
  post-redirect-get so a refresh cannot re-file. All stdlib, all in the donor. Copy
  its **dormant-by-default** posture too: if the destination is unconfigured, show
  a friendly "not open yet" state, never an error. What we do NOT copy is its
  Postgres — built for a public multi-tenant abuse surface, and never switched on.
- The owner then invites them to the Play closed test.
- `slingy-spider-contact@googlegroups.com` already exists and forwards to the owner
  while keeping his personal address and the member list private. Use it as the
  page's contact route.

### 7.5 The digest pipeline — no database

Copying superbot's boundary exactly, with one security improvement:

```
bot (Railway worker)
  │  writes a public, safe summary
  ▼
a separate tiny public data repo        ← NOT the bot's own repo
  │  the site reads it
  ▼
site/data/digest.json  →  the Feedback page
```

Ship a small `digest_contract.json` alongside it listing the guaranteed fields and
a `schema_version` the bot stamps into every digest. A producer-side rename
otherwise blanks the page silently — that exact failure is recorded in the donor,
where a consumer treated two fields as lists when the feed shipped dicts and its
counters quietly showed nonsense. And render an absent or unrecognised digest as an
honest "no digest yet", never as zeroes: a fabricated number is worse than a gap.

**Why a separate data repo rather than the bot's own:** giving the bot write
access to its own source repo is an escalation we do not need, and a commit to
`main` is a production deploy. A scoped token on a data-only repo cannot touch the
bot's code. Rolling history comes free from git history — no database, ever.

### 7.6 How we build it

The owner's approach, adopted:

1. Write **one site spec** — the content map, the locked palette and emoji set,
   the page list, the copy sources.
2. Give the **same spec** to **Gemini in Google AI Studio** and to **Grok**, each
   asked to produce a complete static site. Same input so the outputs are
   comparable.
3. **Compare and merge** — take the better structure from one, the better visual
   treatment from the other.
4. **Finish by hand**: inject the real palette and emoji, swap in real art from
   the game repo, replace placeholder copy with the approved store-listing text,
   and check it on a phone.

Two things the generators must be told explicitly, or they will invent them:
the **exact hex palette and emoji set** from §5, and that **all copy comes from the
game repo** — nothing about the game may be invented.

---

## 8. Privacy and legal

**The game's existing privacy policy is scoped to the app and stays true.** It
says, verifiably, that the game collects nothing and sends nothing off-device.

**But the site and the bot are two new processing activities that policy does not
cover:**

| Activity | Data | Needs |
|---|---|---|
| Site email signup | An email address | Purpose ("to invite you to the test"), retention ("until you are invited or you ask us to delete it"), no marketing, a deletion route |
| Bot AI conversation review | Public Discord messages of opted-in members | The consent copy in §6, plus a plain statement of who the AI provider is |

Both are small. Both are awkward to retrofit. **Write one short "Slingy Spider
online services" statement covering the site and the bot, and link it from the
site footer and the consent panel.** Google Play only asks about the app; GDPR
asks about all three.

Also settled: **16+ is stated in the consent copy** (Netherlands sets the digital
consent age at 16; Discord's floor is 13).

---

## 9. Risks and traps

- **Watch paths — measured, not theoretical.** The donor estate ran a workflow
  that committed a refreshed data file on a schedule, with no watch filter on the
  bot service. It rebuilt and restarted the production worker **~293 times in one
  billing cycle**. Set watch paths, or keep the digest in a separate repo. §7.3.
- **A red CI check can silently freeze deploys.** Railway waits for the *whole*
  check suite. In the donor, one workflow pushing straight to a protected branch
  turned every commit's suite red, which skipped every production deploy — the bot
  ran on stale code for about nine hours with no error anywhere. If anything
  automated ever commits here, land it through an auto-merging PR, never a direct
  push.
- **Make the digest timestamp deterministic.** Derive it from the data, not from
  `now()`. The donor had to fix exactly this so that "no diff" reliably meant
  "genuinely unchanged" rather than "only the wall clock moved".
- **The bot must not hold write access to its own repo.** §7.5.
- **Consent placed too early kills the funnel.** Both reviews. §4 Phase 3.
- **`/panel` has still not been run.** The entire button surface is invisible to
  members until it is. *(Owner action.)*
- **The playable web demo is unbuilt.** The game repo floats it — *"a link that
  plays instantly in a mobile browser with no install would be transformative for
  sharing"* — but `export_presets.cfg` contains **only Android Debug and Android
  Release**. There is no Web target. Treat it as an unverified experiment worth one
  timeboxed attempt, not a plan dependency. If it ever works it changes the funnel
  more than anything else in this document.
- **Doc drift.** superbot's docs disagree with its code in several places its own
  team documented. Keep this plan short enough to maintain, and let source win.

## 10. What only the owner can do

1. Run `/panel` in `#start-here` and pin it.
2. Decide whether the site lives at a domain he owns, and register it if so.
3. Recruit the first real testers — no bot does this.
4. Approve the site copy before it goes public.
5. Read and publish the privacy statement in §8.

## 11. Open questions

- Domain name and hosting for the site — Railway, GitHub Pages, or elsewhere?
- Digest cadence: daily was assumed, but with a small server **weekly** may read
  better and cost less. Decide when Phase 4 starts.
- Whether to pay testers, as floated in the game repo's distribution doc, and if
  so whether the site is where that offer is made.
