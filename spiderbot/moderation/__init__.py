"""AI-assisted moderation: the AI supplies judgement, code supplies authority.

The owner's direction, 2026-09-04: Spider Bot should become *a reliable
automoderator with heavy AI integration*. **Reliable** is the acceptance bar and
it is what shapes this package. Heavy AI integration does not mean a language
model holding Discord permissions; it means a model's judgement entering a
pipeline that only deterministic code can complete:

    Discord event
      -> deterministic pre-check          (prechecks.py - cheap, no model call)
      -> optional AI analysis             (classifier.py - reads, never writes)
      -> TYPED, VALIDATED verdict         (contracts.py - or nothing at all)
      -> deterministic policy engine      (policy.py - data, not if-statements)
      -> permission / risk gate           (gate.py - refuses before Discord does)
      -> typed operation                  (operations.py - the only mutators)
      -> Discord API
      -> audit + case record              (cases.py)

`CLAUDE.md` invariant 5 said *"the AI never performs side effects"* and it still
holds, refined rather than deleted: no module in this package that talks to a
model can reach a module that mutates Discord. `classifier.py` imports
`contracts` and nothing else; `operations.py` imports `discord` and never
imports `classifier`. That is a structural guarantee, not a rule someone has to
remember - `tests/test_moderation_layering.py` fails the build if the import
graph ever closes that loop.

**Modes are a type split, not a flag.** In shadow mode the service holds a
`ShadowExecutor` which has no Discord handle at all, so there is no code path
from a shadow decision to a mutation to forget to guard. See `service.py`.
"""
