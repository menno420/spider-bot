# spider-bot - agent boot file

Spider Bot: the AI community bot of the **Slingy Spider** Discord server
(guild `1541447750628147351`). Read `README.md` first, then `docs/product-shape.md` (what the bot is
for and how it should feel), then the estate plan
at fleet-manager `docs/planning/2026-08-21-game-community-bot/` before any
structural change - that plan outranks preferences you arrive with.

## Invariants (violating any of these is a defect, not a style choice)

1. **Never Administrator.** The bot's Discord permissions are a least-privilege
   management set. Do not request more in the invite URL or portal.
2. **The gateway never raises.** `spiderbot/ai/gateway.py` is the single fault
   boundary; every failure degrades to a reasoned `AIResult`. Keep it that way.
3. **All user-originated text is wrapped** via `spiderbot/ai/safety.py`
   (kinded `<<<UNTRUSTED_DATA__...__BEGIN/END>>>` markers, forgery-disarmed)
   before it reaches the model. New AI features wrap their inputs too.
4. **Unconfigured = silent.** Initiative replies happen only in channels named
   in `AI_INITIATIVE_CHANNELS`. No allow-list entry, no initiative - ever.
5. **The AI never performs side effects.** Role grants, announcements and
   moderation run only through deterministic, permission-gated slash commands.
6. **The bot never DMs members first** (server rule 4 binds the bot too).
7. **Every AI decision leaves exactly one audit event** (stdout JSON; replies,
   degrades and tester actions also go to #mod-log as embeds).
8. **Every send uses `AllowedMentions.none()`** except the deliberate
   welcome-ping and `/announce ping_testers`.
9. **Deterministic without AI**: with `ANTHROPIC_API_KEY` absent or
   `AI_ENABLED=false`, every command and listener still works.
10. **Secrets are env references only.** Never in code, config files, logs, or
    exception text. `.env.example` carries names with empty values.
11. **Mention detection uses `message.mentions`**, never `mentioned_in()`
    (which is true for @everyone - the estate's BUG-0019 false-ping class).
12. **Cog portability (OD-19)**: plain `commands.Cog` classes with
    `async def setup(bot)` - existing superbot cogs should port with only
    slight alteration.

13. **`ui/` never imports `cogs/`.** The layering is
    `cogs -> ui -> (presets, roster, cohort, config)`. Anything both
    layers need lives below them, never in a view (superbot's own view
    rule, adopted).
14. **No dead surfaces.** Every route in `ui/routes.py` has a `_do_<key>`
    handler on `HomePanel`, and a viewer is never shown a button whose
    audience floor they do not meet. Tests enforce both.
15. **Rendering is not authorisation.** Every panel callback re-resolves the
    presser's standing from live Discord state; a panel opened by a mod and
    pressed by a member must refuse.
16. **Nothing reaches the server unpreviewed.** Preset posting shows the exact
    text and destination, then requires a confirm press.

17. **Colour and emoji come from `spiderbot/style.py`.** No stock
    `discord.Color.*` anywhere, and nothing outside the eleven-emoji locked
    vocabulary on a public surface. Colour is semantic: orange means "needs
    your attention" and nothing else, and the AI never speaks without the
    purple accent and the speech balloon. `tests/test_style.py` enforces it.
18. **Back rebuilds, never replays.** A Back button reconstructs its parent
    from live Discord state at click time, so authority is re-resolved on the
    way back too. Restoring a captured snapshot would hand someone the panel
    they had a minute ago, which is invariant 15 through the back door.
19. **Ordinary logs go to stdout, problems to stderr.** `configure_logging`
    splits them, because the host tags every stderr line as an error -
    `basicConfig` would put routine boot chatter and a real crash in the same
    red bucket, which is the same as having no error signal. The JSON audit
    trail goes to stdout via `print`, where Railway parses it into structured
    fields (so its `message` looks empty and the payload is in `attributes`).
20. **The two deliberate pings are spelled out at the call site.**
    `AllowedMentions` leaves unset fields as a sentinel that reads as True;
    they resolve to False only via the client-wide default. The welcome ping
    and `ping_testers` state `everyone=False, roles=..., users=...,
    replied_user=False` explicitly rather than inheriting narrowness from
    another file.

21. **The tester role is never granted by code.** Not by the AI, not by a
    listener, not on rejoin. It mirrors who is actually opted in on Google
    Play, which only a human can confirm; code that grants it inflates the one
    number the project is ranked against. `cogs/membership.py` restores every
    other role automatically and deliberately raises this one to the owner.

## Verify

`ruff check .` + `python -m pytest` + `python -m compileall spiderbot` must
all pass (CI job `quality` runs exactly these on every push; informational,
not a gate). Live check: run locally with the token from the owner's env
(`DISCORD_BOT_TOKEN_SPIDERBOT`) and check the `ready` audit line lists the
five resolved channels - but never leave a local instance running while the
Railway worker is up: that is two live bots answering in the real server.
Deploy = push to main (Railway auto-deploys the `spider-bot` service; verify
the new deployment's `meta.commitHash` equals HEAD). **`railway.json` sets
watch patterns** (`spiderbot/**`, `requirements.txt`, `railway.json`,
`.python-version`), so a docs- or tests-only commit deliberately does *not*
deploy and the live `commitHash` will lag HEAD - that is correct, not a
failure. Without them a scheduled data commit once restarted a donor's
production worker ~293 times in one billing cycle. Force a deploy from the
Railway dashboard (service -> Deployments -> Redeploy) if you ever need one.

## Venue rules

This repo is estate work: clone fresh into `C:\dev\spider-bot`, work, push,
delete the clone ([D-0011] in the owner's hub). Reads need no clone (`gh`).
