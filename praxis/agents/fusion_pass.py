"""Both fusion directions over a store, writing only what has changed.

The counterpart to `praxis.agents.calibration`, and the same shape for the same
reasons. This runs **per store** rather than per document: fusion is a question
about accumulated history and the graph over it, so there is nothing
per-document to iterate and no window to widen. A store that has ingested
nothing since the last run can still have a different answer here, because an
outcome may have landed.

**It costs zero model calls, and that is structural.** Neither `FusionBridge`
nor `CollateralAgent` imports a provider -- ADR 0026 and ADR 0027 -- so this
pass takes none either. `calls` is reported as zero rather than omitted, because
a cost column with a missing row reads as an unmeasured cost rather than an
absent one.

**Writing happens only on a change**, the rule `praxis.monitor.run` established
and `praxis.agents.calibration` repeated. The comparison is on the prosecution
text, which carries every number in the allegation, so any movement in the
numbers is a movement in the text and produces a new version, and no movement
produces nothing at all.

## Two directions, two finding kinds, one pass

They are written together because they are one question asked from two ends and
a person triaging wants both in one place. They stay separable in the result:

- **Forwards.** `FusionBridge` prices an assumption against its estimator's
  history. Where the calibrated number flips the predicate, that is a
  `STALE_DECISION` -- a projection, filed against the assumption.
- **Backwards.** `CollateralAgent` starts from an outcome that already missed.
  That is a `COLLATERAL_IMPACT` -- a measurement, filed against the estimate.

**The two kinds are deliberately not merged**, and the distinction is the one
ADR 0028 turns on: one is a thing that happened and one is a thing that is
likely, and a triage queue that cannot tell them apart would rank a projection
above a measurement. `FindingKind.ASSUMPTION_BREACH` is not used by the forward
direction for exactly that reason -- `praxis.monitor.breach` writes those from
facts a run was given, and a calibration flip is not a fact.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Final

from praxis.agents.collateral import CollateralAgent, CollateralDamage, collateral_finding
from praxis.agents.fusion import FusionBridge, PricedAssumption, flipped
from praxis.domain.enums import FindingKind, RecordKind
from praxis.domain.ids import FindingId
from praxis.domain.links import LinkType
from praxis.domain.records import Decision, Finding
from praxis.monitor.breach import severity_for
from praxis.obs.logging import get_logger
from praxis.store.repository import Repository

_log = get_logger(__name__)

FUSION_ACTOR: Final = "fusion"
"""The actor on the audit rows this pass writes.

The pass rather than the agent, as `praxis.agents.estimation` and
`praxis.agents.calibration` both do: the audit trail records who *wrote* a
record, and `created_by` on the finding records which component produced it.
Both questions have an answer and neither is inferred from the other.
"""

STALE_ACTOR: Final = "FusionBridge"
"""What `created_by` says on a forward finding."""

_UNSPENT: Final = "F-0001"
"""A well-formed finding id that is never stored.

Deciding whether to write means comparing this pass's prosecution against the
one already standing, and building a record to get that text needs *an* id.
Using a freshly allocated one would spend an ordinal on every finding on
every run, including the runs that write nothing.

It is a *valid* id rather than a distinctive one because `Finding` refuses
anything else, and it does not need to be distinctive: only `prosecution`
and `severity` are ever read off a record built with it, and nothing built
with it reaches `add`. The write branch builds a second time with a real id.
"""


@dataclass(frozen=True, slots=True)
class _Pending:
    """A finding this pass would write, before an id has been spent on it.

    Ids are allocated at **write** time and not here, which is not a detail.
    `Repository.next_id` reads the highest ordinal in the store, so building two
    findings before writing either one hands both the same id -- the exact
    failure `praxis.agents.extraction.StoreAllocator` exists to describe. Since
    most passes over a settled store write nothing at all, deferring is also the
    difference between spending an id per finding per run and spending none.

    Attributes:
        kind: Which allegation this is, half of the key an existing finding is
            matched on.
        subject_id: The node it is filed against, the other half.
        build: Makes the record, given the id finally allocated for it.
    """

    kind: FindingKind
    subject_id: str
    build: Callable[[str], Finding]


@dataclass(frozen=True, slots=True)
class FusionRun:
    """Everything one fusion pass over a store did.

    Attributes:
        priced: Every `estimated_as` edge walked, refusals included and in read
            order. The refusals are most of it and are not filtered: which
            groups are one outcome short of speaking is a fact a person can act
            on, and a result showing only findings could not report it.
        damages: Every missed estimate surveyed, harmless ones included.
        raised: Findings written this pass, at version 1.
        revised: Findings whose numbers moved, written as a new version.
        unchanged: Findings that already stood and said the same thing. A
            second pass over an unchanged store is all of these and nothing else.
        calls: Model calls made. Always zero, and reported rather than omitted.
    """

    priced: tuple[PricedAssumption, ...] = ()
    damages: tuple[CollateralDamage, ...] = ()
    raised: tuple[Finding, ...] = ()
    revised: tuple[Finding, ...] = ()
    unchanged: int = 0
    calls: int = 0

    @property
    def flips(self) -> tuple[PricedAssumption, ...]:
        """The priced edges where calibration changed the predicate's verdict."""
        return flipped(self.priced)

    @property
    def damaging(self) -> tuple[CollateralDamage, ...]:
        """The misses that something actually rested on."""
        return tuple(damage for damage in self.damages if damage.damaging)

    @property
    def written(self) -> int:
        """Findings this pass put in the store, new and revised together."""
        return len(self.raised) + len(self.revised)


def fuse_store(
    repository: Repository, *, at: datetime | None = None, run_id: str | None = None
) -> FusionRun:
    """Run both directions over a store and write what has changed.

    Args:
        repository: An open, migrated store.
        at: When this ran. Defaults to now, in UTC. Timezone-aware, invariant 5.
        run_id: Recorded on every audit row this writes.

    Returns:
        What was found in both directions, and what was written.
    """
    moment = at or datetime.now(UTC)
    priced = FusionBridge(repository).price_all(now=moment)
    damages = CollateralAgent(repository).survey()

    raised: list[Finding] = []
    revised: list[Finding] = []
    unchanged = 0
    for pending in _pending(repository, priced, damages, at=moment):
        written = _record(repository, pending, at=moment, run_id=run_id)
        if written is None:
            unchanged += 1
        elif written.version == 1:
            raised.append(written)
        else:
            revised.append(written)

    _log.info(
        "fusion_pass",
        priced=len(priced),
        flips=len(flipped(priced)),
        misses=len(damages),
        damaging=sum(1 for damage in damages if damage.damaging),
        raised=len(raised),
        revised=len(revised),
        unchanged=unchanged,
    )
    return FusionRun(
        priced=priced,
        damages=damages,
        raised=tuple(raised),
        revised=tuple(revised),
        unchanged=unchanged,
    )


def stale_finding(
    result: PricedAssumption, decisions: Sequence[Decision], *, finding_id: str, at: datetime
) -> Finding:
    """Build the finding a calibrated flip raises against an assumption.

    `STALE_DECISION` and not `ASSUMPTION_BREACH`. The difference is whether
    anything was measured: a breach is a predicate evaluated false against facts
    a run was given, and this is a predicate evaluated false against a *number
    nobody has observed yet*, projected from the estimator's track record. Both
    deserve attention and they do not deserve the same attention, so they are
    different kinds rather than one kind with a note in the prose. See ADR 0028.

    Args:
        result: A priced edge whose verdict is `FLIPPED`.
        decisions: What rests on the assumption, from a reverse walk over
            `assumes`. Decides the severity and is named in the prosecution.
        finding_id: Allocated by the caller, which holds the store.
        at: When this was detected. Timezone-aware, invariant 5.

    Returns:
        The finding, at version 1 and undecided.

    Raises:
        ValueError: if the result is not a flip. A finding raised from a
            correction that changed no verdict is the noise this whole
            component is arranged to avoid emitting.
    """
    if not result.flips:
        message = (
            f"a stale-decision finding needs a flipped verdict, got "
            f"{result.verdict.value} for {result.assumption.id}"
        )
        raise ValueError(message)
    return Finding(
        id=FindingId(finding_id),
        kind=FindingKind.STALE_DECISION,
        subject_kind=RecordKind.ASSUMPTION,
        subject_id=result.assumption.id,
        prosecution=stale_prosecution(result, decisions),
        severity=severity_for(decisions),
        confidence=result.assumption.confidence,
        # Empty: computed from the store and a factor, not quoted. The
        # assumption's own span is deliberately not cited -- it evidences what
        # was assumed, and the allegation is about what the history implies.
        evidence_span_ids=(),
        detected_at=at,
        created_by=STALE_ACTOR,
        created_at=at,
    )


def stale_prosecution(result: PricedAssumption, decisions: Sequence[Decision]) -> str:
    """The case against the assumption, stated so it can be argued with.

    Leads with the fact that this is a projection rather than a measurement,
    because a reader who takes it for an observed breach will over-react to it.
    Carries the factor's own sentence, `n` and confidence included, so the
    strength of the evidence arrives with the allegation instead of behind it.
    """
    resting = ", ".join(decision.id for decision in decisions) or "no recorded decision"
    factor = result.factor.describe() if result.factor is not None else "no factor"
    return (
        f'Nothing has been measured yet. "{result.assumption.statement}" compiles to '
        f"`{result.assumption.predicate}`, which the raw estimate of {result.raw} satisfies. "
        f"But {factor}, so the same estimate calibrated is {result.calibrated} "
        f"(ordinarily {result.high} to {result.low}), and that violates the predicate. "
        f"Resting on it: {resting}. Re-examine, or record why the history does not apply."
    )


def _pending(
    repository: Repository,
    priced: Sequence[PricedAssumption],
    damages: Sequence[CollateralDamage],
    *,
    at: datetime,
) -> list[_Pending]:
    """Every finding this pass might write, in a fixed order: forwards, then back.

    Fixed so that two runs over one store consider the same facts in the same
    sequence and therefore allocate the same ids to the same allegations.
    """
    pending = [
        _Pending(
            FindingKind.STALE_DECISION,
            result.assumption.id,
            partial(_stale, result, _resting_on(repository, result), at),
        )
        for result in flipped(priced)
    ]
    pending.extend(
        _Pending(
            FindingKind.COLLATERAL_IMPACT,
            damage.estimate.id,
            partial(_collateral, damage, at),
        )
        for damage in damages
        # A miss nothing rests on is a survey row, not an allegation. It stays
        # in `FusionRun.damages` so "how often is a miss harmless" is
        # answerable, and it raises no finding for a person to triage.
        if damage.damaging
    )
    return pending


def _stale(
    result: PricedAssumption, decisions: Sequence[Decision], at: datetime, finding_id: str
) -> Finding:
    """`stale_finding` with the id last, so it can be bound by `partial`."""
    return stale_finding(result, decisions, finding_id=finding_id, at=at)


def _collateral(damage: CollateralDamage, at: datetime, finding_id: str) -> Finding:
    """`collateral_finding` with the id last, so it can be bound by `partial`."""
    return collateral_finding(damage, finding_id=finding_id, at=at)


def _resting_on(repository: Repository, result: PricedAssumption) -> tuple[Decision, ...]:
    """The decisions that rest on this assumption, nearest first.

    A reverse walk over `assumes` only, rather than `impacted_by`: the question
    is what rests on *the assumption*, and widening it to the whole impact DAG
    would pull in decisions that reached the estimate by another route and never
    made this assumption at all.
    """
    edges = repository.links_to(result.assumption.id, types=(LinkType.ASSUMES,))
    found = (repository.get(Decision, edge.source_id) for edge in edges)
    return tuple(record for record in found if record is not None and not record.retracted)


def _record(
    repository: Repository, pending: _Pending, *, at: datetime, run_id: str | None
) -> Finding | None:
    """Write, revise, or leave alone the finding for one subject.

    Returns:
        What was written, or `None` when what already stands says exactly this.
    """
    standing = _standing(repository, pending.kind, pending.subject_id)
    # Built against a throwaway id purely to compare the prosecution text. The
    # id is spent only on the branch that actually writes, so a pass over a
    # settled store consumes none.
    proposed = pending.build(_UNSPENT)
    if standing is not None and standing.prosecution == proposed.prosecution:
        return None
    if standing is None:
        built = pending.build(repository.next_id(RecordKind.FINDING))
        written = repository.add(
            built,
            actor=FUSION_ACTOR,
            reason=f"{built.kind.value} raised against {pending.subject_id}",
            at=at,
            run_id=run_id,
        )
        _link(repository, written, pending.subject_id, at=at, run_id=run_id)
        return written
    return repository.revise(
        standing.model_copy(
            update={
                "prosecution": proposed.prosecution,
                "severity": proposed.severity,
                "detected_at": at,
            }
        ),
        actor=FUSION_ACTOR,
        reason=f"{pending.kind.value} restated for {pending.subject_id}",
        at=at,
        run_id=run_id,
    )


def _link(
    repository: Repository, finding: Finding, subject_id: str, *, at: datetime, run_id: str | None
) -> None:
    """Record that a collateral finding exists only because of a record's failure.

    `LinkType.COLLATERAL_OF` has existed since Phase 1 with nothing writing it,
    and it says exactly this. Written only for the backward direction: a forward
    finding is a projection about the subject it is already filed against, so an
    edge from it to that same subject would assert nothing the finding does not.
    """
    if finding.kind is not FindingKind.COLLATERAL_IMPACT:
        return
    from praxis.domain.records import Link  # noqa: PLC0415 -- import cycle at module scope

    repository.add(
        Link.between(
            LinkType.COLLATERAL_OF,
            finding.id,
            subject_id,
            rationale=(
                f"{subject_id} was estimated and missed; this finding names what rested on it"
            ),
            # Certain by construction: the edge records that this pass wrote
            # this finding for this record, which is not an inference about the
            # world and has nothing to be uncertain about.
            confidence=1.0,
            created_by=FUSION_ACTOR,
            created_at=at,
        ),
        actor=FUSION_ACTOR,
        reason=f"{finding.id} exists because {subject_id} missed",
        at=at,
        run_id=run_id,
    )


def _standing(repository: Repository, kind: FindingKind, subject_id: str) -> Finding | None:
    """The finding of this kind already filed against this node, if any.

    Keyed on kind *and* subject: an assumption can carry a stale-decision
    finding while its estimate carries a collateral one, and matching on the
    subject alone would let one overwrite the other.
    """
    return next(
        (
            finding
            for finding in repository.list_all(Finding)
            if finding.kind is kind and finding.subject_id == subject_id
        ),
        None,
    )
