"""What the API returns, as models rather than as hand-built dicts.

Pydantic because the domain records already are, so a field that changes shape
fails here rather than in the browser. Decimals serialise as strings: invariant
4 does not stop at the HTTP boundary.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from praxis.agents.bias import CalibrationFactor
from praxis.agents.fusion import PricedAssumption
from praxis.domain.records import (
    Assumption,
    AuditEvent,
    Decision,
    Estimate,
    Finding,
    Outcome,
)
from praxis.store.reports import CalibrationRow


class DecisionOut(BaseModel):
    """A decision, flattened for the page."""

    id: str
    title: str
    chosen: str
    rejected: list[dict[str, str]]
    decision_maker: str
    decided_at: datetime
    scope: str
    impact: str
    status: str
    confidence: float


def decision_out(record: Decision) -> DecisionOut:
    """Convert one decision."""
    return DecisionOut(
        id=record.id,
        title=record.title,
        chosen=record.chosen,
        rejected=[{"option": r.option, "reason": r.reason} for r in record.rejected],
        decision_maker=record.decision_maker,
        decided_at=record.decided_at,
        scope=record.scope.value,
        impact=record.impact.value,
        status=record.status.value,
        confidence=record.confidence,
    )


class AssumptionOut(BaseModel):
    """An assumption and the predicate that decides it."""

    id: str
    statement: str
    predicate: str
    expiry_condition: str
    status: str
    last_evaluated_at: datetime | None
    confidence: float


def assumption_out(record: Assumption) -> AssumptionOut:
    """Convert one assumption."""
    return AssumptionOut(
        id=record.id,
        statement=record.statement,
        predicate=record.predicate,
        expiry_condition=record.expiry_condition,
        status=record.status.value,
        last_evaluated_at=record.last_evaluated_at,
        confidence=record.confidence,
    )


class EstimateOut(BaseModel):
    """An estimate as a prediction, not as a number."""

    id: str
    subject: str
    owner: str
    work_class: str
    active_quantity: Decimal
    blocked_quantity: Decimal
    unit: str
    confidence: float
    conditions: list[str]
    estimated_at: datetime


def estimate_out(record: Estimate) -> EstimateOut:
    """Convert one estimate."""
    return EstimateOut(
        id=record.id,
        subject=record.subject,
        owner=record.owner,
        work_class=record.work_class,
        active_quantity=record.active_quantity,
        blocked_quantity=record.blocked_quantity,
        unit=record.unit.value,
        confidence=record.confidence,
        conditions=list(record.conditions),
        estimated_at=record.estimated_at,
    )


class OutcomeOut(BaseModel):
    """What happened to an estimate."""

    id: str
    estimate_id: str
    active_quantity: Decimal | None
    blocked_quantity: Decimal | None
    unit: str
    match_quality: str
    resolved_at: datetime | None
    notes: str


def outcome_out(record: Outcome) -> OutcomeOut:
    """Convert one outcome."""
    return OutcomeOut(
        id=record.id,
        estimate_id=record.estimate_id,
        active_quantity=record.active_quantity,
        blocked_quantity=record.blocked_quantity,
        unit=record.unit.value,
        match_quality=record.match_quality.value,
        resolved_at=record.resolved_at,
        notes=record.notes,
    )


class FindingOut(BaseModel):
    """A finding, with the prosecution the challenger argued against."""

    id: str
    kind: str
    subject_id: str
    subject_kind: str
    prosecution: str
    challenge: str | None
    verdict: str
    severity: str
    confidence: float
    detected_at: datetime


def finding_out(record: Finding) -> FindingOut:
    """Convert one finding."""
    return FindingOut(
        id=record.id,
        kind=record.kind.value,
        subject_id=record.subject_id,
        subject_kind=record.subject_kind.value,
        prosecution=record.prosecution,
        challenge=record.challenge,
        verdict=record.verdict.value,
        severity=record.severity.value,
        confidence=record.confidence,
        detected_at=record.detected_at,
    )


class AuditOut(BaseModel):
    """One write, as the timeline shows it."""

    id: str
    occurred_at: datetime
    actor: str
    action: str
    entity_kind: str
    entity_id: str
    entity_version: int
    reason: str
    run_id: str | None


def audit_out(record: AuditEvent) -> AuditOut:
    """Convert one audit event."""
    return AuditOut(
        id=record.id,
        occurred_at=record.occurred_at,
        actor=record.actor,
        action=record.action.value,
        entity_kind=record.entity_kind.value,
        entity_id=record.entity_id,
        entity_version=record.entity_version,
        reason=record.reason,
        run_id=record.run_id,
    )


class FactorOut(BaseModel):
    """What `BiasDetective` concluded about one estimator and one class.

    `speaks` is the field the page must respect: a refusal is a result, and
    rendering it as a factor of zero would be a lie about the evidence.
    """

    owner: str
    work_class: str
    speaks: bool
    n: int
    factor: Decimal | None
    low: Decimal | None
    high: Decimal | None
    confidence: Decimal | None
    magnitude: Decimal | None
    direction: str | None
    reason: str
    describe: str


def factor_out(factor: CalibrationFactor) -> FactorOut:
    """Convert one calibration factor, refusals included."""
    return FactorOut(
        owner=factor.group.owner,
        work_class=factor.group.work_class,
        speaks=factor.speaks,
        n=factor.n,
        factor=factor.factor,
        low=factor.low,
        high=factor.high,
        confidence=factor.confidence,
        magnitude=factor.magnitude,
        direction=factor.direction.value if factor.direction else None,
        reason=factor.reason,
        describe=factor.describe(),
    )


class CalibrationRowOut(BaseModel):
    """One estimate beside its actual, for the scatter behind the curves."""

    estimate_id: str
    owner: str
    work_class: str
    unit: str
    subject: str
    estimated_active: Decimal
    estimated_blocked: Decimal
    outcome_id: str
    actual_active: Decimal | None
    actual_blocked: Decimal | None
    match_quality: str
    resolved: bool


def calibration_row_out(row: CalibrationRow) -> CalibrationRowOut:
    """Convert one calibration row."""
    return CalibrationRowOut(
        estimate_id=row.estimate_id,
        owner=row.owner,
        work_class=row.work_class,
        unit=row.unit.value,
        subject=row.subject,
        estimated_active=row.estimated_active,
        estimated_blocked=row.estimated_blocked,
        outcome_id=row.outcome_id,
        actual_active=row.actual_active,
        actual_blocked=row.actual_blocked,
        match_quality=row.match_quality.value,
        resolved=row.resolved,
    )


class FusionOut(BaseModel):
    """One priced `estimated_as` edge -- the argument the phase exists to show.

    `describe` is `PricedAssumption.describe()` verbatim, so the page and the
    CLI say the same sentence rather than two that drift.
    """

    assumption_id: str
    assumption: AssumptionOut
    estimate: EstimateOut | None
    verdict: str
    subject: str | None
    raw: Decimal | None
    calibrated: Decimal | None
    low: Decimal | None
    high: Decimal | None
    factor: FactorOut | None
    flips: bool
    priced: bool
    reason: str
    describe: str


def fusion_out(result: PricedAssumption) -> FusionOut:
    """Convert one priced assumption."""
    return FusionOut(
        assumption_id=result.assumption.id,
        assumption=assumption_out(result.assumption),
        estimate=estimate_out(result.estimate) if result.estimate else None,
        verdict=result.verdict.value,
        subject=result.subject,
        raw=result.raw,
        calibrated=result.calibrated,
        low=result.low,
        high=result.high,
        factor=factor_out(result.factor) if result.factor else None,
        flips=result.flips,
        priced=result.priced,
        reason=result.reason,
        describe=result.describe(),
    )
