# spider-bot - agent boot file

Spider Bot: the AI community bot of the **Slingy Spider** Discord server
(guild `1541447750628147351`). Read `README.md` first, then the estate plan
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

## Verify

`python -m compileall spiderbot` must pass; then run locally with the token
from the owner's env (`DISCORD_BOT_TOKEN_SPIDERBOT`) and check the `ready`
audit line lists the five resolved channels. Deploy = push to main (Railway
auto-deploys the `spider-bot` service).

## Venue rules

This repo is estate work: clone fresh into `C:\dev\spider-bot`, work, push,
delete the clone ([D-0011] in the owner's hub). Reads need no clone (`gh`).
