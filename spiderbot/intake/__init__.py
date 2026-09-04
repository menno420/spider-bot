"""One intake implementation, many entry points.

The owner's direction, 2026-09-04: people should be able to talk naturally to
Spider Bot about *"guidance, complaints, bugs, feedback and improvement ideas"*,
and those reports should become *"durable, easy for the developer to find and
act on — preferably through GitHub"*.

Before this package there were two modals (`ui/forms.py`) that each posted
straight into a forum channel and kept nothing. A report existed only as a
Discord thread: not queryable, not countable, not linkable to a fix, and gone
if the channel was cleaned. This package is the durable middle.

    entry point            ->  IntakeService  ->  Store   (durable, private)
    (form / conversation)                     ->  GitHub  (projection, public)

**Store first, publish second, and never the other way round.** GitHub is a
*sink*, not the record. A confirmed report reaches durable storage with a stable
id before any network call leaves the process, so a GitHub outage costs a delay
and never a report. The status machine is:

    draft -> stored -> publish_pending -> published
                                       -> publish_failed (retryable, idempotent)

**Private by default at the boundary that matters.** *"The game is way too
hard"* is product feedback. *"This user keeps insulting me"* is an interpersonal
report and must never become a public GitHub issue. `privacy.py` decides, and
`github_sink.py` refuses anything not explicitly cleared — the check is on the
sink side, so a new entry point cannot forget it.
"""
