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
5. **The AI never performs side effects — REFINED 2026-09-04, not relaxed.**
   The AI may now supply a *judgement* that influences a moderation decision.
   It still calls nothing. The pipeline is:

       Discord event -> deterministic pre-check -> optional AI analysis
       -> TYPED, SCHEMA-VALIDATED verdict -> deterministic policy engine
       -> permission/risk gate -> typed operation -> Discord API
       -> audit + case record

   Free-form prose is **never** parsed into an action, and invalid or
   incomplete model output means **no automatic action**. This is structural,
   not remembered: `moderation/classifier.py` imports no module that mutates
   Discord, `moderation/operations.py` imports no classifier, and
   `tests/test_moderation_layering.py` fails the build if that ever changes.
   Role grants and announcements are unchanged — still deterministic,
   permission-gated commands only.
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

21. **Shadow mode is a TYPE, not a flag.** `ShadowExecutor` declares no
    `__init__`, holds no instance state and no Discord handle, so there is
    no code path from a shadow decision to a mutation to forget to guard. Never
    replace it with a boolean checked before each side effect: that is the
    design that fails when someone adds a seventh action and forgets the
    seventh check. `executor_for` returns shadow for anything that is not
    exactly `"enforce"`, so a misspelled mode does nothing rather than acting.

22. **No policy rule may produce a kick or a ban.** They are reachable only
    through the staff path (`/modact`), where a human is the actor of record.
    `policy.validate()` fails the table if a rule ever produces one without
    human confirmation. Do not "temporarily" add one to test something.

23. **Publication needs a NAMED HUMAN, never a classifier.**
    `Report.may_publish` requires `approved_by`, and only
    `IntakeService.approve` sets it. Do not "simplify" this back to trusting
    the privacy classifier: an adversarial review reproduced four ways past it,
    and the one that matters is that its vocabulary is English while this
    server's own language is Dutch. The classifier SORTS the queue; a person
    DECIDES what becomes public.

24. **A report is durable before anything is published.** GitHub is a sink, not
    the record: store first, then classify, then publish. A failed durable
    write is reported to the person as a failure — never thanked for.
    `Sensitivity.UNCLASSIFIED` is the initial value and is NOT publishable, so
    a report nothing classified cannot leak. The AI may only make a report
    **more** private, never less.

25. **The scanned set is the published set — one list.** `PUBLISHED_FIELDS`
    defines it, `Report.published_text()` is what the classifier reads, and a
    test asserts every name in it reaches `public_title`/`public_body`. Scan
    the text CLEANED the way it will be published: scanning the raw field and
    publishing the cleaned one let a zero-width space inside a trigger word
    blind the classifier while the reader saw the word intact.

26. **`spider-swing` owns the game; this bot consumes.** Game facts come from
    the versioned support feed with a pinned schema, a last-known-good fallback
    and an honest staleness line that is never omitted. Never hand-copy game
    prose into this repo again — that is what drifted.

27. **The tester role is never granted by code.** Not by the AI, not by a
    listener, not on rejoin. It mirrors who is actually opted in on Google
    Play, which only a human can confirm; code that grants it inflates the one
    number the project is ranked against. `cogs/membership.py` restores every
    other role automatically and deliberately raises this one to the owner.

28. **A caller that needs its own system prompt passes one, and an unknown
    `mode` is refused.** `Gateway.reply` dispatches on a table (`_INSTRUCTIONS`)
    rather than on `mode == "mention"` with everything else falling to the
    initiative branch. `MEASURED` 2026-09-04, and this is what the invariant is
    made of: `mode="moderation"` took the initiative branch from the day the
    classifier was written, so `classifier.SYSTEM` — every judgement rule that
    keeps a frustrated tester from being timed out — was never sent, and the
    final instruction told the classifier to answer `PASS` when unsure. The
    system prompt override never replaces `safety.SYSTEM_SAFETY`; the gateway
    appends it, so a caller cannot ship one without the injection rules.

29. **A rule that acts on person-directed conduct requires `targets_member`.**
    A field the schema asks for and nothing reads is worse than no field: it
    reads as a check. `MEASURED` 2026-09-04, a verdict saying *"general
    frustration, not aimed at anyone"* fired the timeout rule whose own note
    reads "aimed at someone". A rule that does not match falls through to
    `flag_for_review`, so the narrowing always adds a human rather than
    removing a protection.

30. **Who counts as staff is defined once,** in `gate.STAFF_PERMISSIONS`, and
    `prechecks` imports it. Two lists had drifted: a helper whose only elevated
    permission was `kick_members` or `manage_messages` was analysed AND actable
    while a `ban_members` helper was protected. When these disagree the
    disagreement is invisible — one decides whether a message is judged, the
    other whether the action lands, and a member in the gap gets both.

31. **Every button on a public panel re-resolves authority in its own
    callback.** A `DynamicItem` is rebuilt from its `custom_id` on every press
    by whoever pressed it, and the offer panels are posted in `#general`.
    `MEASURED` 2026-09-04: the Save button checked ownership and the "No
    thanks" button beside it did not, so any member could silence somebody
    else's crash report. Also consume what a button acts on — the Save button
    filed the same report twice on a double tap because the draft was read and
    never written back.

32. **Member text can never carry an id this system minted.**
    `redact.for_github` breaks `SB-…` ids with a zero-width space, and
    `find_issue_by_marker` believes a search hit only when the returned body
    actually contains the marker. The intake marker is the ONLY backstop
    against republication and it lives in a field a member types: `MEASURED`
    2026-09-04, writing report A's id into report B made A resolve to B's
    issue, so A never reached the tracker while every panel said "filed".

33. **A field the schema collects and nothing reads is a lie.**
    `targets_member` (invariant 29) and `PublishFailure.retryable` were both
    computed, stored, documented as protections, and consulted nowhere — the
    second one meaning a permanently-failed report was re-POSTed on every
    retry pass, for ever. Either read it or delete it.

34. **Authority is never handed back automatically.** A returning member gets
    every role restored except the tester role and any role carrying a
    moderation permission; the withheld ones are reported to the mod log rather
    than dropped silently. Same reasoning as the tester role: an account that
    left and came back is not proof the same person is on it. And the bot lends
    nothing on the staff path — `/modact`'s actor must hold the permission the
    operation needs and outrank the subject, or the gate refuses.

35. **Member text never renders as a MASKED link.** `[anchor](target)` is the
    one construct where the words a reader sees and the place they go are
    chosen separately, and this server hands out real install links.
    `redact.for_github` and `redact.for_discord` both break it. A BARE url is
    deliberately left alone — it tells the reader where it goes, and defanging
    it would make honest bug reports worse.

36. **A cross-repo feed is read BY KEY NAME, and its links are allow-listed.**
    Reading a dict by insertion order makes "emit your keys in this order" the
    contract, which JSON does not promise and no test pins. And the links block
    is written into the chat system prompt under *"Official links (never invent
    others)"* — so it is https-only and host-allow-listed
    (`support.LINK_HOSTS`), because anything the model is told is official
    should be checkable without trusting the transport or a future producer
    edit.

37. **An implausible number is clamped AND marked, aggregates included.** The
    per-record fields were capped and banner-marked while the lifetime ledger
    had no bounds at all. A value the file supplied that this bot cannot
    represent counts as clamped too: replacing 1e400 with a silent `0.0` shows
    "0 m" as though the game had measured it.

38. **A flag the bot acts on is type-checked, not coerced.** `bool("false")`
    is True, so a model emitting the string `"false"` turned `targets_member`
    ON — re-enabling the very acting rules invariant 29 added it to narrow —
    while `severity: "3"` beside it was correctly rejected. Every field that
    changes what the bot does gets the same strictness.

39. **The untrusted-data boundary is a token, not a constant.** Two literal
    string replacements are not a disarm: one zero-width character inside the
    marker defeated both, and the model received a forgery that rendered
    byte-identically to a real boundary. The strip now covers the invisibles,
    and the moderation span markers carry a per-call random token the member
    cannot guess. **Say what this is and is not:** it turns an
    indistinguishable forgery into a visible discrepancy. It is not a proof.
    The model-independent defences are the quote check, the policy thresholds
    and shadow mode — and the attack it answers produced EVASION (`category:
    none`, nothing done), not a false punishment.

40. **Member-controlled text goes inside the wrapper, never into the sentence
    introducing it.** A display name was the one member-controlled string in
    the chat prompt outside the untrusted markers, protected by a filter that
    missed the three Unicode line breaks. A filter is the belt; containment is
    the braces, and the braces go on.

41. **A brake arms BEFORE the thing it protects, never after it.** This
    codebase measured the same defect three times in one review: the intake
    offer cooldown armed after a successful reply, the initiative hourly cap
    armed after a successful delivery (500 model calls against a cap of 10, by
    one member deleting their own messages), and the mention path had no brake
    at all. Ask what a brake protects and arm it there — the API budget at the
    model call, the store at the write. The one deliberate exception is the
    initiative COOLDOWN, which protects the CHANNEL and so still arms on
    delivery; initiative answers PASS most of the time and consuming it on
    every decline would mean the bot never speaks.

42. **The cold store index is built once, under a lock, and a write that lands
    during the scan is replayed onto it.** Two members filing at the same
    moment started two scans; the first finished after the second write and
    replaced the index with a snapshot from before it. The record was durably
    in the channel and absent from every read path for the life of the process,
    while its reporter had been told "Saved. Your reference is …". Only the
    bot's own messages are read as records, and every Discord call in the store
    has a timeout — `append` already returns False and every caller reports
    that honestly, so a timeout has somewhere to go.

43. **A listener never touches `message.channel.name`.** Any member can archive
    a thread they created, after which discord.py hands the listener a
    `PartialMessageable` with no `.name` — so `getattr(..., "name", "") or ""`
    at the top, everywhere. That is invariant 2 ("no listener may raise") made
    reachable by member action.

44. **A persistent button defers before it does anything slow.** A
    `DynamicItem` exists to survive a deploy, and after a deploy the store
    index is cold — so the first press pays a 2000-message history scan while
    Discord kills the token at 3 seconds and the member sees "This interaction
    failed" on a button that is working. `safe_defer` first, then
    `safe_followup` / `safe_edit`.

45. **The approver reads the text, not the reference.** Invariant 23 says
    publication needs a named human. That is not worth much on its own: an
    adversarial review's sharpest point was that `/publish` took a report id
    and the staff queue showed a 60-character title, so requiring a person
    stopped a CLASSIFIER publishing unseen content and replaced it with a
    PERSON publishing unseen content — and every obfuscation the classifier
    missed sailed past the human too. `/publish` renders `public_title()` and
    `public_body()` verbatim behind a confirm, and re-resolves publishability
    at press time. A preview that paraphrases is the same failure as a
    title-only queue.

46. **Consent is stated before the member types, and the field defaults to
    FALSE.** `reporter_cleared` used to default True on the argument that
    submitting a form IS the agreement — while no form said so before
    submission and the receipt mentioned it only afterwards. A default that
    asserts consent nobody gave is worse than no field, because `may_publish`
    cites it by name. Every entry point that sets it True carries
    `forms.PUBLIC_NOTICE` where the member reads it first; the complaint form
    deliberately does not, because a complaint is never publishable and saying
    otherwise would be false.

47. **A mutating operation is recorded before it happens.** The executor used
    to run and the case write came after, so a full or unwritable case channel
    produced a member-visible timeout with no case behind it — invisible to the
    review queue and to any later question about why. An action nobody can
    review is worse than an action not taken. Shadow mode is exempt: it changes
    nothing a member sees, so a store outage must not turn it into refusals.

48. **The verify gate covers the invariants themselves, not just the code.**
    `policy.validate()` now refuses a mutating rule on person-directed
    categories that does not require targeting — invariant 29 as a check rather
    than as a habit, because the shipped table satisfying a rule says nothing
    about the next edit.

## Verify

`ruff check .` + `python -m pytest` + `python -m compileall spiderbot` must
all pass (CI job `quality` runs exactly these on every push; informational,
not a gate). Read the real exit code, never `$?` after a pipe. Live check: run locally with the token from the owner's env
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
