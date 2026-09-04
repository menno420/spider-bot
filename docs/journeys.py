"""Walk the real journeys end to end and print what a person would actually see.

    python3 docs/journeys.py

**Not a test, and deliberately not one.** The suite in `tests/` covers the
assertions; this covers the thing assertions are bad at — *reading wrong*. It
was written because a test count is not evidence that a journey works, and it
immediately earned its place: on its first run it exposed two user-visible
defects the whole green suite had not, an unbalanced bracket in the provenance
line that goes into the model's system prompt, and doubled bullets in a public
GitHub issue body. Both are now also covered by tests, which is the right order:
read the output, then pin what you found.

Run it after changing anything in `intake/`, `moderation/`, `evidence.py` or
`support.py`, and READ it rather than checking it exited 0 — it has no
assertions and always exits 0. What you are looking for is a line that a member,
a moderator or the developer would find confusing, wrong, or alarming.

Every journey below is one the brief for this work names by hand.
"""

# ruff: noqa: E402, E501, E701, E702, E741, B007
#
# This file bootstraps `sys.path` before importing `spiderbot`, so its imports
# cannot come first (E402) - that is what makes it runnable as a standalone
# script from a fresh checkout, which is the point of it. The remaining
# exemptions are for a dense read-once walkthrough: the fakes are deliberately
# compact so the JOURNEYS are what a reader's eye lands on, not the scaffolding.
# Nothing here ships in `spiderbot/`, which is held to the full rule set.
import asyncio
import json
import pathlib
import sys
import types

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from spiderbot import evidence, redact, store, support
from spiderbot.ai.gateway import AIResult
from spiderbot.intake import github_sink as gh
from spiderbot.intake import models as M
from spiderbot.intake import service as isvc
from spiderbot.moderation import contracts as C
from spiderbot.moderation import policy as P
from spiderbot.moderation import service as msvc
from spiderbot.moderation.cases import ReviewOutcome
from spiderbot.moderation.classifier import Classifier


def H(t): print("\n" + "="*78 + "\n  " + t + "\n" + "="*78)

class FakeGW:
    def __init__(self, text): self.text, self.enabled, self.system = text, True, None
    async def reply(self, payload, *, mode, system=None, model=None, timeout_s=45.0):
        self.system, self.mode, self.payload = system, mode, payload
        return AIResult(self.text, "ok", "test-model", 120, 30)

class FakeGH:
    def __init__(self): self.created = []
    available = True
    async def find_issue_by_marker(self, m):
        for i,(t,b,l) in enumerate(self.created):
            if m in b: return gh.Published(100+i, f"https://github.com/menno420/spider-swing/issues/{100+i}")
        return None
    async def create_issue(self, t, b, l):
        self.created.append((t,b,l)); n = 100+len(self.created)-1
        return gh.Published(n, f"https://github.com/menno420/spider-swing/issues/{n}")

class DeadGH(FakeGH):
    async def create_issue(self, t, b, l):
        return gh.PublishFailure("network", "GitHub is down", retryable=True)

async def main():
    # ---------------------------------------------------------------- INTAKE
    H("JOURNEY 1 — a tester reports a bug, GitHub works")
    st = store.InMemoryStore(); ghc = FakeGH(); svc = isvc.IntakeService(st, ghc)
    out = await svc.file(category=M.Category.BUG,
        title="Game freezes when I release the silk",
        description="It froze twice near 3 km, right after I let go at the top of a swing.",
        device="Pixel 7a, Android 15", repro_steps="Swing, reel in, release at the apex.",
        reporter=M.Reporter(user_id=555, display_name="rin", channel_id=9),
        # The bug form states, before the tester types, that a report may reach
        # the game's public tracker. `reporter_cleared` defaults False so an
        # entry point that has NOT said so cannot produce a publishable report.
        reporter_cleared=True)
    print("  member sees:", out.reporter_message)
    print("  classifier:  ", out.report.sensitivity, "-", out.report.sensitivity_reason[:60])
    print("  may publish? ", out.report.may_publish, "  <- nobody has cleared it yet")
    print("  owner queue: ", [r.id for r in await svc.awaiting_approval()])
    await svc.approve(out.report.id, by="menno")
    print("  menno runs /publish ...")
    pub = await svc.publish(out.report.id)
    print("  member then sees:", pub.reporter_message)
    print("\n  --- THE PUBLIC GITHUB ISSUE ---")
    title, body, labels = ghc.created[0]
    print("  title:", title); print("  labels:", labels)
    print("  " + "\n  ".join(body.splitlines()))
    print("\n  identity check — user id 555 in the body?", "555" in body, "| name 'rin'?", "rin" in body)

    H("JOURNEY 2 — GitHub is down, then comes back")
    st2 = store.InMemoryStore(); dead = DeadGH(); svc2 = isvc.IntakeService(st2, dead)
    o = await svc2.file(category=M.Category.BUG, title="Bird clips walls",
        description="The bird caught me through a wall in Storm Ridge.",
        reporter=M.Reporter(user_id=1), reporter_cleared=True)
    await svc2.approve(o.report.id, by="menno")
    print("  saved:", o.reporter_message)
    f = await svc2.publish(o.report.id)
    print("  publish fails:", f.reporter_message)
    print("  queue:", [r.id for r in await svc2.pending_publication()])
    live = FakeGH(); svc2._github = live
    r = await svc2.retry_pending()
    print("  after retry:", r[0].reporter_message)
    for _ in range(4): await svc2.publish(o.report.id)
    print("  issues created after 5 total publish attempts:", len(live.created), "<- must be 1")

    H("JOURNEY 3 — 'this user keeps insulting me'")
    st3 = store.InMemoryStore(); g3 = FakeGH(); svc3 = isvc.IntakeService(st3, g3)
    c = await svc3.file(category=M.Category.COMPLAINT, title="Harassment",
        description="This user keeps insulting me in general chat and won't stop.",
        reporter=M.Reporter(user_id=7))
    print("  member sees:", c.reporter_message)
    print("  sensitivity:", c.report.sensitivity, "| reason:", c.report.sensitivity_reason)
    p3 = await svc3.publish(c.report.id)
    print("  publish attempt ->", p3.failure, "| issues created:", len(g3.created), "<- must be 0")

    H("JOURNEY 4 — malicious text tries to reach GitHub")
    st4 = store.InMemoryStore(); g4 = FakeGH(); svc4 = isvc.IntakeService(st4, g4)
    o4 = await svc4.file(category=M.Category.BUG,
        title="@menno420 look at #1",
        description="ping @everyone see #1 and menno420/fleet-manager#2\n```\nhidden\n```",
        reporter=M.Reporter(user_id=2), reporter_cleared=True)
    await svc4.approve(o4.report.id, by="menno")
    await svc4.publish(o4.report.id)
    t4, b4, _ = g4.created[0]
    print("  issue title:", repr(t4))
    print("  live @mention in body? ", "@menno420" in b4 or "@everyone" in b4)
    print("  live #ref in body?    ", "#1 " in b4)
    print("  fence in body?        ", "```" in b4)

    H("JOURNEY 4b — the classifier misses, and it still cannot publish itself")
    st4b = store.InMemoryStore()
    g4b = FakeGH()
    svc4b = isvc.IntakeService(st4b, g4b)
    for label, desc in [
        ("plain English naming a member",
         "The panel freezes whenever Marcus Klein is in the lobby. "
         "Marcus calls me names every night."),
        ("Dutch - the server's own language",
         "Het spel loopt vast als Marcus Klein in de lobby zit. "
         "Marcus scheldt mij elke avond uit."),
    ]:
        o = await svc4b.file(category=M.Category.BUG, title="Freeze",
                             description=desc, reporter=M.Reporter(user_id=4), reporter_cleared=True)
        r = await svc4b.publish(o.report.id)
        print(f"  {label}")
        print(f"    classifier says: {o.report.sensitivity} (it cannot read this)")
        print(f"    published: {r.published}  reason: {r.failure}")
    print(f"  issues created: {len(g4b.created)} <- must be 0")
    print("  the classifier SORTS; a person DECIDES. That is the whole gate.")

    # -------------------------------------------------------------- EVIDENCE
    H("JOURNEY 5 — a tester attaches run evidence")
    rec = {"schema_version":2,"record_id":"r1","build_version":"0.45.0-run-feedback",
      "android_version_code":66,"difficulty_id":"standard","terminal_outcome":"death",
      "death_cause":"camera_boundary","final_region_id":"ancient_forest",
      "final_distance_pixels":51234.0,"travelled_distance_pixels":51234.0,
      "active_duration_seconds":88.5,"mean_forward_speed_pixels_per_second":580.0,
      "maximum_forward_speed_pixels_per_second":940.0,"above_reference_speed_share":0.42,
      "successful_web_attachments":61,"reel_activations":18,"burst_activations":3,
      "dive_activations":1,"flies_collected":12,"configuration_kind":"standard","input_source":"human"}
    export = json.dumps({"format":evidence.SUPPORTED_FORMAT,"local_only":True,"transmission":"none",
      "ledger":{"schema_version":2,"history_limit":100,"records":[rec],"feedback_responses":[],
        "total_completed_recorded_runs":14,"total_active_duration_seconds":1180.0,
        "total_distance_travelled_pixels":420000.0,
        "best_distance_pixels_by_difficulty":{"standard":51234.0}}})
    ev = evidence.parse(export)
    print("  'the game feels impossible around 5 km' becomes:")
    for line in ev.summary_lines(redact.for_discord): print("   ", line)
    st5 = store.InMemoryStore(); g5 = FakeGH(); svc5 = isvc.IntakeService(st5, g5)
    o5 = await svc5.file(category=M.Category.GAMEPLAY_FEEDBACK,
        title="Impossible around 5 km", description="I cannot get past about 5 km on standard.",
        evidence_summary=tuple(ev.summary_lines(redact.for_github)),
        evidence_format=evidence.SUPPORTED_FORMAT, reporter=M.Reporter(user_id=3), reporter_cleared=True)
    await svc5.approve(o5.report.id, by="menno")
    await svc5.publish(o5.report.id)
    print("\n  --- the issue's run-evidence section ---")
    b5 = g5.created[0][1]
    print("  " + "\n  ".join(b5.split("### Run evidence")[1].split("###")[0].strip().splitlines()))

    # ------------------------------------------------------------ MODERATION
    H("JOURNEY 6 — moderation, shadow mode")
    def message(content, author_name="member", author_id=5, mod=False):
        perms = types.SimpleNamespace(manage_guild=mod, administrator=False, moderate_members=mod)
        me = types.SimpleNamespace(id=999, bot=True, top_role=types.SimpleNamespace(position=50),
            guild_permissions=types.SimpleNamespace(manage_messages=True, moderate_members=True,
                send_messages=True, kick_members=True, ban_members=True,
                manage_guild=False, administrator=False))
        author = types.SimpleNamespace(id=author_id, display_name=author_name, bot=False,
            guild_permissions=perms, top_role=types.SimpleNamespace(position=1), roles=[])
        author.timeouts = []
        async def to(u, reason=None): author.timeouts.append(reason)
        author.timeout = to
        guild = types.SimpleNamespace(id=1, me=me, owner_id=1, roles=[], members=[])
        ch = types.SimpleNamespace(id=1, name="general")
        return types.SimpleNamespace(content=content, author=author, guild=guild, channel=ch, id=42)

    def verdict(**kw):
        base = dict(category="harassment", severity=3, confidence=0.95,
            reason="sustained hostility aimed at a member", evidence_quote="you are worthless",
            recommended_operation="timeout_short", human_review_required=False, targets_member=True)
        base.update(kw); return json.dumps(base)

    cases = [
      ("a real personal attack", "you are worthless and everyone here knows it", verdict()),
      ("criticism of the GAME", "this game is garbage, whoever designed the bird hates players",
       verdict(category="none", severity=0, confidence=0.9, evidence_quote="",
               recommended_operation="nothing", reason="frustration with the game, not a person")),
      ("QUOTING abuse they received", "he called me worthless and I want it on record",
       verdict(category="none", severity=0, confidence=0.85, evidence_quote="",
               recommended_operation="nothing", reason="the member is reporting abuse, not committing it")),
      ("model returns prose", "you are worthless and everyone knows it", "I think you should ban them."),
      ("model invents a quote", "the reel feels weak on this build",
       verdict(evidence_quote="I will find where you live")),
      ("hostile-sounding, but the model says it is aimed at NO ONE",
       "this whole level is garbage and whoever built it should be ashamed",
       verdict(category="targeted_hostility", severity=3, confidence=0.95,
               evidence_quote="whoever built it should be ashamed",
               targets_member=False, reason="blunt, but aimed at the design")),
    ]
    for label, content, model_out in cases:
        s = msvc.ModerationService(mode="shadow", classifier=Classifier(FakeGW(model_out)),
            policy=P.Policy(ceiling=C.Operation.TIMEOUT_LONG), backing=store.InMemoryStore(),
            enabled_channels=("general",))
        m = message(content)
        case = await s.handle_message(m, bot_user_id=999)
        print(f"\n  {label}")
        print(f"    -> operation={case.operation} performed={case.performed} status={case.status}")
        print(f"       rejection={case.verdict_rejection or '-'} | member touched: {bool(m.author.timeouts)}")
        print(f"       staff line: {case.summary_line()}")
        gw = s._classifier._gateway
        print(f"       asked as: mode={gw.mode} | judgement rules sent: "
              f"{'QUOTING or REPORTING abuse' in (gw.system or '')} | "
              f"author named in payload: {'written by the member' in gw.payload}")

    H("JOURNEY 7 — the same messages in ENFORCE mode")
    for label, content, model_out in cases[:2]:
        s = msvc.ModerationService(mode="enforce", classifier=Classifier(FakeGW(model_out)),
            policy=P.Policy(ceiling=C.Operation.TIMEOUT_LONG), backing=store.InMemoryStore(),
            enabled_channels=("general",))
        m = message(content)
        case = await s.handle_message(m, bot_user_id=999)
        print(f"  {label}: performed={case.performed} | member timed out: {bool(m.author.timeouts)}")

    H("JOURNEY 8 — the shipping default (ceiling flag_for_review)")
    s = msvc.ModerationService(mode="enforce", classifier=Classifier(FakeGW(verdict())),
        policy=P.Policy(), backing=store.InMemoryStore(), enabled_channels=("general",))
    m = message("you are worthless and everyone knows it")
    case = await s.handle_message(m, bot_user_id=999)
    print(f"  even in ENFORCE, at the shipping ceiling: performed={case.performed}")
    print(f"  member touched: {bool(m.author.timeouts)} | decision said: {case.decision['operation']}")
    print(f"  clamped from: {case.decision['clamped_from']}")

    H("JOURNEY 9 — a moderator reviews, and the tally is the evidence")
    s2 = msvc.ModerationService(mode="shadow", classifier=Classifier(FakeGW(verdict())),
        policy=P.Policy(ceiling=C.Operation.TIMEOUT_LONG), backing=store.InMemoryStore(),
        enabled_channels=("general",))
    ids_ = []
    for text in ("you are worthless", "this game is garbage"):
        c = await s2.handle_message(message(text), bot_user_id=999)
        if c: ids_.append(c.id)
    await s2.review(ids_[0], ReviewOutcome.CORRECT, by="menno")
    await s2.review(ids_[1], ReviewOutcome.TOO_STRICT, by="menno", note="just frustration with the game")
    from spiderbot.moderation.cases import review_tally
    print("  tally:", review_tally(await s2.cases()))
    print("\n  console:")
    for line in s2.describe(): print("   ", line)

    H("JOURNEY 10 — guidance from the live support feed")
    feed_path = _ROOT.parent / "spider-swing" / "support" / "spider-bot-support-feed.json"
    if not feed_path.is_file():
        print("   (no local spider-swing checkout; skipping the live-feed half)")
        return
    raw = feed_path.read_text()
    facts = support.parse(raw)
    print("  ", facts.staleness())
    print("   build the bot now knows:", facts.build_version, "| version code", facts.android_version_code)
    stale = support.SupportFacts(source=support.Source.BUILT_IN, problem="feed returned HTTP 404")
    print("   if the feed is unreachable:", stale.staleness())
    future = support.parse(json.dumps({**json.loads(raw), "schema_version": 9}))
    print("   if the feed moves ahead of the bot:", future.staleness())

asyncio.run(main())
