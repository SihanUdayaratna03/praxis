"""The composite shapes: an index row, a drill-down, a queue item.

Split from `payloads.py` to keep both under the 400-line rule. These are the
models that stitch several records into one response.
"""

from __future__ import annotations

from pydantic import BaseModel

from praxis.store import dashboard, drilldown
from praxis.web.payloads import (
    AssumptionOut,
    AuditOut,
    DecisionOut,
    EstimateOut,
    FindingOut,
    OutcomeOut,
    assumption_out,
    audit_out,
    decision_out,
    estimate_out,
    finding_out,
    outcome_out,
)


class DecisionSummaryOut(BaseModel):
    """A decision as the index lists it."""

    decision: DecisionOut
    assumptions: int
    breached: int
    findings: int
    at_risk: bool


def decision_summary_out(summary: dashboard.DecisionSummary) -> DecisionSummaryOut:
    """Convert one index row."""
    return DecisionSummaryOut(
        decision=decision_out(summary.decision),
        assumptions=summary.assumptions,
        breached=summary.breached,
        findings=summary.findings,
        at_risk=summary.at_risk,
    )


class AssumptionLineOut(BaseModel):
    """One link in a decision's chain of argument."""

    assumption: AssumptionOut
    estimate: EstimateOut | None
    outcome: OutcomeOut | None
    findings: list[FindingOut]


class DecisionDetailOut(BaseModel):
    """A decision with everything under it."""

    decision: DecisionOut
    lines: list[AssumptionLineOut]
    findings: list[FindingOut]
    audit: list[AuditOut]


def decision_detail_out(detail: drilldown.DecisionDetail) -> DecisionDetailOut:
    """Convert one drill-down."""
    return DecisionDetailOut(
        decision=decision_out(detail.decision),
        lines=[
            AssumptionLineOut(
                assumption=assumption_out(line.assumption),
                estimate=estimate_out(line.estimate) if line.estimate else None,
                outcome=outcome_out(line.outcome) if line.outcome else None,
                findings=[finding_out(f) for f in line.findings],
            )
            for line in detail.lines
        ],
        findings=[finding_out(f) for f in detail.findings],
        audit=[audit_out(e) for e in detail.audit],
    )


class ReachedOut(BaseModel):
    """A record the collateral walk reached, and how far away it was."""

    id: str
    kind: str
    depth: int


class EstimateDetailOut(BaseModel):
    """An estimate, its outcome, and everything that leaned on it."""

    estimate: EstimateOut
    outcome: OutcomeOut | None
    assumptions: list[AssumptionOut]
    impacted: list[ReachedOut]
    findings: list[FindingOut]


def estimate_detail_out(detail: drilldown.EstimateDetail) -> EstimateDetailOut:
    """Convert one estimate drill-down."""
    return EstimateDetailOut(
        estimate=estimate_out(detail.estimate),
        outcome=outcome_out(detail.outcome) if detail.outcome else None,
        assumptions=[assumption_out(a) for a in detail.assumptions],
        impacted=[
            ReachedOut(id=node.id, kind=node.kind.value, depth=node.depth)
            for node in detail.impacted
        ],
        findings=[finding_out(f) for f in detail.findings],
    )


class QueueItemOut(BaseModel):
    """A finding as the review queue lists it."""

    finding: FindingOut
    subject_label: str


def queue_item_out(item: dashboard.QueueItem) -> QueueItemOut:
    """Convert one queue row."""
    return QueueItemOut(finding=finding_out(item.finding), subject_label=item.subject_label)
