"""Deciding whether a report may leave the server. Deterministic first.

The owner named the hard case himself: *"complaint"* is ambiguous. *"The game
is way too hard"* is product feedback and belongs in a GitHub issue. *"This user
keeps insulting me"* is an interpersonal report and must never become one —
publishing it would put a member's conduct allegation on a public page anyone
can read, from a server anyone can join.

**The default is private, and it is the default in the strongest sense: it is
what happens when nothing decides.** `Sensitivity.UNCLASSIFIED` is the initial
value of the field and `Report.is_public_safe` is false for it, so a report that
this module never sees, or that it declines to classify, cannot be published.
Nothing has to remember to set it.

**Deterministic before AI, and deterministic can veto.** The signals that make
something interpersonal are cheap and reliable: a Discord mention token means a
specific person; second-person accusation ("he keeps", "they won't stop") means
a specific person; the words people use for harassment mean the report is about
conduct. A model is not needed to see any of that, and a model that disagreed
would be overruled — `classify` gives the model the ability to make a report
private and no ability to make one public that the deterministic pass did not
already clear.

That asymmetry is the whole design. The AI supplies judgement in the direction
where being wrong is cheap (over-classifying as private costs a GitHub issue
nobody filed); the deterministic pass holds the direction where being wrong is
expensive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from spiderbot.intake.models import CATEGORY_LABELS, Category, Report, Sensitivity, Target

#: A raw Discord mention token. Its presence means the reporter pointed at a
#: specific person or role, which is what makes a report interpersonal.
MENTION = re.compile(r"<@[!&]?\d+>")

#: Conduct vocabulary. Deliberately about how people treat each other rather
#: than about profanity: someone swearing about the bird is feedback, someone
#: being harassed is not. Matched on word boundaries so "harassment" in
#: "the difficulty is harassment" still trips it - a false private is cheap.
CONDUCT = re.compile(
    r"\b("
    r"harass(?:ing|ed|ment)?|bull(?:y|ied|ying)|insult(?:s|ed|ing)?|abus(?:e|ed|ive)"
    r"|threat(?:s|en|ened|ening)?|stalk(?:ing|ed)?|creep(?:y|ing)"
    r"|racist|racism|sexist|homophobic|transphobic|slur(?:s)?"
    r"|report(?:ing)? (?:a |this )?(?:user|member|person|guy|player)"
    r"|(?:this|that) (?:user|member|person|guy|player)"
    r"|dm(?:ed|ing)? me|dms? me|private message"
    r"|scam(?:mer|ming)?|doxx?(?:ed|ing)?|grooming|predator"
    r")\b",
    re.IGNORECASE,
)

#: Third-person accusation. "he keeps", "she won't stop", "they are being".
ACCUSATION = re.compile(
    r"\b(he|she|they|someone|somebody|this person)\s+"
    r"(keeps?|kept|wont|won't|will not|is|are|was|were|has|have|said|says|told|"
    r"sent|started|refuses?)\b",
    re.IGNORECASE,
)

#: Anything that looks like contact details or an account handle. Publishing
#: one is a privacy incident whatever the report was about.
CONTACT = re.compile(
    r"([\w.+-]+@[\w-]+\.[\w.]+)"          # email
    r"|(\+?\d[\d\s().-]{7,}\d)"           # phone-ish
    r"|(\b(?:discord|snap|insta|instagram|telegram|whatsapp)\s*[:@]\s*\S+)",
    re.IGNORECASE,
)

#: Categories that can be public at all, before any other test. A complaint is
#: absent on purpose - see `Report.is_public_safe`, which enforces it again on
#: the record itself so a new classifier cannot widen it.
PUBLISHABLE_CATEGORIES: frozenset[Category] = frozenset(
    {
        Category.BUG,
        Category.IDEA,
        Category.GAMEPLAY_FEEDBACK,
        Category.TESTING_PROBLEM,
        Category.BOT_PROBLEM,
    }
)


@dataclass(frozen=True)
class Classification:
    sensitivity: Sensitivity
    reason: str
    signals: tuple[str, ...] = ()

    @property
    def public(self) -> bool:
        return self.sensitivity is Sensitivity.PUBLIC_SAFE


def deterministic_signals(text: str) -> tuple[str, ...]:
    """Every reason this text looks interpersonal. Empty means none found."""
    found: list[str] = []
    if MENTION.search(text):
        found.append("mentions a specific member")
    if CONDUCT.search(text):
        found.append("uses conduct vocabulary")
    if ACCUSATION.search(text):
        found.append("describes what another person did")
    if CONTACT.search(text):
        found.append("contains contact details")
    return tuple(found)


def classify(report: Report, *, ai_says_private: bool | None = None) -> Classification:
    """Decide whether this report may be published.

    `ai_says_private` is the model's contribution and it is deliberately a
    one-way lever: `True` makes a report private, `None` and `False` change
    nothing. A model cannot clear a report the deterministic pass held back,
    and a model failure (which arrives as `None`) cannot loosen anything.
    """
    # The classifier reads EXACTLY the text that would be published, cleaned
    # the way it will be published (`Report.published_text`). Two holes closed:
    # a field printed into the body but absent from the scanned tuple, and a
    # zero-width space inside a trigger word blinding the scan while the
    # published text carried the word intact.
    text = report.published_text()

    if report.category is Category.COMPLAINT:
        return Classification(
            Sensitivity.PRIVATE,
            "a complaint stays private until a human decides otherwise: it is "
            "the category that can be either product feedback or a report about "
            "a person, and only one of those may be published",
            ("category is complaint",),
        )
    if report.category not in PUBLISHABLE_CATEGORIES:
        return Classification(
            Sensitivity.PRIVATE,
            f"{report.category} is not a category this bot publishes",
            (f"category is {report.category}",),
        )

    signals = deterministic_signals(text)
    if signals:
        return Classification(
            Sensitivity.PRIVATE,
            "this reads as being about a person rather than about the game: "
            + "; ".join(signals),
            signals,
        )
    if ai_says_private:
        return Classification(
            Sensitivity.PRIVATE,
            "the classifier judged this to be about a person rather than the game",
            ("ai judged interpersonal",),
        )
    subject = "the bot" if report.target is Target.BOT else "the game"
    return Classification(
        Sensitivity.PUBLIC_SAFE,
        f"{CATEGORY_LABELS[report.category].lower()} about {subject}, with no "
        "sign of anything about a specific person",
    )


# A note that belongs beside the code rather than in a document: this
# classifier is a SORTER, not a gate. `Report.may_publish` additionally
# requires a named human approver, because a keyword vocabulary in one language
# cannot be the last thing standing between a member's words and a public
# tracker — and this server's own language is not the one the vocabulary is
# written in. What `classify` buys is that the staff queue is already in the
# right order, and that the obvious cases are marked private before anyone
# looks. What it must never buy is publication.


def apply(report: Report, *, ai_says_private: bool | None = None) -> Report:
    """Classify and stamp. The only way a report becomes publishable."""
    decision = classify(report, ai_says_private=ai_says_private)
    return report.with_(
        sensitivity=decision.sensitivity, sensitivity_reason=decision.reason
    )
