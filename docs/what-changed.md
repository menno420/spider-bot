# What changed, in plain language

> For Menno. Written 2026-09-04, against the branch `claude/spider-bot-ai-ops-sthix0`.
> No jargon, no invariant numbers. The engineering version is `docs/architecture.md`
> and the invariants are in `CLAUDE.md`.
>
> **Nothing in here is switched on by merging it.** The moderation half ships
> `off`, and the GitHub half cannot post anything until you set a token. Both
> are one environment variable away from live, and both are described below so
> you can decide when.

## The one-sentence version

Spider Bot can now take what people say to it and turn it into something you
can find later — and it can watch a channel and form a judgement about a
message — but it cannot punish anyone or post anything public without a person
pressing a button.

## What people in the server can do now

**Talk to it and have it stick.** If someone writes *"the game froze when I
released the silk near 3 km"* in a watched channel, the bot offers — in the
channel, as a normal reply — to write it down. They press **Save it** and get a
reference like `SB-R-M1PB8V6G-KT0GBA`. That reference is real: they can ask for
their own reports later from `/home`.

**Report on purpose.** `/report` gives five routes: a bug, an idea, how the
game feels, *a problem with Spider Bot*, and *something private*. The private
one never goes anywhere public at all. A problem with the bot goes to the bot's
own tracker (`menno420/spider-bot`), never the game's — your call, 2026-09-04.

**See where it went.** `/home` → *My reports* lists what a member has filed and
what happened to each one.

**Nothing is written down without them saying yes.** The bot offers; it never
files on its own. If they say no, nothing is stored.

## What you get

**A queue instead of scrollback.** `/home` → *Reports* is every report, newest
first, with the ones waiting on you at the top.

**`/publish <id>` — and it shows you the issue first.** This is the important
one. When you publish a report, the bot renders the exact title and the exact
body that would go on the tracker — `menno420/spider-swing` for the game,
`menno420/spider-bot` for a problem with the bot — plus the labels, and asks you to
confirm. You read the real text before it exists anywhere public. Nothing
publishes without that press.

**A private return path.** The report record holds who reported it and where,
so you can answer them later — and none of that goes on GitHub. The public
issue never carries a Discord id, a username, or a channel link.

**Reports survive a restart.** They live in a private Discord channel, written
by the bot, and are read back on boot. If the bot cannot write one, it says so
to the reporter rather than pretending.

## Moderation — what it is, and what it deliberately is not

The shape, and every arrow is deterministic code except the one marked:

```
a message → is it worth judging at all? → [ the AI forms an opinion ]
          → is that opinion a valid, complete, evidence-backed verdict?
          → what does the policy table say that means?
          → is this member someone we may act on, and can we?
          → the action, and a case record
```

**The AI never acts.** It cannot delete, time out, kick, ban, or touch a role or
a channel. It returns a structured judgement or it returns nothing, and an
incomplete or malformed one means nothing happens. That is enforced by the shape
of the code — the half that talks to the model has no path to the half that
touches Discord — and a test fails the build if that ever stops being true.

**It ships off.** `MOD_MODE` defaults to `off`. The step after that is
`shadow`, which judges and records and changes nothing anyone can see. Only
after that is `enforce` worth discussing, and even then the autonomy ceiling
ships at *flag for review*, meaning the most it does on its own is put a case in
front of you.

**It never acts against staff.** Anyone holding a moderation permission is
neither judged nor actionable. Kick and ban are not reachable from the automatic
path at all — no combination of category, severity and confidence produces one.
They exist only through `/modact`, where a person is the actor of record, and
even there the bot lends nothing: you must hold the permission yourself.

**What it is for.** Conduct toward people — sustained hostility, threats,
harassment, hate, and fake "tester links", which in this server are the fastest
way to hurt someone. It is explicitly *not* for volume, mention floods, invite
spam or link filtering: Discord's own AutoMod does those natively and better,
and `docs/rollout.md` says which rules to turn on there instead.

**Frustration with the game is never a moderation matter.** "This game is
garbage" is feedback. So is blunt criticism of your design choices. So is
swearing. The model is told this explicitly, and — after this branch — it is
actually told it, which it was not before.

## What was wrong, and is now fixed

An independent review attacked the code from eight angles and found 41 things.
All 41 are fixed with a test that fails if the fix is removed. Four are worth
your time:

1. **The moderation instructions never reached the model.** All the rules that
   keep a frustrated tester from being timed out were written, tested around,
   and sent on no call ever made — a routing bug meant the classifier ran with
   the chat persona instead. Found before anything was ever enabled.
2. **The publication gate was blind.** Making publication need a person was the
   right fix for a keyword filter that could not read Dutch. But it approved by
   reference number and showed you a 60-character title, so it swapped a
   classifier posting text nobody read for a *person* posting text nobody read.
   That is why `/publish` now renders the body.
3. **A masked link.** `[official tester link](somewhere else)` passed straight
   through into a public issue and into the bot's own embeds. In this server
   that is the worst thing member text can render as.
4. **A report could silently vanish.** Two people filing at the same instant
   could leave one report durably stored but invisible to every screen — while
   its reporter had been told it was saved.

## What you have to do, once, and only when you want the feature

Nothing here is required to merge. Each line buys one capability.

| To turn on | Do this | Until you do |
|---|---|---|
| Durable reports | Create a **private** channel `#intake-state` the bot can read and write | Intake is off and every panel says so |
| Moderation cases | Create a **private** channel `#case-state`, same permissions | Moderation cannot record and stays off |
| Reports reaching GitHub | Set `GITHUB_TOKEN` in Railway to a **fine-grained** token scoped to `menno420/spider-swing` **and** `menno420/spider-bot`, with Issues: read & write and nothing else, then set `INTAKE_PUBLISH_ENABLED=true` | Reports are stored and queued; the bot says plainly that it could not file them |
| Moderation watching | Set `MOD_WATCH_CHANNELS` and `MOD_MODE=shadow` | Nothing is judged |
| Current game facts | Merge `menno420/spider-swing#181` and set `SUPPORT_FEED_URL` | The bot answers from a static block and says so |

Two of those are genuinely yours alone: the GitHub token (I will not create a
credential) and the decision to publish anything at all to a public tracker.

## What I would not do next

Not because it is hard — because it is the wrong order.

**Do not go to `enforce`.** Run `shadow` for a fortnight first and read
`/home` → *Cases*. Every case is markable as agree/disagree, and that tally is
the only honest basis for turning enforcement on. Turning it on because the code
works is the failure this whole design is arranged against.

**Do not point the bot at more channels than you read.** Every watched channel
is a model call per qualifying message.

## What I would build next

1. **The shadow-mode review loop, used.** The surface exists; it needs two weeks
   of real cases and your verdicts. Everything about enforcement depends on it.
2. **Report → fix → tell them.** The return path is stored and unused. When you
   close an issue the bot could tell the person who reported it, in the channel
   they reported from. That is the single thing that would make people report
   more.
3. **Deciding what happens to `#intake-state` when it fills.** It holds 2000
   messages of history on a cold read and logs an error the moment it hits that.
   At a handful of reports a week that is years away. It is written down so it
   is not a surprise.

Not on the list, deliberately: an economy, XP, games inside the bot, a web
dashboard, or anything that makes this a general platform. `docs/product-shape.md`
says why.
