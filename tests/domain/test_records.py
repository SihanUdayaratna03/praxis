"""The record models: what they accept, and more importantly what they refuse.

Each rejection test corresponds to a state that would be indistinguishable from
a valid one downstream. A breached assumption with no evaluation time, an
outcome that is unresolved and also carries a number, a verdict with no
challenge behind it -- all three would read as ordinary data to an agent, and
all three are lies.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import given
from praxis.domain.enums import (
    AssumptionStatus,
    MatchQuality,
    RecordKind,
    Severity,
    Unit,
    Verdict,
)
from praxis.domain.links import LinkType
from praxis.domain.records import (
    RECORD_TYPES,
    Assumption,
    AuditEvent,
    Decision,
    Document,
    Estimate,
    Finding,
    Link,
    Outcome,
    Span,
)
from pydantic import ValidationError

from tests import strategies as s

NOW = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)


def make_document(content: str = "hello wörld, again") -> Document:
    return Document(
        id="DOC-0001",
        source_uri="docs/adr/0003-sqlite-as-the-graph-store.md",
        source_kind="markdown",
        content=content,
        ingested_at=NOW,
        created_at=NOW,
        created_by="SourceAdapter",
    )


def make_span(document: Document | None = None, start: int = 0, end: int = 5) -> Span:
    doc = document if document is not None else make_document()
    return Span.covering(doc, start, end, created_by="SegmenterAgent", created_at=NOW)


# --------------------------------------------------------------------------
# Cross-cutting guarantees
# --------------------------------------------------------------------------


def test_every_kind_has_a_record_class():
    assert set(RECORD_TYPES) == set(RecordKind)
    for kind, cls in RECORD_TYPES.items():
        assert cls.record_kind is kind


def test_records_are_frozen():
    span = make_span()
    with pytest.raises(ValidationError):
        span.text = "something else"


def test_unknown_fields_are_rejected():
    # A misspelled field silently discarded is how a confidence of 0.5 becomes
    # a confidence of nothing, three phases after anyone could notice.
    with pytest.raises(ValidationError, match="confdence"):
        Document(
            id="DOC-0001",
            source_uri="x",
            source_kind="text",
            content="",
            ingested_at=NOW,
            created_at=NOW,
            created_by="t",
            confdence=0.5,
        )


def test_naive_datetimes_are_rejected():
    # An assumption's expiry is a comparison against wall-clock time; a naive
    # datetime makes that comparison depend on which machine ran it.
    with pytest.raises(ValidationError):
        Document(
            id="DOC-0001",
            source_uri="x",
            source_kind="text",
            content="",
            ingested_at=datetime(2026, 1, 1),  # noqa: DTZ001
            created_at=NOW,
            created_by="t",
        )


def test_an_id_of_the_wrong_kind_is_rejected():
    # The NewTypes make this a mypy error too, but the store loads rows from
    # SQL where mypy cannot help, so it is checked at runtime as well.
    with pytest.raises(ValidationError, match="not a valid document id"):
        Document(
            id="D-0001",
            source_uri="x",
            source_kind="text",
            content="",
            ingested_at=NOW,
            created_at=NOW,
            created_by="t",
        )


def test_offset_aware_timestamps_compare_by_instant():
    # The store writes ISO-8601 with an offset rather than normalising to UTC,
    # so this is the property that keeps a round-trip lossless.
    india = timezone(timedelta(hours=5, minutes=30))
    assert datetime(2026, 8, 11, 9, 0, tzinfo=UTC) == datetime(2026, 8, 11, 14, 30, tzinfo=india)


# --------------------------------------------------------------------------
# Document and Span
# --------------------------------------------------------------------------


def test_document_hash_and_length_are_derived_from_content():
    doc = make_document("wörld")
    assert doc.byte_length == 6  # ö is two bytes
    assert doc.content_hash == make_document("wörld").content_hash
    assert doc.content_hash != make_document("world").content_hash


def test_span_carries_the_exact_text_at_its_offsets():
    doc = make_document()
    span = Span.covering(doc, 0, 5, created_by="t", created_at=NOW)
    assert span.text == "hello"
    assert doc.content_bytes[span.start_byte : span.end_byte].decode() == span.text


def test_span_id_is_derived_when_omitted():
    doc = make_document()
    explicit = Span.covering(doc, 0, 5, created_by="t", created_at=NOW)
    implicit = Span(
        doc_id=doc.id,
        start_byte=0,
        end_byte=5,
        text="hello",
        created_by="t",
        created_at=NOW,
    )
    assert implicit.id == explicit.id


def test_a_span_id_that_addresses_a_different_range_is_rejected():
    doc = make_document()
    elsewhere = Span.covering(doc, 6, 9, created_by="t", created_at=NOW)
    with pytest.raises(ValidationError, match="does not address"):
        Span(
            id=elsewhere.id,
            doc_id=doc.id,
            start_byte=0,
            end_byte=5,
            text="hello",
            created_by="t",
            created_at=NOW,
        )


def test_a_span_whose_text_does_not_fill_its_range_is_rejected():
    with pytest.raises(ValidationError, match="bytes wide"):
        Span(
            doc_id="DOC-0001",
            start_byte=0,
            end_byte=5,
            text="hi",
            created_by="t",
            created_at=NOW,
        )


def test_empty_spans_are_rejected():
    with pytest.raises(ValidationError):
        Span(doc_id="DOC-0001", start_byte=4, end_byte=4, text="", created_by="t", created_at=NOW)


def test_covering_refuses_a_range_that_splits_a_character():
    doc = make_document("wörld")
    with pytest.raises(ValueError, match="splits a character"):
        Span.covering(doc, 1, 2, created_by="t", created_at=NOW)


def test_covering_refuses_a_range_past_the_end():
    doc = make_document("short")
    with pytest.raises(ValueError, match="not a range within"):
        Span.covering(doc, 0, 99, created_by="t", created_at=NOW)


# --------------------------------------------------------------------------
# Decision and Assumption
# --------------------------------------------------------------------------


@given(decision=s.decisions())
def test_a_decision_always_records_what_it_turned_down(decision: Decision):
    assert len(decision.rejected) >= 1


def test_a_decision_with_no_rejected_options_is_rejected():
    with pytest.raises(ValidationError):
        Decision(
            id="D-0001",
            title="Use SQLite",
            chosen="SQLite",
            rejected=(),
            decision_maker="Sihan Udayaratna",
            decided_at=NOW,
            scope="project",
            impact="high",
            span_id=make_span().id,
            confidence=0.8,
            created_at=NOW,
            created_by="t",
        )


@pytest.mark.parametrize(
    ("status", "evaluated_at"),
    [(AssumptionStatus.BREACHED, None), (AssumptionStatus.UNVERIFIED, NOW)],
)
def test_assumption_status_must_agree_with_its_evaluation_time(status, evaluated_at):
    with pytest.raises(ValidationError):
        Assumption(
            id="A-0001",
            statement="FTS5 ships with the bundled SQLite",
            predicate="fts5_available == true",
            expiry_condition='on_event("a new target platform is added")',
            status=status,
            last_evaluated_at=evaluated_at,
            span_id=make_span().id,
            confidence=0.9,
            created_at=NOW,
            created_by="t",
        )


# --------------------------------------------------------------------------
# Estimate and Outcome -- the active/blocked split
# --------------------------------------------------------------------------


def make_estimate(**overrides: object) -> Estimate:
    fields: dict[str, object] = {
        "id": "EST-0002",
        "subject": "Phase 1 - data model and store",
        "owner": "claude-opus-5",
        "work_class": "data-modelling",
        "active_quantity": Decimal("2.0"),
        "blocked_quantity": Decimal("0"),
        "unit": Unit.HOURS,
        "confidence": 0.5,
        "estimated_at": NOW,
        "span_id": make_span().id,
        "created_at": NOW,
        "created_by": "claude-opus-5",
    }
    return Estimate(**(fields | overrides))


def test_an_estimate_totals_its_two_parts():
    estimate = make_estimate(active_quantity=Decimal("1.1"), blocked_quantity=Decimal("1.52"))
    assert estimate.total_quantity == Decimal("2.62")


def test_blocked_time_defaults_to_zero_rather_than_being_absent():
    # OUT-0001's lesson: a total that silently includes blocked time reads as an
    # accurate estimate. Zero has to be a stated prediction, not a missing one.
    assert make_estimate().blocked_quantity == Decimal("0")


def test_an_estimate_of_nothing_is_rejected():
    with pytest.raises(ValidationError, match="predicts nothing"):
        make_estimate(active_quantity=Decimal("0"), blocked_quantity=Decimal("0"))


def test_a_float_quantity_is_rejected():
    # Invariant 4: money and quantities are Decimal. Strict mode is what stops a
    # float entering here and being summed across a calibration history.
    with pytest.raises(ValidationError):
        make_estimate(active_quantity=2.0)


def test_an_infinite_quantity_is_rejected():
    # Decimal('Infinity') >= 0 is true, so the bound alone would let it through.
    with pytest.raises(ValidationError):
        make_estimate(active_quantity=Decimal("Infinity"))


def make_outcome(**overrides: object) -> Outcome:
    fields: dict[str, object] = {
        "id": "OUT-0002",
        "estimate_id": "EST-0002",
        "active_quantity": Decimal("1.1"),
        "blocked_quantity": Decimal("1.52"),
        "unit": Unit.HOURS,
        "match_quality": MatchQuality.PARTIAL,
        "resolved_at": NOW,
        "created_at": NOW,
        "created_by": "claude-opus-5",
    }
    return Outcome(**(fields | overrides))


def test_an_unresolved_outcome_cannot_carry_a_number():
    with pytest.raises(ValidationError, match="unresolved outcome"):
        make_outcome(match_quality=MatchQuality.UNRESOLVED)


def test_a_resolved_outcome_must_carry_a_number():
    with pytest.raises(ValidationError, match="needs a quantity"):
        make_outcome(active_quantity=None, blocked_quantity=None, resolved_at=None)


def test_unresolved_is_representable():
    outcome = make_outcome(
        match_quality=MatchQuality.UNRESOLVED,
        active_quantity=None,
        blocked_quantity=None,
        resolved_at=None,
    )
    assert outcome.match_quality is MatchQuality.UNRESOLVED


# --------------------------------------------------------------------------
# Link -- the grammar of the graph
# --------------------------------------------------------------------------


def make_link(link_type: LinkType, source: str, target: str) -> Link:
    return Link.between(
        link_type,
        source,
        target,
        rationale="because the phase 8 traversal needs this edge to exist",
        confidence=0.9,
        created_by="FusionBridge",
        created_at=NOW,
    )


def test_the_fusion_edge_runs_from_assumption_to_estimate():
    link = make_link(LinkType.ESTIMATED_AS, "A-0001", "EST-0002")
    assert link.source_kind is RecordKind.ASSUMPTION
    assert link.target_kind is RecordKind.ESTIMATE


@pytest.mark.parametrize(
    ("link_type", "source", "target"),
    [
        (LinkType.ASSUMES, "EST-0001", "A-0001"),  # reversed
        (LinkType.ASSUMES, "D-0001", "EST-0001"),  # a decision does not assume an estimate
        (LinkType.ESTIMATED_AS, "D-0001", "EST-0001"),  # only assumptions are estimates in disguise
        (LinkType.SUPERSEDES, "D-0001", "A-0001"),  # replacement is within a kind
        (LinkType.COLLATERAL_OF, "D-0001", "EST-0001"),  # only findings are collateral
    ],
)
def test_edges_the_vocabulary_does_not_express_are_rejected(link_type, source, target):
    with pytest.raises(ValidationError):
        make_link(link_type, source, target)


def test_a_node_cannot_link_to_itself():
    with pytest.raises(ValidationError, match="itself"):
        make_link(LinkType.CONTRADICTS, "D-0001", "D-0001")


def test_a_declared_kind_that_contradicts_the_id_is_rejected():
    with pytest.raises(ValidationError, match="is not a"):
        Link(
            id="L-0000000000000000",
            link_type=LinkType.ASSUMES,
            source_id="D-0001",
            source_kind=RecordKind.DECISION,
            target_id="A-0001",
            target_kind=RecordKind.ESTIMATE,
            confidence=0.5,
            rationale="lying about the target kind",
            created_at=NOW,
            created_by="t",
        )


def test_a_link_id_that_does_not_address_its_endpoints_is_rejected():
    with pytest.raises(ValidationError, match="does not address"):
        Link(
            id="L-0000000000000000",
            link_type=LinkType.ASSUMES,
            source_id="D-0001",
            source_kind=RecordKind.DECISION,
            target_id="A-0001",
            target_kind=RecordKind.ASSUMPTION,
            confidence=0.5,
            rationale="fabricated id",
            created_at=NOW,
            created_by="t",
        )


def test_an_audit_event_cannot_be_an_endpoint():
    with pytest.raises(ValidationError):
        make_link(LinkType.CONTRADICTS, "AUD-0001", "D-0001")


# --------------------------------------------------------------------------
# Finding and AuditEvent
# --------------------------------------------------------------------------


def test_a_verdict_requires_the_challenge_that_produced_it():
    with pytest.raises(ValidationError, match="no challenge was recorded"):
        Finding(
            id="F-0001",
            kind="assumption_breach",
            subject_kind=RecordKind.ASSUMPTION,
            subject_id="A-0001",
            prosecution="the predicate is false as of today",
            challenge=None,
            verdict=Verdict.UPHELD,
            severity=Severity.HIGH,
            confidence=0.7,
            detected_at=NOW,
            created_at=NOW,
            created_by="ChallengerAgent",
        )


def test_a_finding_about_an_audit_event_is_rejected():
    with pytest.raises(ValidationError):
        Finding(
            id="F-0001",
            kind="contradiction",
            subject_kind=RecordKind.AUDIT_EVENT,
            subject_id="AUD-0001",
            prosecution="the log disagrees with itself",
            severity=Severity.LOW,
            confidence=0.1,
            detected_at=NOW,
            created_at=NOW,
            created_by="t",
        )


def test_an_audit_event_has_no_version_of_its_own():
    event = AuditEvent(
        id="AUD-0001",
        occurred_at=NOW,
        actor="claude-opus-5",
        action="created",
        entity_kind=RecordKind.DECISION,
        entity_id="D-0001",
        entity_version=1,
        reason="ingested from docs/adr/0003",
    )
    assert not hasattr(event, "version")
    assert not hasattr(event, "retracted")
