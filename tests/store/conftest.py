"""A migrated store, and a small coherent graph to ask questions of.

Every store test wants the same three things: an empty migrated database, a
fixed timestamp so nothing depends on the clock, and a handful of records that
actually reference each other. Building that per file would mean four slightly
different versions of the same graph, and a test asserting on the wrong one
reads exactly like a test asserting on a bug.

The store is in memory. A file per test would put the hypothesis budget into the
filesystem, and nothing here is testing durability.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from praxis.domain.enums import (
    DecisionScope,
    FindingKind,
    Impact,
    MatchQuality,
    RecordKind,
    Severity,
    SourceKind,
    Unit,
)
from praxis.domain.ids import (
    AssumptionId,
    DecisionId,
    DocumentId,
    EstimateId,
    FindingId,
    NodeId,
    OutcomeId,
)
from praxis.domain.links import LinkType
from praxis.domain.records import (
    Assumption,
    Decision,
    Document,
    Estimate,
    Finding,
    Link,
    Outcome,
    RejectedOption,
    Span,
)
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

WRITTEN_AT = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
"""One fixed, offset-aware instant. Tests that need ordering add to it."""

ACTOR = "test-suite"


@pytest.fixture
def store() -> Iterator[Repository]:
    """An empty, migrated store."""
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


@dataclass(frozen=True, slots=True)
class World:
    """The smallest graph that exercises the fusion path.

        D-0001 --assumes--> A-0001 --estimated_as--> EST-0001
        D-0002 --justified_by--> EST-0001

    Two routes to the same estimate, one direct and one two hops long, which is
    what makes the shortest-distance behaviour of a walk observable.

    An `Outcome` and a `Finding` hang off it as well. They take part in no edge,
    and they are here because a table with no rows in it cannot demonstrate that
    its append-only triggers work.
    """

    document: Document
    span: Span
    decision: Decision
    other_decision: Decision
    assumption: Assumption
    estimate: Estimate
    outcome: Outcome
    finding: Finding


@pytest.fixture
def world(store: Repository) -> World:
    """Write the fixture graph and return it."""
    return build_world(store)


def build_world(store: Repository) -> World:
    """Write a document, a span, two decisions, an assumption and an estimate."""
    document = _add(
        store,
        Document(
            id=DocumentId(store.next_id(RecordKind.DOCUMENT)),
            source_uri="file://plan.md",
            source_kind=SourceKind.MARKDOWN,
            title="Migration plan",
            content="We migrate the ledger in six weeks.",
            ingested_at=WRITTEN_AT,
            created_at=WRITTEN_AT,
            created_by=ACTOR,
        ),
    )
    span = _add(store, Span.covering(document, 0, 11, created_by=ACTOR, created_at=WRITTEN_AT))
    decision = _add(store, _decision(store, "Ledger backend", span.id))
    other = _add(store, _decision(store, "Migration window", span.id))
    assumption = _add(
        store,
        Assumption(
            id=AssumptionId(store.next_id(RecordKind.ASSUMPTION)),
            statement="the migration is short",
            predicate="migration_weeks <= 6",
            expiry_condition="when(phases_completed >= 6)",
            span_id=span.id,
            confidence=0.6,
            created_at=WRITTEN_AT,
            created_by=ACTOR,
        ),
    )
    estimate = _add(
        store,
        Estimate(
            id=EstimateId(store.next_id(RecordKind.ESTIMATE)),
            subject="ledger migration",
            owner="Sihan Udayaratna",
            work_class="data-modelling",
            active_quantity=Decimal("6.5"),
            blocked_quantity=Decimal("0.25"),
            unit=Unit.WEEKS,
            confidence=0.5,
            conditions=("no scope change",),
            estimated_at=WRITTEN_AT,
            span_id=span.id,
            created_at=WRITTEN_AT,
            created_by=ACTOR,
        ),
    )
    outcome = _add(
        store,
        Outcome(
            id=OutcomeId(store.next_id(RecordKind.OUTCOME)),
            estimate_id=estimate.id,
            active_quantity=Decimal("9.0"),
            blocked_quantity=Decimal("1.5"),
            unit=Unit.WEEKS,
            match_quality=MatchQuality.MISS,
            resolved_at=WRITTEN_AT,
            notes="the ledger rewrite took longer than the schema work",
            created_at=WRITTEN_AT,
            created_by=ACTOR,
        ),
    )
    finding = _add(
        store,
        Finding(
            id=FindingId(store.next_id(RecordKind.FINDING)),
            kind=FindingKind.ASSUMPTION_BREACH,
            subject_kind=RecordKind.ASSUMPTION,
            subject_id=assumption.id,
            prosecution="the migration ran to nine weeks against a predicate of six",
            severity=Severity.HIGH,
            confidence=0.9,
            evidence_span_ids=(span.id,),
            detected_at=WRITTEN_AT,
            created_at=WRITTEN_AT,
            created_by=ACTOR,
        ),
    )
    link(store, LinkType.ASSUMES, decision.id, assumption.id)
    link(store, LinkType.ESTIMATED_AS, assumption.id, estimate.id)
    link(store, LinkType.JUSTIFIED_BY, other.id, estimate.id)
    return World(
        document=document,
        span=span,
        decision=decision,
        other_decision=other,
        assumption=assumption,
        estimate=estimate,
        outcome=outcome,
        finding=finding,
    )


def link(store: Repository, link_type: LinkType, source: NodeId, target: NodeId) -> Link:
    """Assert an edge, with the fixture's actor and timestamp."""
    return _add(
        store,
        Link.between(
            link_type,
            source,
            target,
            rationale="asserted by the fixture",
            confidence=0.9,
            created_by=ACTOR,
            created_at=WRITTEN_AT,
        ),
    )


def _decision(store: Repository, title: str, span_id: str) -> Decision:
    return Decision(
        id=DecisionId(store.next_id(RecordKind.DECISION)),
        title=title,
        chosen="SQLite",
        rejected=(RejectedOption(option="Postgres", reason="needs a server nobody will run"),),
        decision_maker="Sihan Udayaratna",
        decided_at=WRITTEN_AT,
        scope=DecisionScope.PROJECT,
        impact=Impact.HIGH,
        span_id=span_id,
        confidence=0.8,
        created_at=WRITTEN_AT,
        created_by=ACTOR,
    )


def _add[R](store: Repository, record: R) -> R:
    return store.add(record, actor=ACTOR, reason="written by the fixture", at=WRITTEN_AT)
