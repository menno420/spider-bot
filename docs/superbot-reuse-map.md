# superbot reuse map — server and admin functions

> **Status:** `survey` · written 2026-08-25 · owner-directed.
> **Why this exists:** the owner asked that Spider Bot "get most of superbot's
> server and admin functions before being called ready". superbot has 24 cog
> packages and 60+ cog files. "Most of superbot" is not actionable until it is
> a list, so this is the list, with what each thing costs and what it collides
> with. Read via `gh` at superbot HEAD 2026-08-25; **the donor is never edited.**
> **Outranked by:** `CLAUDE.md` invariants and `docs/product-shape.md`.

---

## 1. Two corrections to the assumptions this started from

**The mechanics exist in superbot; the memory does not.** Join recognition,
audited role writes, hierarchy/permission preflight and the grant path are all
there and worth copying. What is absent is any record of what a departing
member had: no `restore_roles`, no role snapshot, no rejoin handler. The only
`on_member_join` listeners are `welcome_cog` (greeting + counters) and
`security_cog` (join screening). The three role features it does have are each
a different thing:

| Feature | What it really does |
|---|---|
| `role_cog` autorole | Grants one configured role to everyone on join |
| `role_grants_cog` | Grants a role **temporarily**, sweeps it off at expiry (5-min tick) |
| `services/role_automation.py` | Time/XP **threshold progression** (30 days → Regular) |

None of them remembers what a departing member had. So the *plumbing* is a
port and the *memory* is new work — roughly 150 lines plus a storage seam.

**Restoring the tester role automatically would be a defect, not a feature.**
`Slingy Tester` is a mirror of who is actually opted in on Google Play,
granted by hand after the owner verifies the Play Console count. A member who
leaves Discord and rejoins has not necessarily kept their Play opt-in. Auto-
regranting would inflate the roster against reality — the exact failure
invariant 5 exists to prevent, on the one number the whole project is ranked
against. **Sticky roles must carry an exclusion list, and `Slingy Tester` is
permanently on it.**

## 1b. What "verify a tester" can and cannot mean

**The bot cannot ask Google.** Play Console exposes an aggregate opt-in count,
not a per-user status, and the cohort is managed through a Google Group whose
member list needs Workspace credentials plus an email-to-Discord mapping we do
not have and should not start collecting. There is no API path to "is this
Discord user opted in right now".

So verification is necessarily human, and the useful thing the bot can add is
**memory of it**. Today that memory is Discord's audit log, which retains ~45
days - shorter than a tester's full lifetime on the project. A verification
record fixes that and answers three questions the bot currently cannot:

- Was this person ever verified, when, and by whom?
- Did they hold the role continuously, or has it lapsed and returned?
- On rejoin: were they a verified tester before they left?

**Which makes the rejoin case safe.** The bot does not silently re-grant
`Slingy Tester` - it restores the ordinary roles automatically and raises the
tester role to the owner in `#mod-log`, saying plainly that it did *not* act,
who returned, and what it did restore. The owner still decides, but he decides
from a record instead of from memory.

*Shipped as a prompt, not a button.* A one-press re-grant needs a persistent
view keyed to a user id (`discord.ui.DynamicItem`), and that path cannot be
exercised without a real leave-and-rejoin, so it is deliberately the follow-up
rather than the first cut. The natural home for it is a `Route` on Home -
persistent by construction, and testable - rather than bespoke view plumbing.

## 2. The storage question, which gates half of this list

Spider Bot deliberately has no database. `product-shape.md`: *"Jobs 3 and 4 are
where Postgres genuinely becomes necessary - not before."* Several requested
features need durable memory: warnings, mod-log history, role snapshots.

| Option | Cost | Verdict |
|---|---|---|
| Postgres (what superbot uses) | Railway addon, ops surface, backups | Right answer eventually, overkill at 3 members |
| Railway volume + JSON | Cheap, but single-instance and manual backups | Weakest: silent data loss risk |
| **Discord as the store** | Zero infra; state written as messages in a private channel and read back | **Recommended now** |

Discord-as-store is already this estate's stated philosophy — the plan says it
outright for the site's email capture (*"No database. Discord is the store"*)
and the tester clock already reads Discord's audit log instead of a table. At
this server's size the volume is trivial. The seam is swappable: keep every
read/write behind one module so a Postgres move later is a file, not a rewrite.

## 3. The map

**Port cost** is rough implementation size in this repo, not superbot's line
count — the donor's cogs are thin shells over service layers we do not have.

| superbot | What it does | Needs storage | Cost | Verdict |
|---|---|---|---|---|
| `welcome_cog` | Greeting + farewell + join counters | no | — | **Already ours, better.** Ours carries the one button that matters; superbot's ships its card off by default |
| `role_cog` autorole | One default role on join | no | S | **Take.** Cheap, useful the moment strangers arrive |
| `role_grants_cog` | Temporary roles with expiry sweep | yes | M | Later. No current need |
| `role_automation` | Time/XP threshold progression | yes | L | **Skip.** Needs an XP system we deliberately do not have |
| `logging_cog` | Server event logging to a channel | no | S | **Take.** Joins, leaves, role changes, deletes — this is what makes a kick legible after the fact |
| `moderation_cog` | warn / timeout / kick / ban / unban / clearwarnings / modlogs | warn+logs: yes | M | **Take, converted.** All seven are prefix commands; the ledger excludes prefix commands and our doctrine is buttons. Port as a panel + slash |
| `automod_cog` | Message filter engine | no | L | **Skip.** Discord's own AutoMod does this natively and better; product-shape lists it as a standing non-goal |
| `security_cog` | Join screening (raid/alt detection) | partial | L | Defer. Real value only under attack; a 3-member private server is not a raid target |
| `settings_cog` / `setup_cog` / `quicksetup_cog` | Config wizards | yes | L | **Skip.** Built for multi-guild provisioning. Our config is env vars for one guild |
| `server_management_cog` | Hub over channel/role management | no | M | Defer. Thin value over Discord's own UI |
| `cleanup_cog` | Bulk message purge | no | S | **Take.** Genuinely tedious by hand |
| `starboard_cog` | N ⭐ → hall-of-fame channel | yes | M | Later. Needs a community big enough to star things |
| `counters_cog` | Channel names showing live stats | no | S | **Take, adapted** — a channel showing `Testers: 1/12` puts the one number where he cannot miss it |
| `proof_channel_cog` | Enforced media-only channel | no | S | Later |
| `karma_cog` | Peer recognition points | yes | M | Later |
| `ticket_cog` | Support tickets | yes | L | **Skip** — already excluded by the ledger |
| games / economy / xp | blackjack, btd6, mining, fishing, casino, farm… | yes | XL | **Skip** — already excluded by the ledger [D-0032] |

## 4. Recommended order

Ranked by value to a server that is about to receive strangers, cheapest first.

1. ~~**Role restore on rejoin**~~ — **shipped 2026-08-25.**
   `spiderbot/memory.py` (the Discord-as-store seam) + `cogs/membership.py`.
   Ordinary roles restored automatically, `Slingy Tester` raised to the owner
   (§1b), and `/tester add` now writes a verification record that outlives the
   audit log's ~45 days. Every later item reuses that seam.
   *Owner step:* create a private `#bot-state` channel the bot can read and
   write - until it exists the feature is silently off, by design.
2. **Server event logging** — joins, leaves, role changes, message deletes to a
   staff channel. Makes moderation reviewable rather than remembered.
3. **Moderation panel** — warn / timeout / kick / ban / unban, as buttons with
   the preview-then-confirm discipline the presets already use.
4. **Autorole** — a default role on join.
5. **Counter channel** — `Testers: N/12` in the channel list.
6. **Cleanup / purge.**

Items 1–3 share one storage module. Build it once, in item 1.

## 5. What this does not change

The plan is still ranked against 12 testers × 14 continuous days, and none of
the above moves that number — they make the server better for people who are
not there yet. Both independent design reviews named breadth-before-funnel as
the single biggest risk. That is not an argument against building these; it is
an argument for not letting them delay recruiting. Recorded so the trade is
explicit rather than accidental.
