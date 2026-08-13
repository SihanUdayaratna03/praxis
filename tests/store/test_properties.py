"""The store's invariants, stated over generated inputs rather than examples.

The four files beside this one assert on a fixture: a graph somebody chose,
which is exactly as good as the choosing. What is here is the set of statements
that have to hold for *every* record the domain can produce -- round-trip
identity, span offsets that still address their document, edges that cannot
point at nothing, and a version history that no sequence of operations can
shorten.

Each example builds its own in-memory store. A file per example would put the
hypothesis budget into the filesystem, and reusing one store across examples
would make each example depend on the ids the last one happened to draw.

`max_examples` is bounded on purpose. These run in the pre-commit loop, and a
thirty-second hook is a hook that gets disabled.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from praxis.domain.base import Record
from praxis.domain.enums import RecordKind
from praxis.domain.links import LinkType
from praxis.domain.records import Estimate, Link, Span
from praxis.store.connection import MEMORY, connect
from praxis.store.errors import DanglingEdgeError
from praxis.store.migrations import current_version, latest_version, migrate
from praxis.store.repository import Repository

from tests import strategies
from tests.store.conftest import ACTOR, WRITTEN_AT

EXAMPLES = 50
"""Enough to explore the awkward end of the input space, few enough to stay in
the pre-commit loop."""

REASON = "written by a property test"

WITH_CONTENT = strategies.documents().filter(lambda document: document.content)
"""Documents a span can be cut from. An empty document has no byte range."""


@contextmanager
def a_store() -> Iterator[Repository]:
    """A fresh migrated store, thrown away when the example ends."""
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    try:
        yield repository
    finally:
        repository.close()


def write(store: Repository, records: tuple[Record, ...]) -> None:
    """Write records in dependency order."""
    for record in records:
        store.add(record, actor=ACTOR, reason=REASON, at=WRITTEN_AT)


def schema_of(store: Repository) -> list[str]:
    """Every object the store's schema declares, in a comparable order."""
    rows = store.connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


@st.composite
def document_and_span(draw: st.DrawFn) -> tuple[Record, Span]:
    """A document and a span cut from it."""
    document = draw(WITH_CONTENT)
    return document, draw(strategies.spans(document=document))


def _an_outcome(draw: st.DrawFn, span: Span) -> tuple[Record, ...]:
    """An outcome and the estimate it resolves."""
    estimate = draw(strategies.estimates(span_id=span.id))
    return (span, estimate, draw(strategies.outcomes(estimate_id=estimate.id)))


def _a_finding(draw: st.DrawFn, span: Span) -> tuple[Record, ...]:
    """A finding and the decision it is about."""
    decision = draw(strategies.decisions(span_id=span.id))
    return (span, decision, draw(strategies.findings(subject_id=decision.id)))


def _an_edge(draw: st.DrawFn, span: Span) -> tuple[Record, ...]:
    """An edge and the two nodes it joins."""
    decision = draw(strategies.decisions(span_id=span.id))
    assumption = draw(strategies.assumptions(span_id=span.id))
    edge = Link.between(
        LinkType.ASSUMES,
        decision.id,
        assumption.id,
        rationale=draw(strategies.PROSE),
        confidence=draw(strategies.CONFIDENCES),
        created_by=draw(strategies.ACTORS),
        created_at=draw(strategies.AWARE_DATETIMES),
    )
    return (span, decision, assumption, edge)


@st.composite
def an_estimate_and_its_references(draw: st.DrawFn) -> tuple[Record, ...]:
    """An estimate, with the document and span it cites."""
    document, span = draw(document_and_span())
    return (document, span, draw(strategies.estimates(span_id=span.id)))


@st.composite
def chains(draw: st.DrawFn) -> tuple[Record, ...]:
    """A record and everything it references, in the order they must be written.

    The last element is the record under test. Generating the references
    alongside it rather than planting a fixed document is what keeps the
    property about *any* record instead of any record that happens to cite the
    one span the test wrote first.
    """
    document, span = draw(document_and_span())
    tails = {
        RecordKind.DOCUMENT: lambda: (),
        RecordKind.SPAN: lambda: (span,),
        RecordKind.DECISION: lambda: (span, draw(strategies.decisions(span_id=span.id))),
        RecordKind.ASSUMPTION: lambda: (span, draw(strategies.assumptions(span_id=span.id))),
        RecordKind.ESTIMATE: lambda: (span, draw(strategies.estimates(span_id=span.id))),
        RecordKind.OUTCOME: lambda: _an_outcome(draw, span),
        RecordKind.FINDING: lambda: _a_finding(draw, span),
        RecordKind.LINK: lambda: _an_edge(draw, span),
    }
    # Only the drawn kind's builder runs, so a chain costs one record's worth of
    # generation rather than eight.
    kind = draw(st.sampled_from(sorted(tails)))
    return (document, *tails[kind]())


# --- round-tripping -------------------------------------------------------


@given(chains())
@settings(max_examples=EXAMPLES, deadline=None)
def test_any_record_comes_back_out_of_the_store_unchanged(chain):
    subject = chain[-1]

    with a_store() as store:
        write(store, chain)

        assert store.get(type(subject), subject.id) == subject


@given(document_and_span())
@settings(max_examples=EXAMPLES, deadline=None)
def test_a_span_still_addresses_its_document_after_a_round_trip(pair):
    document, span = pair

    with a_store() as store:
        write(store, (document, span))
        stored_document = store.require(type(document), document.id)
        stored_span = store.require(Span, span.id)

        # The hallucinated-citation gate rests on exactly this: the offsets are
        # inside the document, and the text at those offsets is the text the
        # span claims. A store that mangled either would make VerifierAgent
        # reject sound citations, or accept unsound ones.
        assert 0 <= stored_span.start_byte < stored_span.end_byte <= stored_document.byte_length
        assert (
            stored_document.content_bytes[stored_span.start_byte : stored_span.end_byte].decode()
            == stored_span.text
        )


@given(an_estimate_and_its_references())
@settings(max_examples=EXAMPLES, deadline=None)
def test_a_quantity_keeps_its_scale_as_well_as_its_value(chain):
    estimate = chain[-1]

    with a_store() as store:
        write(store, chain)

        stored = store.require(Estimate, estimate.id)

        # Equality would not catch this: Decimal('1.10') == Decimal('1.1'). The
        # scale is part of what the estimator said, so it is part of the record.
        assert str(stored.active_quantity) == str(estimate.active_quantity)
        assert str(stored.blocked_quantity) == str(estimate.blocked_quantity)


# --- referential integrity ------------------------------------------------


@given(
    strategies.sequential_ids(RecordKind.DECISION),
    strategies.sequential_ids(RecordKind.ASSUMPTION),
    strategies.PROSE,
    strategies.CONFIDENCES,
)
@settings(max_examples=EXAMPLES, deadline=None)
def test_an_edge_to_a_node_that_is_not_there_is_refused(
    source_id, target_id, rationale, confidence
):
    edge = Link.between(
        LinkType.ASSUMES,
        source_id,
        target_id,
        rationale=rationale,
        confidence=confidence,
        created_by=ACTOR,
        created_at=WRITTEN_AT,
    )

    with a_store() as store, pytest.raises(DanglingEdgeError):
        store.add(edge, actor=ACTOR, reason=REASON, at=WRITTEN_AT)


@given(chains())
@settings(max_examples=EXAMPLES, deadline=None)
def test_no_edge_in_the_store_points_at_a_missing_node(chain):
    with a_store() as store:
        write(store, chain)

        dangling = store.connection.execute(
            "SELECT l.id FROM link AS l "
            "LEFT JOIN node AS s ON s.id = l.source_id "
            "LEFT JOIN node AS t ON t.id = l.target_id "
            "WHERE s.id IS NULL OR t.id IS NULL"
        ).fetchall()

        # Asserted as well as enforced: a graph whose edges can point at nothing
        # makes every traversal a source of quietly short answers.
        assert dangling == []


# --- append-only ----------------------------------------------------------


@given(chains(), st.lists(st.sampled_from(["revise", "retract", "read"]), max_size=6))
@settings(max_examples=EXAMPLES, deadline=None)
def test_no_sequence_of_operations_shortens_a_history(chain, operations):
    subject = chain[-1]

    with a_store() as store:
        write(store, chain)
        history = {subject.version: subject}
        current = subject

        for operation in operations:
            before = store.current_version(current.id)
            if operation == "revise":
                current = store.revise(current, actor=ACTOR, reason=REASON, at=WRITTEN_AT)
            elif operation == "retract":
                current = store.retract(
                    type(current), current.id, actor=ACTOR, reason=REASON, at=WRITTEN_AT
                )
            else:
                current = store.require(type(current), current.id)
            history[current.version] = current
            assert store.current_version(current.id) >= before

        assert store.versions(subject.id) == tuple(range(1, len(history) + 1))
        # Every version that was ever current is still readable, and still says
        # what it said. This is invariant 7 as a statement about the whole
        # history rather than about one write.
        for version, record in history.items():
            assert store.require(type(record), record.id, version) == record
        assert len(store.audit_for(subject.id)) == len(history)


@given(chains(), st.integers(min_value=0, max_value=4))
@settings(max_examples=EXAMPLES, deadline=None)
def test_a_retraction_hides_a_record_without_removing_it(chain, revisions):
    subject = chain[-1]

    with a_store() as store:
        write(store, chain)
        current = subject
        for _ in range(revisions):
            current = store.revise(current, actor=ACTOR, reason=REASON, at=WRITTEN_AT)

        withdrawn = store.retract(
            type(current), current.id, actor=ACTOR, reason=REASON, at=WRITTEN_AT
        )

        assert withdrawn.retracted
        assert store.list_all(type(subject)) == ()
        assert store.require(type(subject), subject.id, 1) == subject


# --- migrations -----------------------------------------------------------


@given(st.integers(min_value=1, max_value=4))
@settings(max_examples=10, deadline=None)
def test_migrating_again_changes_nothing(times):
    with a_store() as store:
        after_first = schema_of(store)

        for _ in range(times):
            result = migrate(store.connection)

            # Not merely "no error". A migration that ran again and produced the
            # same schema by accident would still have run its DDL over live
            # rows, and this is the assertion that says it did not run at all.
            assert not result.changed

        assert current_version(store.connection) == latest_version()
        assert schema_of(store) == after_first


@given(chains(), st.integers(min_value=1, max_value=3))
@settings(max_examples=EXAMPLES, deadline=None)
def test_migrating_a_populated_store_leaves_its_records_alone(chain, times):
    subject = chain[-1]

    with a_store() as store:
        write(store, chain)

        for _ in range(times):
            migrate(store.connection)

        assert store.get(type(subject), subject.id) == subject
        assert current_version(store.connection) == latest_version()
