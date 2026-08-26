"""Fixtures for the monitoring tests: a real store, and records that fit in it.

Built through `Repository` rather than as loose objects, because half of what
the monitor does is decide whether to *write*, and a double that accepted every
write would let the re-runnability tests pass against nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from praxis.domain.enums import DecisionScope, Impact, MatchQuality, Unit
from praxis.domain.links import LinkType
from praxis.domain.records import (
    Assumption,
    Decision,
    Document,
    Estimate,
    Link,
    Outcome,
    RejectedOption,
    Span,
)
from praxis.ingest.adapters import MARKDOWN_ADAPTER, document_from
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

AT = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
ACTOR = "test"

BODY = """\
# ADR: the product search index

## Decision

We are going with OpenSearch on managed nodes, rather than Postgres full-text
search. Decided by Nadeesha for the Discovery team.

## Assumptions

- This rests on the assumption that the index stays under 50 GB for the next
  year. In predicate form: `index_size_gb <= 50`.

## Effort

Nadeesha put this at 4 weeks of hands-on work, blocked time aside.
"""


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


@pytest.fixture
def document(store: Repository) -> Document:
    """One document through the real adapter, written to the store."""
    source = MARKDOWN_ADAPTER.normalise(BODY.encode("utf-8"), source_uri="adr.md")
    record = document_from(source, doc_id="DOC-0001", ingested_at=AT)
    return store.add(record, actor=ACTOR, reason="fixture")


@pytest.fixture
def span(store: Repository, document: Document) -> Span:
    """One span covering the whole document, so a citation always resolves."""
    record = Span.covering(
        document, 0, len(document.content.encode("utf-8")), created_by=ACTOR, created_at=AT
    )
    return store.add(record, actor=ACTOR, reason="fixture")


def make_assumption(  # noqa: PLR0913 -- every field a test varies, named
    span: Span,
    *,
    assumption_id: str = "A-0001",
    predicate: str = "index_size_gb <= 50",
    expiry: str = 'on_event("the index is re-sharded")',
    statement: str = "the index stays under 50 GB for the next year",
    confidence: float = 0.8,
) -> Assumption:
    """A formalized assumption -- both fields parse unless a test says otherwise."""
    return Assumption(
        id=assumption_id,
        statement=statement,
        predicate=predicate,
        expiry_condition=expiry,
        span_id=span.id,
        confidence=confidence,
        created_by="AssumptionFormalizer",
        created_at=AT,
    )


def make_decision(
    span: Span,
    *,
    decision_id: str = "D-0001",
    impact: Impact = Impact.MEDIUM,
    scope: DecisionScope = DecisionScope.TEAM,
) -> Decision:
    """A decision that can rest on an assumption."""
    return Decision(
        id=decision_id,
        title="the product search index",
        chosen="OpenSearch on managed nodes",
        rejected=(RejectedOption(option="Postgres full-text search", reason="ranking quality"),),
        decision_maker="Nadeesha",
        decided_at=AT,
        scope=scope,
        impact=impact,
        span_id=span.id,
        confidence=0.9,
        created_by=ACTOR,
        created_at=AT,
    )


def rest_on(store: Repository, decision: Decision, assumption: Assumption) -> Link:
    """Write the `assumes` edge that makes a breach reach a decision."""
    return store.add(
        Link.between(
            LinkType.ASSUMES,
            decision.id,
            assumption.id,
            rationale=assumption.statement,
            confidence=0.9,
            created_by=ACTOR,
            created_at=AT,
            span_id=assumption.span_id,
        ),
        actor=ACTOR,
        reason="fixture",
    )


def measure(  # noqa: PLR0913 -- the whole fusion chain in one call
    store: Repository,
    span: Span,
    assumption: Assumption,
    *,
    estimated: Decimal,
    actual: Decimal,
    unit: Unit = Unit.WEEKS,
    outcome_unit: Unit | None = None,
) -> Outcome:
    """The fusion chain: assumption -> estimated_as -> estimate <- outcome.

    The one binding the store itself asserts, and the product's central claim in
    miniature -- a measured outcome reaching back to the assumption a decision
    rests on.
    """
    estimate = store.add(
        Estimate(
            id="EST-0001",
            subject=assumption.statement,
            owner="Nadeesha",
            work_class="infrastructure",
            active_quantity=estimated,
            unit=unit,
            confidence=0.6,
            estimated_at=AT,
            span_id=span.id,
            created_by=ACTOR,
            created_at=AT,
        ),
        actor=ACTOR,
        reason="fixture",
    )
    store.add(
        Link.between(
            LinkType.ESTIMATED_AS,
            assumption.id,
            estimate.id,
            rationale="a quantified forward-looking claim about effort",
            confidence=0.7,
            created_by=ACTOR,
            created_at=AT,
            span_id=span.id,
        ),
        actor=ACTOR,
        reason="fixture",
    )
    return store.add(
        Outcome(
            id="OUT-0001",
            estimate_id=estimate.id,
            active_quantity=actual,
            unit=outcome_unit if outcome_unit is not None else unit,
            match_quality=MatchQuality.MISS,
            resolved_at=AT,
            created_by=ACTOR,
            created_at=AT,
        ),
        actor=ACTOR,
        reason="fixture",
    )
