"""The fusion mechanism run backwards: an estimate missed, so what rested on it?

`FusionBridge` goes forwards -- a decision's assumption is secretly an estimate,
so ask what this estimator's history says about it. This is the other direction
`ARCHITECTURE.md` names, and it starts from a fact rather than from a
projection: **an outcome has already landed and it missed badly.** The question
is which decisions were justified by the number that turned out to be wrong.

## Almost none of this is new, and that is the point

Phase 1 built the walk and documented it as this query. `DEPENDENCY_LINK_TYPES`
holds the three edges that form the impact DAG, and they were made to point the
same way -- dependent to depended-upon -- specifically so that *"an estimate
missed, what rests on it?"* is reverse reachability and not a special case per
hop:

    Decision --assumes--> Assumption --estimated_as--> Estimate
    Decision --justified_by--> Estimate

`Repository.impacted_by` walks exactly that, transitively, with a depth on every
node, and its own docstring calls itself "the fusion query". So this module does
not implement a traversal. It picks the outcomes worth asking about, asks, keeps
the decisions, and writes down what it found.

`FindingKind.COLLATERAL_IMPACT` and `LinkType.COLLATERAL_OF` have both existed
since Phase 1 with nothing writing either. This is what writes them.

## Which misses count is arithmetic, and it was already decided

`MatchQuality.MISS` is the threshold, and no second one is invented here.
ADR 0021 made match quality arithmetic over two numbers rather than a
judgement, Phase 6 computes it at write time, and a component that re-decided
"but how badly is badly" would be a second opinion about a settled number --
the same override ADR 0024 refused for the calibration threshold, arriving
through a different door.

## One finding per estimate, not one per damaged decision

`praxis.monitor.breach` made this choice for a breached assumption and the
argument transfers unchanged: writing one finding per affected decision makes
the count of findings a function of how many decisions happened to cite the
record, so a report counting collateral impacts would be counting citations. The
finding is filed against the **estimate that missed**, which is the one node the
whole allegation is about, and the decisions are named in the prosecution
because they are what a person is being asked to go and re-read.

Severity is `praxis.monitor.breach.severity_for`, imported rather than
reimplemented. The worst impact among the decisions resting on the record,
promoted to critical when a high-impact decision also binds the whole
organisation, is the same question here as there, and two copies of it would be
two triage queues that disagree.

**No model call.** A graph walk, a quality enum already computed, a ratio and a
template. See ADR 0027.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from praxis.agents.distribution import ratio_of
from praxis.domain.enums import FindingKind, MatchQuality, RecordKind, Severity
from praxis.domain.ids import FindingId
from praxis.domain.records import Assumption, Decision, Estimate, Finding, Outcome
from praxis.monitor.breach import severity_for
from praxis.obs.logging import get_logger
from praxis.store.graph import ReachedNode
from praxis.store.repository import Repository

_log = get_logger(__name__)

COLLATERAL_NAME: Final = "CollateralAgent"
"""Spelled as `praxis.config.models.NON_LLM_AGENTS` spells it, so the set that
refuses to route this agent and the agent itself cannot drift under a rename."""

BADLY_MISSED: Final[frozenset[MatchQuality]] = frozenset({MatchQuality.MISS})
"""Which qualities are worth surveying for collateral damage.

A set of one, and held here rather than written into an `if`, so that widening
it to include `PARTIAL` later is one edit in a named place rather than a
condition somebody has to find. `UNRESOLVED` is deliberately not in it: an
estimate nothing ever answered has not been shown to be wrong, and raising
collateral findings from silence would fill the queue with rows no evidence
supports.
"""


@dataclass(frozen=True, slots=True)
class CollateralDamage:
    """One missed estimate and everything the graph says rested on it.

    Attributes:
        estimate: The prediction that turned out wrong.
        outcome: What actually happened, and the row that says it was a miss.
        decisions: Every decision reverse-reachable over the impact DAG,
            nearest first. Possibly empty -- an estimate nothing rests on can
            still miss, and that is worth recording and not worth waking
            anybody for.
        assumptions: The assumptions on the path, for a person who wants to see
            how a decision reached this estimate rather than being told that it
            did.
        overrun: `actual / estimated` on active quantity, or `None` when a zero
            on either side means no ratio exists. Reported rather than
            invented.
    """

    estimate: Estimate
    outcome: Outcome
    decisions: tuple[Decision, ...]
    assumptions: tuple[Assumption, ...]
    overrun: Decimal | None

    @property
    def severity(self) -> Severity:
        """How urgently this needs a person. Arithmetic over the decisions."""
        return severity_for(self.decisions)

    @property
    def damaging(self) -> bool:
        """Whether anything actually rested on the estimate that missed."""
        return bool(self.decisions)

    def describe(self) -> str:
        """One line, for a report or a log."""
        if not self.damaging:
            return f"{self.estimate.id} missed and nothing recorded rests on it"
        names = ", ".join(decision.id for decision in self.decisions)
        return (
            f"{self.estimate.id} missed and {len(self.decisions)} decision(s) rest on it: {names}"
        )


class CollateralAgent:
    """Given an estimate that missed, the decisions that leaned on it.

    Deterministic: the traversal is `Repository.impacted_by`, the threshold is a
    stored `MatchQuality`, and the severity is `severity_for`. See ADR 0027.
    """

    name: Final = COLLATERAL_NAME

    def __init__(self, repository: Repository) -> None:
        """Bind the agent to a store.

        Args:
            repository: Holds the outcomes, the impact DAG and the decisions.
        """
        self._repository = repository

    def damage_from(self, outcome: Outcome) -> CollateralDamage | None:
        """What one outcome damaged, or `None` if it damaged nothing.

        Args:
            outcome: The row to survey. Ignored unless its quality is a miss.

        Returns:
            The damage, or `None` when this outcome is not a miss or its
            estimate is no longer standing. `None` means "no question to ask
            here", never "the question failed".
        """
        if outcome.match_quality not in BADLY_MISSED:
            return None
        estimate = self._repository.get(Estimate, outcome.estimate_id)
        # Retracted as well as absent, the asymmetry Phase 8 found in `get`:
        # retraction is a statement about a record rather than its
        # disappearance, so a check for `None` alone would raise collateral
        # findings from a withdrawn estimate.
        if estimate is None or estimate.retracted:
            return None
        reached = self._repository.impacted_by(estimate.id)
        return CollateralDamage(
            estimate=estimate,
            outcome=outcome,
            decisions=self._records(Decision, reached),
            assumptions=self._records(Assumption, reached),
            overrun=_overrun(estimate, outcome),
        )

    def survey(self) -> tuple[CollateralDamage, ...]:
        """Every missed estimate in the store, and what rested on each.

        Returns:
            One entry per miss, in store order. Entries with no decisions are
            included: "this missed and nothing rested on it" is a fact, and a
            survey that showed only the damaging ones could not be used to say
            how often a miss is harmless.
        """
        damages = [
            damage
            for outcome in self._repository.list_all(Outcome)
            if (damage := self.damage_from(outcome)) is not None
        ]
        _log.info(
            "collateral_survey",
            agent=self.name,
            misses=len(damages),
            damaging=sum(1 for damage in damages if damage.damaging),
        )
        return tuple(damages)

    def _records[R: Decision | Assumption](
        self, record_type: type[R], reached: Sequence[ReachedNode]
    ) -> tuple[R, ...]:
        """The reached nodes of one kind, resolved to records, nearest first.

        Nearest first because `impacted_by` carries the shortest distance and a
        decision one hop away justified the estimate directly, while one two
        hops away reached it through an assumption. That is the order a person
        would want to read them in.

        A retracted record is dropped: the walk is over edges, and an edge to a
        withdrawn decision still exists. Naming one in a prosecution would send
        somebody to re-read a decision that has already been taken back.
        """
        wanted = record_type.record_kind
        found = (
            self._repository.get(record_type, node.id) for node in reached if node.kind is wanted
        )
        return tuple(record for record in found if record is not None and not record.retracted)


def prosecution_for(damage: CollateralDamage) -> str:
    """The case against the estimate, stated so it can be argued with.

    Names the two quantities, the ratio between them where one exists, and every
    decision resting on the estimate -- because those are what a person is being
    asked to go and re-read, and a finding that made them look those up is a
    finding nobody follows up.

    Args:
        damage: The survey result.

    Returns:
        One paragraph, in the shape `Finding.prosecution` documents.
    """
    resting = ", ".join(decision.id for decision in damage.decisions) or "no recorded decision"
    ratio = (
        f" -- {damage.overrun}x the estimate"
        if damage.overrun is not None
        else " -- a zero on one side, so no ratio exists"
    )
    actual = damage.outcome.active_quantity
    return (
        f'"{damage.estimate.subject}" was estimated at {damage.estimate.active_quantity} '
        f"{damage.estimate.unit.value} of hands-on effort and actually took {actual}"
        f"{ratio}, which the store already graded {damage.outcome.match_quality.value}. "
        f"Resting on it: {resting}."
    )


def collateral_finding(damage: CollateralDamage, *, finding_id: str, at: datetime) -> Finding:
    """Build the finding a missed estimate raises against what rested on it.

    Args:
        damage: The survey result. Must carry a miss, which `damage_from`
            guarantees by construction.
        finding_id: Allocated by the caller, which holds the store.
        at: When this was detected. Timezone-aware, invariant 5.

    Returns:
        The finding, at version 1 and undecided -- `ChallengerAgent` is what
        turns a prosecution into a verdict and it arrives in a later phase. A
        finding written already decided would claim a review that never happened.

    Raises:
        ValueError: if the outcome is not a miss. The caller has already made
            that distinction, and this refuses it a second time because a
            collateral finding raised from a `CLOSE` outcome is an allegation
            with no evidence under it.
    """
    if damage.outcome.match_quality not in BADLY_MISSED:
        message = (
            f"collateral damage needs a missed outcome, got "
            f"{damage.outcome.match_quality.value} for {damage.estimate.id}"
        )
        raise ValueError(message)
    return Finding(
        id=FindingId(finding_id),
        kind=FindingKind.COLLATERAL_IMPACT,
        subject_kind=RecordKind.ESTIMATE,
        subject_id=damage.estimate.id,
        prosecution=prosecution_for(damage),
        severity=damage.severity,
        confidence=damage.estimate.confidence,
        # Empty rather than the estimate's span: this finding is computed from
        # two stored records and the graph between them, not quoted from a
        # document. `Finding.evidence_span_ids` documents exactly this case.
        evidence_span_ids=(),
        detected_at=at,
        created_by=COLLATERAL_NAME,
        created_at=at,
    )


def _overrun(estimate: Estimate, outcome: Outcome) -> Decimal | None:
    """How much longer the work really took, or `None` where no ratio exists."""
    if outcome.active_quantity is None:
        return None
    return ratio_of(estimate.active_quantity, outcome.active_quantity)
