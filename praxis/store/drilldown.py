"""The decision and estimate drill-downs: one record with its whole argument.

Split out of `dashboard.py` to keep both files under the 400-line rule. The
list views are there; the detail views are here. See ADR 0036.
"""

from __future__ import annotations

from dataclasses import dataclass

from praxis.domain.base import VersionedRecord
from praxis.domain.enums import AssumptionStatus
from praxis.domain.ids import NodeId
from praxis.domain.links import LinkType
from praxis.domain.records import (
    Assumption,
    AuditEvent,
    Decision,
    Estimate,
    Finding,
    Outcome,
)
from praxis.store.dashboard import findings_for
from praxis.store.graph import ReachedNode
from praxis.store.repository import Repository

# --- the decision drill-down -----------------------------------------------


@dataclass(frozen=True, slots=True)
class AssumptionLine:
    """One assumption a decision rests on, with whatever priced it.

    Attributes:
        assumption: The assumption.
        estimate: What it turned out to be, if an `estimated_as` edge exists.
        outcome: What happened to that estimate, resolved or not.
        findings: Findings about this assumption.
    """

    assumption: Assumption
    estimate: Estimate | None
    outcome: Outcome | None
    findings: tuple[Finding, ...]


@dataclass(frozen=True, slots=True)
class DecisionDetail:
    """A decision and the whole chain of argument under it.

    Attributes:
        decision: The decision.
        lines: One per assumption, in the order the edges were written.
        findings: Findings about the decision itself.
        audit: Every write to the decision, oldest first.
    """

    decision: Decision
    lines: tuple[AssumptionLine, ...]
    findings: tuple[Finding, ...]
    audit: tuple[AuditEvent, ...]

    @property
    def breached(self) -> tuple[AssumptionLine, ...]:
        """The lines whose assumption has already failed."""
        return tuple(
            line for line in self.lines if line.assumption.status is AssumptionStatus.BREACHED
        )


def decision_detail(repository: Repository, decision_id: NodeId) -> DecisionDetail | None:
    """One decision with its assumptions, their estimates and their outcomes.

    Returns `None` when no current decision has that id, so a route can answer
    404 without a second lookup.
    """
    decision = _standing(repository.get(Decision, decision_id))
    if decision is None:
        return None
    outcomes = {o.estimate_id: o for o in repository.list_all(Outcome)}
    findings = repository.list_all(Finding)
    lines = []
    for edge in repository.links_from(decision_id, [LinkType.ASSUMES]):
        assumption = _standing(repository.get(Assumption, edge.target_id))
        if assumption is None:
            continue
        estimate = _estimate_behind(repository, assumption.id)
        lines.append(
            AssumptionLine(
                assumption=assumption,
                estimate=estimate,
                outcome=outcomes.get(estimate.id) if estimate else None,
                findings=tuple(f for f in findings if f.subject_id == assumption.id),
            )
        )
    return DecisionDetail(
        decision=decision,
        lines=tuple(lines),
        findings=tuple(f for f in findings if f.subject_id == decision_id),
        audit=repository.audit_for(decision_id),
    )


def _estimate_behind(repository: Repository, assumption_id: NodeId) -> Estimate | None:
    """The estimate an assumption turned out to be, if the fusion edge exists."""
    for edge in repository.links_from(assumption_id, [LinkType.ESTIMATED_AS]):
        estimate = _standing(repository.get(Estimate, edge.target_id))
        if estimate is not None:
            return estimate
    return None


def _standing[R: VersionedRecord](record: R | None) -> R | None:
    """Drop a record the store no longer stands behind.

    `Repository.get` returns a retracted record rather than `None`, and an edge
    is not retracted with its target -- so an edge outlives what it points at.
    The list views filter retracted rows in SQL; these walks have to do the
    same or the drill-down would show what the index does not count.
    """
    return None if record is None or record.retracted else record


# --- the estimate drill-down -----------------------------------------------


@dataclass(frozen=True, slots=True)
class EstimateDetail:
    """An estimate, what happened to it, and what leaned on it.

    Attributes:
        estimate: The prediction.
        outcome: The row answering it. `unresolved` rather than absent.
        assumptions: Assumptions that turned out to be this estimate.
        impacted: Records that depend on it, nearest first. Phase 8's
            collateral walk, which the store already had.
        findings: Findings naming the estimate itself.
    """

    estimate: Estimate
    outcome: Outcome | None
    assumptions: tuple[Assumption, ...]
    impacted: tuple[ReachedNode, ...]
    findings: tuple[Finding, ...]


def estimate_detail(repository: Repository, estimate_id: NodeId) -> EstimateDetail | None:
    """One estimate with its outcome and everything that rested on it."""
    estimate = _standing(repository.get(Estimate, estimate_id))
    if estimate is None:
        return None
    outcome = next((o for o in repository.list_all(Outcome) if o.estimate_id == estimate_id), None)
    assumptions = tuple(
        found
        for edge in repository.links_to(estimate_id, [LinkType.ESTIMATED_AS])
        if (found := _standing(repository.get(Assumption, edge.source_id))) is not None
    )
    return EstimateDetail(
        estimate=estimate,
        outcome=outcome,
        assumptions=assumptions,
        impacted=repository.impacted_by(estimate_id),
        findings=findings_for(repository, estimate_id),
    )
