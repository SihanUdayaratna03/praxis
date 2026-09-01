"""Documents that exist to be got wrong: the corpus's controls.

The four templates in `templates` all plant real answers. These plant none, or
plant only labelled negatives, so a false positive has somewhere to show up.
Kept out of `TEMPLATES` because the scheduler rotates that tuple over topics and
these are written in passes of their own.
"""

from __future__ import annotations

from datetime import date, timedelta
from random import Random
from typing import Final

from praxis.corpus.drafting import Draft
from praxis.corpus.groundtruth import Comparison, ExpectedField, ItemKind
from praxis.corpus.templates import Written, as_text
from praxis.corpus.topics import Topic
from praxis.domain.enums import SourceKind

_EPOCH: Final = date(2026, 1, 5)

_SERVICES: Final = ("api", "worker", "gateway", "scheduler")

_QUIET_CLOSERS: Final = (
    "Nothing needs a follow-up.",
    "No action for the next shift.",
    "Handing over clean.",
)


def clean_control(topic: Topic, draft: Draft, rng: Random, index: int) -> Written:
    """An operational document with nothing in it to extract.

    Records no ground-truth items at all, so anything an agent pulls out of one
    of these is a false positive by construction. That is the only way a
    document-level hallucination rate becomes measurable.
    """
    if index % 2 == 0:
        _handover(topic, draft, rng, index)
    else:
        _deploy_log(topic, draft, rng, index)
    return as_text(draft, f"{index:02d}-{topic.slug}-operations.md", SourceKind.MARKDOWN)


def _handover(topic: Topic, draft: Draft, rng: Random, index: int) -> None:
    """An on-call shift handover. Facts about what happened, no choices."""
    draft.block(f"# {topic.team} on-call handover")
    draft.block(f"Shift ending {_day(index)}. Pager was quiet for most of it.")
    draft.block("## Pages")
    draft.block("- None that reached a person.")
    draft.block("## Alerts that cleared on their own")
    draft.block(
        f"- {topic.team.lower()}-{rng.choice(_SERVICES)} latency above the warning line "
        f"for four minutes, back under it before anyone looked."
    )
    draft.block("## Runbook")
    draft.block("Unchanged since the last handover.")
    draft.block(rng.choice(_QUIET_CLOSERS))


def _deploy_log(topic: Topic, draft: Draft, rng: Random, index: int) -> None:
    """A deployment log. Timestamps and version numbers, nothing argued."""
    service = f"{topic.team.lower().replace(' ', '-')}-{rng.choice(_SERVICES)}"
    draft.block(f"# Deploys — {_day(index)}")
    draft.block(f"Rollouts recorded automatically for {topic.team}. No manual edits.")
    draft.block(f"- 09:14 {service} 4.2.1 to all regions, no errors reported.")
    draft.block(f"- 11:02 {service} 4.2.2, a log level change and nothing else.")
    draft.block(f"- 16:40 {service} 4.2.3, dependency bump from the weekly job.")
    draft.block("Rollback count for the day: zero.")


def adversarial_memo(topic: Topic, draft: Draft, rng: Random, index: int) -> Written:
    """A memo in which every extractable-looking sentence is a labelled negative.

    Five different ways a sentence can read like a decision and not be one. A
    document with a real decision in it lets an over-eager extractor be right by
    accident; this one does not.
    """
    first, second = topic.rejected[0][0], topic.rejected[1][0]
    draft.block(f"# Open questions — {topic.subject}")
    draft.block(f"Notes from {topic.team}, {_day(index)}. Nothing here is settled.")

    draft.block("## Raised and not decided")
    hypothetical = draft.block(topic.hypothetical)
    draft.block("## Deferred")
    deferral = draft.block(
        f"We will look at {first} again next quarter. Nobody has agreed to that yet."
    )
    draft.block("## Somebody else's decision")
    elsewhere = draft.block(
        f"A neighbouring team went with {second} for their own service. "
        f"That is their call and it does not bind us."
    )
    draft.block("## Rejected outright")
    rejected = draft.block(
        f"The proposal to standardise on {second} across every team was turned down."
    )
    draft.block("## Still an open question")
    question = draft.block(f"Should {topic.subject} be owned by {topic.team} at all?")
    draft.block(rng.choice(_QUIET_CLOSERS))

    draft.distractor(
        ItemKind.DECISION, hypothetical, note="A conditional, under a heading saying so."
    )
    draft.distractor(ItemKind.DECISION, deferral, note="A deferral. Nobody agreed to anything.")
    draft.distractor(ItemKind.DECISION, elsewhere, note="Another team's decision, not this one's.")
    draft.distractor(ItemKind.DECISION, rejected, note="A proposal that was refused.")
    draft.distractor(ItemKind.DECISION, question, note="A question, phrased like a proposal.")
    return as_text(draft, f"{index:02d}-{topic.slug}-open-questions.md", SourceKind.MARKDOWN)


def orphan_note(topic: Topic, draft: Draft, rng: Random, index: int) -> Written:
    """A note stating a belief that no decision anywhere rests on.

    The assumption is real and should be extracted; what it lacks is an
    `assumes` edge from any decision. That is what `CuratorAgent` retires, and
    until now the corpus planted none of them.
    """
    predicate = orphan_predicate(topic)
    draft.block(f"# Working note — {topic.subject}")
    draft.block(f"{topic.owner}, {_day(index)}. Filed for the record, not for a decision.")
    draft.block("## What we are assuming")
    assumption = draft.block(
        f"- We are assuming {orphan_statement(topic)}. "
        f"In predicate form: `{predicate}`. Re-check {topic.expiry}."
    )
    draft.block("## Why this is written down")
    draft.block(
        "Nothing has been decided on the strength of it. It is here so that if it "
        "stops being true somebody notices."
    )
    draft.block(rng.choice(_QUIET_CLOSERS))

    draft.record(
        ItemKind.ASSUMPTION,
        assumption,
        fields=(
            ExpectedField(
                name="statement", value=orphan_statement(topic), comparison=Comparison.CONTAINS
            ),
            ExpectedField(name="predicate", value=predicate),
            ExpectedField(name="expiry_condition", value=topic.expiry),
        ),
        note="An assumption no decision rests on -- a retirement candidate.",
    )
    return as_text(draft, f"{index:02d}-{topic.slug}-working-note.md", SourceKind.MARKDOWN)


def orphan_statement(topic: Topic) -> str:
    """What an orphan note asserts, in words."""
    return f"{topic.team} has no more than five open questions about {topic.subject}"


def orphan_predicate(topic: Topic) -> str:
    """The same claim as an expression, on a quantity no other document uses.

    A fresh quantity on purpose: sharing one with a topic's own assumption would
    make the two share a measurement, and the orphan's verdict would then be a
    fact about the other document.
    """
    return f"{topic.slug.replace('-', '_')}_open_questions <= 5"


def _day(index: int) -> str:
    """A date that moves with the document's position, as the templates do."""
    return (_EPOCH + timedelta(days=index * 3)).isoformat()
