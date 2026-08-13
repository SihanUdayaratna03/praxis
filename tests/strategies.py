"""Hypothesis strategies that generate valid records of every kind.

Written once here rather than per test file, because the round-trip property
and the append-only property need exactly the same generators and any drift
between two copies would weaken whichever one drifted.

The strategies aim at the awkward end of the input space on purpose: astral
plane characters, timezones either side of UTC, Decimal quantities with more
digits than a float can hold. Those are what break a store, and generating only
comfortable values would prove nothing about one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from hypothesis import strategies as st
from praxis.domain import ids
from praxis.domain.enums import (
    AssumptionStatus,
    DecisionScope,
    DecisionStatus,
    FindingKind,
    Impact,
    MatchQuality,
    RecordKind,
    Severity,
    SourceKind,
    Unit,
    Verdict,
)
from praxis.domain.records import (
    Assumption,
    Decision,
    Document,
    Estimate,
    Finding,
    Outcome,
    RejectedOption,
    Span,
)

# Deliberately includes surrogate-free astral characters: a store that mangles
# an emoji mangles a byte offset, and every span in the system is a byte offset.
TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=1,
    max_size=60,
)
PROSE = TEXT.map(str.strip).filter(bool)

AWARE_DATETIMES = st.datetimes(
    min_value=datetime(2000, 1, 1),  # noqa: DTZ001 -- a bound, not a timestamp
    max_value=datetime(2100, 1, 1),  # noqa: DTZ001
    timezones=st.sampled_from(
        [UTC, timezone(timedelta(hours=5, minutes=30)), timezone(timedelta(hours=-8))]
    ),
)

QUANTITIES = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("10000"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)
POSITIVE_QUANTITIES = QUANTITIES.filter(lambda d: d > 0)

CONFIDENCES = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

WORK_CLASSES = st.sampled_from(
    ["scaffolding", "data-modelling", "agent-implementation", "eval-harness", "docs"]
)

ACTORS = st.sampled_from(["claude-opus-5", "DecisionScout", "FusionBridge", "Sihan Udayaratna"])


def ordinals() -> st.SearchStrategy[int]:
    """Sequential-id counters, kept small enough to collide usefully."""
    return st.integers(min_value=1, max_value=999)


def sequential_ids(kind: RecordKind) -> st.SearchStrategy[str]:
    """Well-formed ids of a kind that allocates them from a counter."""
    return ordinals().map(lambda n: ids.format_sequential_id(kind, n))


@st.composite
def documents(draw: st.DrawFn) -> Document:
    """A document with content chosen to make byte offsets non-trivial."""
    return Document(
        id=draw(sequential_ids(RecordKind.DOCUMENT)),
        source_uri=draw(PROSE),
        source_kind=draw(st.sampled_from(list(SourceKind))),
        title=draw(st.one_of(st.none(), PROSE)),
        content=draw(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=200)),
        ingested_at=draw(AWARE_DATETIMES),
        created_at=draw(AWARE_DATETIMES),
        created_by=draw(ACTORS),
    )


@st.composite
def spans(draw: st.DrawFn, document: Document | None = None) -> Span:
    """A span cut from a real document, so it resolves by construction.

    Character indices are converted to byte offsets rather than generated
    directly, which is the only way to guarantee the range never splits a
    multi-byte character.
    """
    doc = document if document is not None else draw(documents().filter(lambda d: d.content))
    characters = len(doc.content)
    start_char = draw(st.integers(min_value=0, max_value=max(0, characters - 1)))
    end_char = draw(st.integers(min_value=start_char + 1, max_value=characters))
    start_byte = len(doc.content[:start_char].encode("utf-8"))
    end_byte = len(doc.content[:end_char].encode("utf-8"))
    return Span.covering(
        doc,
        start_byte,
        end_byte,
        created_by=draw(ACTORS),
        created_at=draw(AWARE_DATETIMES),
    )


@st.composite
def decisions(draw: st.DrawFn, span_id: str | None = None) -> Decision:
    """A decision with at least one rejected alternative, as the schema demands."""
    return Decision(
        id=draw(sequential_ids(RecordKind.DECISION)),
        title=draw(PROSE),
        chosen=draw(PROSE),
        rejected=tuple(
            draw(
                st.lists(
                    st.builds(RejectedOption, option=PROSE, reason=PROSE),
                    min_size=1,
                    max_size=3,
                )
            )
        ),
        decision_maker=draw(ACTORS),
        decided_at=draw(AWARE_DATETIMES),
        scope=draw(st.sampled_from(list(DecisionScope))),
        impact=draw(st.sampled_from(list(Impact))),
        status=draw(st.sampled_from(list(DecisionStatus))),
        span_id=span_id if span_id is not None else draw(spans()).id,
        confidence=draw(CONFIDENCES),
        created_at=draw(AWARE_DATETIMES),
        created_by=draw(ACTORS),
    )


@st.composite
def assumptions(draw: st.DrawFn, span_id: str | None = None) -> Assumption:
    """An assumption whose status and evaluation time agree with each other."""
    status = draw(st.sampled_from(list(AssumptionStatus)))
    evaluated = status is not AssumptionStatus.UNVERIFIED
    return Assumption(
        id=draw(sequential_ids(RecordKind.ASSUMPTION)),
        statement=draw(PROSE),
        predicate=draw(PROSE),
        expiry_condition=draw(PROSE),
        status=status,
        last_evaluated_at=draw(AWARE_DATETIMES) if evaluated else None,
        span_id=span_id if span_id is not None else draw(spans()).id,
        confidence=draw(CONFIDENCES),
        created_at=draw(AWARE_DATETIMES),
        created_by=draw(ACTORS),
    )


@st.composite
def estimates(draw: st.DrawFn, span_id: str | None = None) -> Estimate:
    """An estimate that predicts a non-zero total across the active/blocked split."""
    active = draw(QUANTITIES)
    blocked = draw(QUANTITIES if active > 0 else POSITIVE_QUANTITIES)
    return Estimate(
        id=draw(sequential_ids(RecordKind.ESTIMATE)),
        subject=draw(PROSE),
        owner=draw(ACTORS),
        work_class=draw(WORK_CLASSES),
        active_quantity=active,
        blocked_quantity=blocked,
        unit=draw(st.sampled_from(list(Unit))),
        confidence=draw(CONFIDENCES),
        conditions=tuple(draw(st.lists(PROSE, max_size=3))),
        estimated_at=draw(AWARE_DATETIMES),
        span_id=span_id if span_id is not None else draw(spans()).id,
        created_at=draw(AWARE_DATETIMES),
        created_by=draw(ACTORS),
    )


@st.composite
def outcomes(draw: st.DrawFn, estimate_id: str | None = None) -> Outcome:
    """An outcome, resolved or explicitly unresolved but never half of each."""
    quality = draw(st.sampled_from(list(MatchQuality)))
    resolved = quality is not MatchQuality.UNRESOLVED
    return Outcome(
        id=draw(sequential_ids(RecordKind.OUTCOME)),
        estimate_id=(
            estimate_id if estimate_id is not None else draw(sequential_ids(RecordKind.ESTIMATE))
        ),
        active_quantity=draw(QUANTITIES) if resolved else None,
        blocked_quantity=draw(QUANTITIES) if resolved else None,
        unit=draw(st.sampled_from(list(Unit))),
        match_quality=quality,
        resolved_at=draw(AWARE_DATETIMES) if resolved else None,
        notes=draw(st.text(max_size=80)),
        span_id=None,
        created_at=draw(AWARE_DATETIMES),
        created_by=draw(ACTORS),
    )


@st.composite
def findings(draw: st.DrawFn, subject_id: str | None = None) -> Finding:
    """A finding whose verdict, if any, is backed by a recorded challenge."""
    verdict = draw(st.sampled_from(list(Verdict)))
    subject = subject_id if subject_id is not None else draw(sequential_ids(RecordKind.DECISION))
    return Finding(
        id=draw(sequential_ids(RecordKind.FINDING)),
        kind=draw(st.sampled_from(list(FindingKind))),
        subject_kind=ids.kind_of(subject),
        subject_id=subject,
        prosecution=draw(PROSE),
        challenge=draw(PROSE) if verdict is not Verdict.UNDECIDED else draw(st.none() | PROSE),
        verdict=verdict,
        severity=draw(st.sampled_from(list(Severity))),
        confidence=draw(CONFIDENCES),
        evidence_span_ids=(),
        detected_at=draw(AWARE_DATETIMES),
        created_at=draw(AWARE_DATETIMES),
        created_by=draw(ACTORS),
    )
