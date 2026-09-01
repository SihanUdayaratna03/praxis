"""The read-only API. Every handler is a store read plus a conversion.

No SQL here -- `test_only_the_store_knows_the_database_driver` enforces that.
No writes anywhere: ADR 0003's single-writer assumption depends on it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from praxis.agents.bias import MINIMUM_SAMPLE, BiasDetective
from praxis.agents.collateral import CollateralAgent
from praxis.agents.fusion import FusionBridge
from praxis.domain.enums import AssumptionStatus, RecordKind
from praxis.domain.ids import NodeId
from praxis.domain.records import Assumption, Estimate
from praxis.store import dashboard, drilldown, traces
from praxis.store.reports import calibration_history, stats
from praxis.store.repository import Repository
from praxis.web import payloads, views

router = APIRouter(prefix="/api")


def store(request: Request) -> Iterator[Repository]:
    """The repository the server opened, held for the length of one request.

    One connection behind one lock. Starlette runs these handlers in a
    threadpool, and the store's handle is opened `cross_thread` for that
    reason -- so the lock is what keeps two requests off it at once. Reads
    only, so a lock is enough and a pool would be a write path nobody wants.
    """
    repository = request.app.state.repository
    if not isinstance(repository, Repository):  # pragma: no cover - set by create_app
        raise HTTPException(status_code=503, detail="the store is not open")
    with request.app.state.lock:
        yield repository


Store = Annotated[Repository, Depends(store)]
"""The dependency every handler takes instead of reaching for app state."""


class Overview(BaseModel):
    """The four KPI cards and the status chips above them."""

    decisions: int
    assumptions: int
    estimates: int
    findings: int
    at_risk: int
    assumptions_valid: float
    assumption_health: dict[str, int]
    calibration_bias: Decimal | None
    calibration_n: int
    review_items: int
    calibration_groups: int
    schema_version: int
    provider: str
    generated_at: datetime


@router.get("/overview")
def overview(request: Request, repository: Store) -> Overview:
    """Everything the top of the dashboard shows, in one read."""
    counts = stats(repository.connection)
    health = dashboard.assumption_health(repository)
    live = sum(health.values())
    holding = health[AssumptionStatus.HOLDING] + health[AssumptionStatus.UNVERIFIED]
    factors = BiasDetective(repository).all_factors()
    speaking = [f for f in factors if f.speaks]
    # The loudest group is what the KPI card shows; a mean over refusals would
    # be a number with no evidence under it.
    loudest = max(speaking, key=lambda f: f.magnitude or Decimal(0), default=None)
    queue = dashboard.finding_queue(repository, undecided_only=True)
    return Overview(
        decisions=counts.records.get(RecordKind.DECISION, 0),
        assumptions=live,
        estimates=counts.records.get(RecordKind.ESTIMATE, 0),
        findings=counts.records.get(RecordKind.FINDING, 0),
        at_risk=sum(1 for s in dashboard.decision_index(repository) if s.at_risk),
        assumptions_valid=(holding / live) if live else 0.0,
        assumption_health={status.value: n for status, n in health.items()},
        calibration_bias=loudest.magnitude if loudest else None,
        calibration_n=loudest.n if loudest else 0,
        review_items=len(queue),
        calibration_groups=len(factors),
        schema_version=counts.schema_version,
        provider=request.app.state.provider,
        generated_at=datetime.now(UTC),
    )


@router.get("/decisions")
def decisions(repository: Store) -> list[views.DecisionSummaryOut]:
    """Every decision with the counts the list view shows."""
    return [views.decision_summary_out(s) for s in dashboard.decision_index(repository)]


@router.get("/decisions/{decision_id}")
def decision(repository: Store, decision_id: str) -> views.DecisionDetailOut:
    """One decision and the whole chain of argument under it."""
    detail = drilldown.decision_detail(repository, NodeId(decision_id))
    if detail is None:
        raise HTTPException(status_code=404, detail=f"no decision {decision_id}")
    return views.decision_detail_out(detail)


@router.get("/assumptions")
def assumptions(repository: Store) -> list[payloads.AssumptionOut]:
    """Every current assumption."""
    return [payloads.assumption_out(a) for a in repository.list_all(Assumption)]


@router.get("/estimates")
def estimates(repository: Store) -> list[payloads.EstimateOut]:
    """Every current estimate."""
    return [payloads.estimate_out(e) for e in repository.list_all(Estimate)]


@router.get("/estimates/{estimate_id}")
def estimate(repository: Store, estimate_id: str) -> views.EstimateDetailOut:
    """One estimate, its outcome, and everything that leaned on it."""
    detail = drilldown.estimate_detail(repository, NodeId(estimate_id))
    if detail is None:
        raise HTTPException(status_code=404, detail=f"no estimate {estimate_id}")
    return views.estimate_detail_out(detail)


class Calibration(BaseModel):
    """Per-group factors beside the rows they were computed from."""

    factors: list[payloads.FactorOut]
    history: list[payloads.CalibrationRowOut]
    minimum_sample: int


@router.get("/calibration")
def calibration(repository: Store) -> Calibration:
    """What each estimator's record says, refusals included.

    Refusals are returned rather than filtered: "this group is two outcomes
    short" is the honest answer and a chart that hid it would imply there was
    nothing to say.
    """
    return Calibration(
        factors=[payloads.factor_out(f) for f in BiasDetective(repository).all_factors()],
        history=[
            payloads.calibration_row_out(r) for r in calibration_history(repository.connection)
        ],
        minimum_sample=MINIMUM_SAMPLE,
    )


class Fusion(BaseModel):
    """The two directions Phase 8 argues in."""

    priced: list[payloads.FusionOut]
    flips: list[payloads.FusionOut]
    damages: list[str]


@router.get("/fusion")
def fusion(repository: Store) -> Fusion:
    """Every `estimated_as` edge priced against its estimator's record.

    Computed, never written -- the same two agents `praxis fuse --dry-run`
    runs, so the page and the CLI cannot disagree.
    """
    priced = FusionBridge(repository).price_all(now=datetime.now(UTC))
    return Fusion(
        priced=[payloads.fusion_out(p) for p in priced],
        flips=[payloads.fusion_out(p) for p in priced if p.flips],
        damages=[d.describe() for d in CollateralAgent(repository).survey() if d.damaging],
    )


@router.get("/queue")
def queue(
    repository: Store, undecided_only: Annotated[bool, Query()] = False
) -> list[views.QueueItemOut]:
    """Findings needing a person, most severe first."""
    items = dashboard.finding_queue(repository, undecided_only=undecided_only)
    return [views.queue_item_out(item) for item in items]


class TimelinePage(BaseModel):
    """One page of the audit trail."""

    events: list[payloads.AuditOut]
    next_before: int | None
    total: int


@router.get("/timeline")
def timeline(
    repository: Store,
    limit: Annotated[int, Query(ge=1, le=dashboard.MAX_PAGE)] = dashboard.DEFAULT_PAGE,
    before: Annotated[int | None, Query()] = None,
) -> TimelinePage:
    """The append-only trail, newest first."""
    page = dashboard.audit_timeline(repository, limit=limit, before=before)
    return TimelinePage(
        events=[payloads.audit_out(e) for e in page.events],
        next_before=page.next_before,
        total=page.total,
    )


class TraceOut(BaseModel):
    """One model call, as the reasoning panel shows it."""

    seq: int | None
    run_id: str
    agent: str
    task: str
    role: str
    provider: str
    model_id: str
    attempt: int
    outcome: str
    cost_usd: Decimal
    latency_ms: int
    occurred_at: datetime
    request_json: str
    response_text: str
    error: str | None


class TracePage(BaseModel):
    """One page of the trace store, with the runs to filter by."""

    traces: list[TraceOut]
    runs: list[str]
    next_before: int | None
    total: int


@router.get("/traces")
def trace_page(
    repository: Store,
    limit: Annotated[int, Query(ge=1, le=dashboard.MAX_PAGE)] = 25,
    before: Annotated[int | None, Query()] = None,
    agent: Annotated[str | None, Query()] = None,
    run_id: Annotated[str | None, Query()] = None,
) -> TracePage:
    """What every agent actually said. Phase 2's trace store, finally shown."""
    connection = repository.connection
    found = traces.trace_index(connection, limit=limit, before=before, agent=agent, run_id=run_id)
    rows = [
        TraceOut(
            seq=traces.sequence_of(connection, trace),
            run_id=trace.run_id,
            agent=trace.agent,
            task=trace.task,
            role=trace.role.value,
            provider=trace.provider,
            model_id=trace.model_id,
            attempt=trace.attempt,
            outcome=trace.outcome.value,
            cost_usd=trace.cost_usd,
            latency_ms=trace.latency_ms,
            occurred_at=trace.occurred_at,
            request_json=trace.request_json,
            response_text=trace.response_text,
            error=trace.error,
        )
        for trace in found
    ]
    return TracePage(
        traces=rows,
        runs=list(traces.runs(connection)),
        next_before=rows[-1].seq if rows else None,
        total=traces.trace_count(connection),
    )
