"""One monitoring pass over a store: read, evaluate, write only what changed.

The store-facing half, kept apart from `AssumptionMonitor` for the reason every
Half A agent is kept apart from its pipeline -- the agent decides, the pipeline
holds the store and the ids. It is also what makes the monitor testable without
SQLite, and what lets the same verdict be reached in a one-shot check and in a
scheduled re-run without two code paths.

**Writing only on a change is the whole of re-runnability.** The store already
holds the last verdict, so "has anything changed" is a comparison rather than a
bookkeeping problem. A second pass over an unchanged world writes no version, no
audit row and no duplicate finding, and costs no model calls beyond the single
event-matching question -- which itself is skipped when nothing is awaited.

Two writes happen and they happen in this order: the assumption's new version
first, then the breach finding that cites it. The finding names the assumption
as its subject and the store's foreign keys mean the subject has to exist, so
the order is required rather than tidy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from praxis.domain.enums import AssumptionStatus, RecordKind
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Decision, Finding
from praxis.llm.provider import LLMProvider
from praxis.monitor.breach import breach_finding
from praxis.monitor.facts import FactsFile, world_for
from praxis.monitor.monitor import AssumptionMonitor, Verdict
from praxis.obs.logging import get_logger
from praxis.predicates.world import WorldState
from praxis.store.repository import Repository

_log = get_logger(__name__)

MONITOR_ACTOR: Final = "AssumptionMonitor"
"""Who the audit trail records for a status change."""


@dataclass(frozen=True, slots=True)
class MonitoringRun:
    """What one pass concluded and what it wrote.

    Attributes:
        verdicts: One per assumption read, in store order, whether or not it
            changed. Reported in full because "nothing changed" is a result and
            a run that only listed its writes could not be distinguished from a
            run that never happened.
        findings: The breaches raised. One per assumption that *became*
            breached, never one per decision resting on it.
        revised: How many assumptions got a new version.
        calls: Model calls made -- at most one per run, for event matching.
    """

    verdicts: tuple[Verdict, ...] = ()
    findings: tuple[Finding, ...] = ()
    revised: int = 0
    calls: int = 0

    @property
    def breached(self) -> tuple[Verdict, ...]:
        """Assumptions whose predicate evaluated false."""
        return tuple(verdict for verdict in self.verdicts if verdict.breached)

    @property
    def aged(self) -> tuple[Verdict, ...]:
        """Assumptions that merely expired.

        Reported apart from `breached` because telling the two apart is the
        claim this phase is graded on.
        """
        return tuple(verdict for verdict in self.verdicts if verdict.aged)

    @property
    def holding(self) -> tuple[Verdict, ...]:
        """Assumptions whose predicate evaluated true."""
        return tuple(
            verdict for verdict in self.verdicts if verdict.status is AssumptionStatus.HOLDING
        )

    @property
    def unchecked(self) -> tuple[Verdict, ...]:
        """Assumptions the facts did not settle, and that had not expired.

        Neither holding nor breached: nothing measured them. The number a
        person reads to know how much of their memory is currently unverifiable.
        """
        return tuple(
            verdict
            for verdict in self.verdicts
            if not verdict.evaluation.decided and not verdict.aged
        )

    def counts(self) -> dict[AssumptionStatus, int]:
        """How many assumptions ended in each status."""
        counted = dict.fromkeys(AssumptionStatus, 0)
        for verdict in self.verdicts:
            counted[verdict.status] += 1
        return counted


def monitor_store(  # noqa: PLR0913 -- see the note under Args
    repository: Repository,
    *,
    provider: LLMProvider | None = None,
    supplied: FactsFile | None = None,
    world: WorldState | None = None,
    at: datetime | None = None,
    run_id: str | None = None,
) -> MonitoringRun:
    """Evaluate every assumption in a store and write what changed.

    Args:
        repository: The store to read and write.
        provider: The seam, for event matching only. Omitted entirely, every
            predicate is still evaluated and every breach still raised.
        supplied: A person's measurements and observations, layered over what
            the store itself asserts.
        world: A world to use instead of building one. For tests and for
            replaying a check against a state that was recorded elsewhere.
        at: When this ran. Defaults to now, in UTC. Timezone-aware, invariant 5.
        run_id: Recorded on every audit row this writes.

    Six arguments, and none of them collapses into another: a store, a seam,
    two different ways of saying what is true, a clock and a run id. Merging
    `supplied` into `world` would hide the layering `praxis.monitor.facts`
    depends on, and defaulting the clock inside would make a replay silently
    evaluate against today.

    Returns:
        Every verdict reached and everything written.

    Raises:
        ProviderError: for failures about the run rather than one record.
        StoreError: if a write is refused.
    """
    occurred_at = at if at is not None else datetime.now(UTC)
    assumptions = repository.list_all(Assumption)
    state = (
        world if world is not None else world_for(repository, now=occurred_at, supplied=supplied)
    )
    monitor = AssumptionMonitor(provider)
    state, calls = monitor.enrich(state, assumptions)

    verdicts: list[Verdict] = []
    findings: list[Finding] = []
    revised = 0
    for assumption in assumptions:
        verdict = monitor.check(assumption, state)
        verdicts.append(verdict)
        if not verdict.changed:
            continue
        revised += 1
        _record(repository, verdict, at=occurred_at, run_id=run_id)
        if verdict.breached:
            findings.append(_raise_breach(repository, verdict, at=occurred_at, run_id=run_id))
    _log.info(
        "monitoring_run",
        assumptions=len(assumptions),
        revised=revised,
        breaches=len(findings),
        calls=calls,
    )
    return MonitoringRun(
        verdicts=tuple(verdicts), findings=tuple(findings), revised=revised, calls=calls
    )


def decisions_resting_on(repository: Repository, assumption_id: str) -> tuple[Decision, ...]:
    """Every decision that reaches this assumption over `assumes`.

    A reverse walk rather than a stored list, because the edges are the record
    of what rests on what and a second copy would be a second thing to keep in
    step. Restricted to `assumes` so that an estimate's own dependents do not
    arrive here as decisions about this assumption.

    Args:
        repository: The store.
        assumption_id: The breached assumption.

    Returns:
        The decisions, in the order the walk reached them.
    """
    reached = repository.impacted_by(assumption_id, types=(LinkType.ASSUMES,))
    # Filtered by kind before the lookup: the walk returns node ids of every
    # kind it reached, and asking the store for a decision by an assumption's
    # id would be a lookup that always misses.
    found = (
        repository.get(Decision, node.id) for node in reached if node.kind is RecordKind.DECISION
    )
    return tuple(decision for decision in found if decision is not None)


def _record(repository: Repository, verdict: Verdict, *, at: datetime, run_id: str | None) -> None:
    """Write the assumption's new status as a new version.

    Append-only, invariant 7: the previous verdict stays readable, so "this was
    holding in March and breached in June" is a query rather than a memory.
    """
    repository.revise(
        verdict.assumption.model_copy(update={"status": verdict.status, "last_evaluated_at": at}),
        actor=MONITOR_ACTOR,
        reason=verdict.reason,
        at=at,
        run_id=run_id,
    )


def _raise_breach(
    repository: Repository, verdict: Verdict, *, at: datetime, run_id: str | None
) -> Finding:
    """Write the finding a violated predicate raises.

    The id comes from `next_id` immediately before the write rather than from a
    counter, which is the opposite of what `praxis.agents.extraction` needs and
    correct here for the same underlying reason: that pipeline builds three
    records before writing any, and this writes each finding before the next is
    built.
    """
    finding = breach_finding(
        verdict.assumption,
        verdict.evaluation,
        decisions_resting_on(repository, verdict.assumption.id),
        finding_id=repository.next_id(RecordKind.FINDING),
        at=at,
    )
    return repository.add(
        finding,
        actor=MONITOR_ACTOR,
        reason=f"{verdict.assumption.id} was breached: {verdict.reason}",
        at=at,
        run_id=run_id,
    )
