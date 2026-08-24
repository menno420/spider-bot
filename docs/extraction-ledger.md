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

Explicitly NOT carried (per plan + [D-0032]): game/economy cogs, multi-guild
config, parity/golden apparatus, ticket system, prefix commands, Postgres
(deferred to Phase 0 proper - v1 audits to stdout + #mod-log instead).
