# Extraction ledger

Every reuse from the estate's source repos, per the GCB plan's rule
(fleet-manager `docs/planning/2026-08-21-game-community-bot/`). Source repos
are never modified.

| # | Source | What | Decision | Where it landed |
|---|--------|------|----------|-----------------|
| 1 | superbot-next `sb/kernel/ai/safety.py` (read via gh, 2026-08-24) | Kinded untrusted-data markers `<<<UNTRUSTED_DATA__{kind}__BEGIN/END>>>`, control-char strip, marker-forgery disarm (`<<<UNTRUSTED_DATA` -> `<<<<UNTRUSTED_DATA`, `UNTRUSTED_DATA__` -> `UNTRUSTED_DATA___`), display-name sanitization with reserved-name rejection | adapt (trimmed to the kinds v1 uses) | `spiderbot/ai/safety.py` |
| 2 | superbot-next `sb/kernel/ai/instructions.py` | Prompt layer order (safety -> policy/persona -> knowledge -> wrapped data -> wrapped user message); "labels are presentational tags, not roles"; do-not-claim-to-be-Claude persona rule | copy pattern, rewritten small | `spiderbot/ai/safety.py` (SYSTEM_SAFETY), `spiderbot/ai/gateway.py` (PERSONA, system block order) |
| 3 | superbot-next `sb/kernel/ai/providers/anthropic_provider.py` | AsyncAnthropic client, system as one cache-marked block, guarded degrade-not-raise error shape, 1024-token default cap | copy pattern, reimplemented (adaptive thinking + effort added per current API) | `spiderbot/ai/gateway.py` |
| 4 | superbot `disbot/core/runtime/ai/natural_language_stage.py` + `ai_natural_language_policy.py` | Decision pipeline order; mention check via `message.mentions` (BUG-0019); bare-mention strip regex; bystander-recorded-first memory ordering; cooldown marked only on delivery; deny-when-unconfigured; exactly-one-audit-event rule; `AllowedMentions.none()` on every send; 2000-char split posture | copy pattern, compressed to ~150 lines | `spiderbot/cogs/chat.py` |
| 5 | superbot-next `railway.json` / deployment shape | Railway worker service, ON_FAILURE restart x10, no exposed port | adapt (NIXPACKS instead of Dockerfile for v1) | `railway.json` |
| 6 | spider-swing `docs/product/play-store-listing.md` (owner-approved copy) | Game description for the bot's knowledge block | copy verbatim facts, condensed | `spiderbot/knowledge.py` |
| 7 | superbot `disbot/views/base.py` (read via gh, 2026-08-24) | BaseView/HubView interaction lifecycle: panel belongs to its invoker unless public, timeout disables the buttons instead of stripping the view, `message` bound so the timeout can edit, standard view-error handler with the `response_done` split (missing-defer vs real bug), and the rule that authority is re-checked at callback time rather than trusted from panel open | adapt (donor imports `views.navigation` inside `__init__` to dodge a circular import; here nav is data handed in, so `ui/` never imports `cogs/` - the donor's own `.claude/rules/discord-views.md` layering rule, enforced structurally) | `spiderbot/ui/base.py` |
| 8 | superbot `disbot/utils/hub_registry.py` + `subsystem_registry.py` | Central presentation registry: one frozen entry per surface (key, display name, emoji, purpose, audience floor), every front door reading that one source, and boot validation of Discord's component budgets | adapt (donor splits this across two registries and carries metadata in untyped dicts - `meta.get("display_name")` - which its own docstring calls painful; collapsed to one frozen dataclass so a typo fails at import instead of rendering a `None` button) | `spiderbot/ui/routes.py` |
| 9 | superbot `disbot/views/community/hub.py` | Hub panel shape: children discovered from the registry never hardcoded in the view, embed description generated from the discovered children so it cannot drift, one `build_*_panel` factory shared by every entry point, visibility filtered before render, five-buttons-per-row layout | copy pattern, compressed | `spiderbot/ui/home.py` |
| 10 | superbot `disbot/core/runtime/interaction_helpers.py` (read via gh, 2026-08-24) | `clamp_embed` (per-component caps, the 25-field cap, and the 6000-character *total* budget that rejects an embed whose parts are each legal), plus `safe_defer` / `safe_followup` / `safe_edit` - defer idempotently, route through followup once responded, clamp before every send, and report failure as a return value rather than raising | adapt (dropped the donor's `help_ctx_shim`, which has no analogue here since our panels take dependencies directly, and its `file`/`attachments` plumbing, which exists for the rendered image cards the plan rules out) | `spiderbot/ui/safe.py` |
| 11 | superbot `disbot/services/role_automation.py` + `utils/role_feasibility.py` (read via gh, 2026-08-25) | Role-write preflight: refuse `@everyone`, refuse integration-`managed` roles, and refuse anything at or above the bot's own top role, rather than letting Discord reject the call | copy the rule set, reimplemented small; the donor's threshold/XP progression is deliberately not carried | `spiderbot/cogs/membership.py` (`restorable_roles`) |
| 12 | superbot server/admin surface, surveyed at HEAD 2026-08-25 | Full map of which of its 24 cog packages are worth porting, what each needs, and what collides with our invariants - including the finding that role restoration on rejoin does **not** exist there | survey only, no code | `docs/superbot-reuse-map.md` |

Explicitly NOT carried (per plan + [D-0032]): game/economy cogs, multi-guild
config, parity/golden apparatus, ticket system, prefix commands, Postgres
(deferred to Phase 0 proper - v1 audits to stdout + #mod-log instead).

## AI-operations tranche, 2026-09-04

Rows 13 onward. The notable thing about this batch is how **few** rows it has:
the moderation, intake, storage and evidence subsystems are new work rather than
ports. The donors were consulted through the survey in
[`superbot-reuse-map.md`](superbot-reuse-map.md) — which is what that survey is
for — and the reuse that survived is the small, proven, structural kind.

| # | Source | What | Decision | Where it landed |
|---|--------|------|----------|-----------------|
| 13 | superbot-next `sb/kernel/ai/safety.py` (already row 1) | The kinded untrusted-data wrapper, applied to a SECOND consumer: the moderation classifier reads text written by the person it is judging, which is the sharpest injection surface in the bot | reuse as-is, no change | `spiderbot/moderation/classifier.py` (`build_payload`) |
| 14 | `spiderbot/memory.py` (this repo, 2026-08-25) | Discord-as-database: JSON records as messages in a private staff channel, human-readable without tooling, behind a narrow seam | **generalise** — collections, keyed lookup, an index built once per deploy instead of a scan per read, and multi-message records because a bug report exceeds Discord's 2000-character cap and `memory.py`'s clamp silently truncated one | `spiderbot/store.py` |
| 15 | `spiderbot/cogs/chat.py` cooldown/cap ladder (this repo) | The shape of a rate ladder: allow-list, then keyword, then cooldown, then hourly cap, each denial audited by name | copy the shape | `spiderbot/moderation/prechecks.py`, `spiderbot/cogs/intake.py` (offer cooldown) |
| 16 | superbot `moderation_cog` — surveyed, **not ported** | warn / timeout / kick / ban / unban as prefix commands over a service layer | **rebuild** rather than port: the donor's seven prefix commands do not survive this repo's button doctrine, and its service layer assumes a Postgres warnings table. What was taken is the *operation set* and the hierarchy preflight (row 11); everything else is new | `spiderbot/moderation/operations.py`, `gate.py` |
| 17 | superbot `automod_cog` — surveyed, **deliberately not built** | A message-filter engine | **skip, and say why in code**: Discord's own AutoMod does this at the gateway, and discord.py 2.7.1 exposes the whole API. The useful thing is to recommend rules the owner enables | `spiderbot/moderation/prechecks.py` (`AUTOMOD_RECOMMENDATIONS`) |
| 18 | spider-swing `game/domain/run_record.gd` + `run_record_ledger.gd` @ `fc64a3fb` | The run-evidence export contract: wrapper keys, 43 record fields, the ledger aggregates, `PIXELS_PER_METRE = 10.0` | **consume, never copy** — the schema is read from that repo's source and pinned here; spider-swing stays canonical | `spiderbot/evidence.py` |
| 19 | spider-swing `CONSTITUTION.md` § cross-repo feeds | The pinned-feed contract: producer stamps and enforces in CI, consumer pins and fails honestly | implement both halves | `spiderbot/support.py` (consumer) + spider-swing `tools/generate_support_feed.py` (producer) |
| 20 | spider-swing `tools/generate_audio_samples.py --check` idiom | Generator with a `--check` mode wired into `tools/verify.py`'s engine-independent section | copy the idiom exactly, so the second instance reads like the first | spider-swing `tools/generate_support_feed.py` |

**Not carried, and now with a reason each:** the donor's warnings table
(Postgres, which this bot still does not need), its ticket system, its XP and
threshold progression, its multi-guild config, and its parity/golden apparatus.
