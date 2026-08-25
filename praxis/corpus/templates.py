"""The four document shapes a generated corpus is written in.

An ADR, the meeting it came out of, the status update that closed it, and a
JSON export from an issue tracker. Each is a different extraction problem: the
ADR states its decision under a heading, the meeting buries it in a bullet, the
status update never uses the word "estimate", and the JSON one has no prose
structure at all -- only the adapter's rendering.

Every template plants the same four things where a grader can check them: a
decision, the assumptions under it, an estimate, and at least one **distractor**
-- a sentence that reads like a decision and is not. The distractors are the
part that makes precision measurable, so they are hypotheticals, deferrals and
other people's decisions rather than obvious noise.

Two assumptions come out of every ADR on purpose. One is about the world; the
other is about how long the work will take, which is a quantified
forward-looking claim -- an estimate wearing an assumption's clothes. That
second one carries the `estimated_as` edge, so the corpus contains the fusion
relationship the whole product is built to find, labelled, in advance of the
agent that has to find it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from random import Random
from typing import Final

from praxis.corpus.drafting import Draft
from praxis.corpus.groundtruth import (
    Comparison,
    ExpectedField,
    ExpectedLink,
    GroundTruthItem,
    ItemKind,
)
from praxis.corpus.topics import Topic
from praxis.domain.enums import SourceKind
from praxis.domain.links import LinkType
from praxis.ingest.adapters import json_leaves, render_leaf

_EPOCH: Final = date(2026, 1, 5)
"""The Monday every generated date is measured from.

A fixed date rather than today's, because a corpus regenerated tomorrow with
the same seed must be byte-identical to the one regenerated today -- otherwise
every metric recorded against it moves for a reason that is not a change.
"""

_PRESSURES: Final = (
    "the workaround we agreed last quarter has stopped holding",
    "two of the three on-call pages last month came from it",
    "the team has been asked for a date",
    "it is now the largest line on the infrastructure bill",
)
_CLOSERS: Final = (
    "No other business. Notes circulated the same afternoon.",
    "We ran out of time on the rest of the agenda.",
    "Next meeting is in a week, same room.",
)
_SIGN_OFFS: Final = (
    "Shout if any of this looks wrong.",
    "Full numbers are in the dashboard as usual.",
    "Same update next week.",
)
_ATTENDEES: Final = ("Nadeesha", "Priyanka", "Tharindu", "Ishara", "Ruwan", "Amali", "Dilanka")


@dataclass(frozen=True, slots=True)
class Written:
    """One finished document: what goes on disk, and what a span addresses.

    The two differ for a JSON source, which is the reason this carries both.
    `file_bytes` is the file; `content` is what the adapter produces from it,
    and every offset in `items` is an offset into `content`.
    """

    filename: str
    source_kind: SourceKind
    file_bytes: bytes
    content: str
    items: tuple[GroundTruthItem, ...]


def architecture_record(topic: Topic, draft: Draft, rng: Random, index: int) -> Written:
    """An ADR: the decision stated plainly, under headings, with its reasoning."""
    first, second = topic.rejected[0][0], topic.rejected[1][0]
    draft.block(f"# ADR: {topic.subject}")
    draft.block(f"Status: accepted. Decided {_day(index)} by the {topic.team} team.")
    draft.block("## Context")
    draft.block(f"{_sentence(topic.driver)}, and {rng.choice(_PRESSURES)}.")
    draft.block("## Decision")
    decision = draft.block(
        f"We are going with {topic.chosen}, rather than {first} or {second}. "
        f"Decided by {topic.owner} for the {topic.team} team."
    )
    draft.block("## Alternatives considered")
    for option, reason in topic.rejected:
        draft.block(f"- {option} — {reason}.")
    draft.block("## Assumptions")
    world = draft.block(
        f"- This rests on the assumption that {topic.assumption}. "
        f"In predicate form: `{topic.predicate}`. Re-check {topic.expiry}."
    )
    effort = draft.block(
        f"- It also rests on the work finishing inside {topic.estimate_weeks} weeks. "
        f"In predicate form: `{_effort_predicate(topic)}`. "
        f'Re-check on_event("the work ships").'
    )
    draft.block("## Effort")
    estimate = draft.block(
        f"{topic.owner} put this at {topic.estimate_weeks} weeks of hands-on work, "
        f"blocked time aside."
    )
    draft.block("## Not decided here")
    hypothetical = draft.block(topic.hypothetical)

    estimate_id = draft.record(ItemKind.ESTIMATE, estimate, fields=_estimate_fields(topic))
    effort_id = draft.record(
        ItemKind.ASSUMPTION,
        effort,
        fields=_assumption_fields(
            f"the work finishing inside {topic.estimate_weeks} weeks",
            _effort_predicate(topic),
            'on_event("the work ships")',
        ),
        links=(ExpectedLink(link_type=LinkType.ESTIMATED_AS, target_item_id=estimate_id),),
        note="A quantified forward-looking claim: an estimate wearing an assumption's clothes.",
    )
    world_id = draft.remember(
        world_assumption_key(topic),
        draft.record(
            ItemKind.ASSUMPTION,
            world,
            fields=_assumption_fields(topic.assumption, topic.predicate, topic.expiry),
        ),
    )
    draft.record(
        ItemKind.DECISION,
        decision,
        fields=_decision_fields(topic),
        links=(
            ExpectedLink(link_type=LinkType.ASSUMES, target_item_id=world_id),
            ExpectedLink(link_type=LinkType.ASSUMES, target_item_id=effort_id),
        ),
    )
    draft.distractor(
        ItemKind.DECISION,
        hypothetical,
        note="A hypothetical under a heading that says it was not decided.",
    )
    return _as_text(draft, f"{index:02d}-{topic.slug}-adr.md", SourceKind.MARKDOWN)


def meeting_notes(topic: Topic, draft: Draft, rng: Random, index: int) -> Written:
    """Notes: the same decision, buried in a bullet, next to an open question."""
    draft.block(f"# {topic.team} weekly — {_day(index)}")
    draft.block(f"Present: {', '.join(_attendees(rng))}.")
    draft.block("## Decisions")
    decision = draft.block(
        f"- We settled on {topic.chosen} for {topic.subject}, over "
        f"{topic.rejected[0][0]}. {topic.owner} owns it."
    )
    draft.block("## Assumptions we are carrying")
    assumption = draft.block(
        f"- We are assuming {topic.assumption} — predicate `{topic.predicate}`. "
        f"Worth another look {topic.expiry}."
    )
    draft.block("## Open questions")
    hypothetical = draft.block(f"- {topic.hypothetical}")
    draft.block("## Anything else")
    draft.block(rng.choice(_CLOSERS))

    assumption_id = draft.remember(
        world_assumption_key(topic),
        draft.record(
            ItemKind.ASSUMPTION,
            assumption,
            fields=_assumption_fields(topic.assumption, topic.predicate, topic.expiry),
        ),
    )
    draft.record(
        ItemKind.DECISION,
        decision,
        fields=_decision_fields(topic, rejected=(topic.rejected[0][0],)),
        links=(ExpectedLink(link_type=LinkType.ASSUMES, target_item_id=assumption_id),),
        note="Stated in a bullet rather than under a Decision heading.",
    )
    draft.distractor(
        ItemKind.DECISION,
        hypothetical,
        note="An open question phrased as if it were settled.",
    )
    return _as_text(draft, f"{index:02d}-{topic.slug}-notes.md", SourceKind.MARKDOWN)


def status_update(topic: Topic, draft: Draft, rng: Random, index: int) -> Written:
    """Plain text: an estimate and its outcome, neither called by that name."""
    draft.block(f"{topic.team} status — week {index + 1}")
    draft.block("What shipped")
    draft.block(f"{_sentence(topic.subject)} is live in production.")
    draft.block("Estimates and actuals")
    estimate = draft.block(
        f"{topic.owner} put {topic.subject} at {topic.estimate_weeks} weeks of hands-on "
        f"work, and that is the number we planned against."
    )
    outcome = draft.block(f"It actually took {topic.actual_weeks} weeks of hands-on work.")
    draft.block("Next up")
    hypothetical = draft.block(topic.hypothetical)
    draft.block(rng.choice(_SIGN_OFFS))

    estimate_id = draft.record(ItemKind.ESTIMATE, estimate, fields=_estimate_fields(topic))
    draft.record(
        ItemKind.OUTCOME,
        outcome,
        fields=_outcome_fields(topic),
        resolves_item_id=estimate_id,
        note="The actual, in a sentence that never uses the word outcome.",
    )
    draft.distractor(
        ItemKind.DECISION,
        hypothetical,
        note="Something that might happen, under a heading about what is next.",
    )
    return _as_text(draft, f"{index:02d}-{topic.slug}-status.txt", SourceKind.TEXT)


def issue_export(topic: Topic, draft: Draft, rng: Random, index: int) -> Written:
    """A JSON export: no prose structure at all, only the adapter's rendering."""
    payload = {
        "title": f"{_sentence(topic.subject)} rollout",
        "team": topic.team,
        "status": rng.choice(("closed", "done", "shipped")),
        "decision": {
            "summary": (
                f"Chose {topic.chosen} over {topic.rejected[0][0]} and "
                f"{topic.rejected[1][0]}, decided by {topic.owner}."
            ),
            "chosen": topic.chosen,
            "rejected": [option for option, _ in topic.rejected],
        },
        "estimate": {
            "summary": (
                f"{topic.owner} estimated {topic.estimate_weeks} weeks of hands-on work "
                f"for {topic.work_class}."
            ),
            "weeks": topic.estimate_weeks,
        },
        "risks": [topic.hypothetical],
    }
    # Blocks are built from the adapter's own rendering, so an offset recorded
    # here is an offset into exactly what ingestion will produce.
    placed = {path: draft.block(render_leaf(path, value)) for path, value in json_leaves(payload)}

    draft.record(ItemKind.ESTIMATE, placed["estimate.summary"], fields=_estimate_fields(topic))
    draft.record(ItemKind.DECISION, placed["decision.summary"], fields=_decision_fields(topic))
    draft.distractor(
        ItemKind.DECISION,
        placed["risks[0]"],
        note="A risk, which reads like a decision once the key path is stripped off.",
    )
    return Written(
        filename=f"{index:02d}-{topic.slug}-export.json",
        source_kind=SourceKind.JSON,
        file_bytes=json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8") + b"\n",
        content=draft.text(),
        items=tuple(draft.items),
    )


def world_assumption_key(topic: Topic) -> str:
    """Where a topic's world assumption is remembered, for a later document.

    One key per topic rather than per document: an ADR and the meeting it came
    out of state the same assumption, and a revision note overturning it should
    join to whichever was written first rather than to both. Writing to two
    would put two `contradicts` edges in the key for one disagreement, and a
    detector finding one of them would score as half right.
    """
    return f"{topic.slug}:world-assumption"


def revision_note(topic: Topic, draft: Draft, rng: Random, index: int) -> Written:
    """A note, months later, saying the assumption turned out to be wrong.

    Deliberately **not** in `TEMPLATES`. The four templates are shapes a corpus
    is written in and the scheduler picks between them; this one is a *reply* to
    a document that already exists, so it is written in a second pass over the
    topics that have already been stated. Putting it in the rotation would let a
    revision be generated before the assumption it revises, and the edge would
    have nothing to point at.

    The assumption it asserts is the original's reversal, written so the two
    predicates permit no common value -- which is what makes the planted
    contradiction settleable by arithmetic and gradeable without a model.
    """
    overturned = draft.recall(world_assumption_key(topic))
    draft.block(f"# Revision: {topic.subject}")
    draft.block(f"Circulated {_day(index)} to the {topic.team} team.")
    draft.block("## What changed")
    draft.block(
        f"We wrote down that {topic.assumption}. That is no longer true, and it "
        f"has not been true for a while."
    )
    draft.block("## The corrected assumption")
    reversal = draft.block(
        f"- We now have to assume {topic.reversal}. "
        f"In predicate form: `{topic.reversal_predicate}`. Re-check {topic.expiry}."
    )
    draft.block("## What we are not changing")
    hypothetical = draft.block(
        f"This does not by itself reverse the decision to use {topic.chosen}. {topic.hypothetical}"
    )
    draft.block(rng.choice(_SIGN_OFFS))

    draft.record(
        ItemKind.ASSUMPTION,
        reversal,
        fields=_assumption_fields(topic.reversal, topic.reversal_predicate, topic.expiry),
        links=(
            (ExpectedLink(link_type=LinkType.CONTRADICTS, target_item_id=overturned),)
            if overturned is not None
            else ()
        ),
        note=(
            "Overturns an assumption stated in an earlier document. The two "
            "predicates permit no common value, so this pair is provable rather "
            "than a judgement."
        ),
    )
    draft.distractor(
        ItemKind.DECISION,
        hypothetical,
        note="A sentence saying what is NOT being decided, under a heading that says so.",
    )
    return _as_text(draft, f"{index:02d}-{topic.slug}-revision.md", SourceKind.MARKDOWN)


TEMPLATES: Final = (architecture_record, meeting_notes, status_update, issue_export)


def _as_text(draft: Draft, filename: str, source_kind: SourceKind) -> Written:
    """Finish a document whose file is its own content."""
    content = draft.text()
    return Written(
        filename=filename,
        source_kind=source_kind,
        file_bytes=content.encode("utf-8"),
        content=content,
        items=tuple(draft.items),
    )


def _decision_fields(
    topic: Topic, rejected: tuple[str, ...] | None = None
) -> tuple[ExpectedField, ...]:
    """What a `Decision` extracted from this topic should say."""
    options = rejected if rejected is not None else tuple(option for option, _ in topic.rejected)
    return (
        ExpectedField(name="chosen", value=topic.chosen, comparison=Comparison.CONTAINS),
        ExpectedField(name="rejected", value="|".join(options), comparison=Comparison.SET),
        ExpectedField(name="decision_maker", value=topic.owner),
    )


def _assumption_fields(statement: str, predicate: str, expiry: str) -> tuple[ExpectedField, ...]:
    """What an `Assumption` extracted from this block should say."""
    return (
        ExpectedField(name="statement", value=statement, comparison=Comparison.CONTAINS),
        ExpectedField(name="predicate", value=predicate),
        ExpectedField(name="expiry_condition", value=expiry),
    )


def _estimate_fields(topic: Topic) -> tuple[ExpectedField, ...]:
    """What an `Estimate` extracted from this topic should say."""
    return (
        ExpectedField(
            name="active_quantity",
            value=str(topic.estimate_weeks),
            comparison=Comparison.NUMERIC,
            tolerance=Decimal(0),
        ),
        ExpectedField(name="unit", value="weeks"),
        ExpectedField(name="owner", value=topic.owner),
        ExpectedField(name="work_class", value=topic.work_class),
    )


def _outcome_fields(topic: Topic) -> tuple[ExpectedField, ...]:
    """What an `Outcome` extracted from this topic should say."""
    return (
        ExpectedField(
            name="active_quantity",
            value=str(topic.actual_weeks),
            comparison=Comparison.NUMERIC,
            tolerance=Decimal(0),
        ),
        ExpectedField(name="unit", value="weeks"),
    )


def _effort_predicate(topic: Topic) -> str:
    """The effort assumption as an expression, in the identifier style ADRs use."""
    return f"{topic.slug.replace('-', '_')}_weeks <= {topic.estimate_weeks}"


def _attendees(rng: Random) -> list[str]:
    """Four names, in a stable order for a given draw."""
    return sorted(rng.sample(_ATTENDEES, 4))


def _day(index: int) -> str:
    """A plausible date, a week apart per document, never today's."""
    return (_EPOCH + timedelta(weeks=index)).isoformat()


def _sentence(text: str) -> str:
    """Capitalise a phrase so it can open a sentence."""
    return text[:1].upper() + text[1:]
