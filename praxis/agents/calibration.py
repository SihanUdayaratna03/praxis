"""Calibration over a store: read every group, grade it, and write what stands.

The counterpart to `praxis.agents.estimation`, and deliberately a different
shape. Half A and Half B both run **per document**: a pass reads spans, asks a
model about them, and writes records. This runs **per store**. Calibration is
computed over accumulated history rather than over one document, so there is
nothing per-document to iterate, no window to widen, and no reason for this to
sit inside ingestion. A store that has ingested nothing new since the last run
can still have a different answer here, because an outcome may have landed.

**It costs zero model calls, and that is structural rather than incidental.**
None of `BiasDetective`, `CalibratorAgent` or `ScoringAgent` imports a provider,
so this pass takes none either. `calls` is reported as zero rather than omitted,
because a cost column with a missing row reads as an unmeasured cost.

**Writing happens only on a change**, the rule `praxis.monitor.run` established.
A second pass over an unchanged store writes no version and no audit row. The
comparison is on the prosecution text, which carries the factor, the band, `n`
and the confidence -- so any movement in the numbers is a movement in the text
and produces a new version, and no movement produces nothing.

## Whether `Finding` can carry a calibration finding

Checked against `praxis/domain/records.py` and `001_core.sql` before any
migration was considered, the way Phase 6 checked `Estimate` and `Outcome`. The
answer is **no migration, and `Finding` is not the carrier of the factor** --
two separate conclusions, and the second is the interesting one.

`Finding.subject_id` is a `NodeRef`: one graph node. A calibration factor is a
property of an `(owner, work_class)` **group**, and no such node exists. The
nearest thing available is one estimate from the group, so a finding here is
written against an **anchor** rather than a subject: the group's lowest estimate
id, which is stable as the group grows -- the most recent one would move every
time an estimate was written and turn one continuing allegation into a trail of
findings about different rows. The prosecution names the group in its first
clause, so nobody reads the anchor as "this estimate is the biased one".

`FindingKind.CALIBRATION_BIAS` has existed since Phase 1 and `evidence_span_ids`
is already documented as empty for exactly this case, so the record does hold
the *allegation* well. What it cannot hold is the *query*: `prosecution` is
prose, and Phase 8 asking "the factor, `n` and the confidence, in one call"
against a text column would be a parse. So the finding is what a person reads,
and `BiasDetective` recomputing from `calibration_history` is what
`FusionBridge` calls. That split has a second benefit worth stating: a stored
factor is stale the moment an outcome lands, and a stale calibration factor is
precisely the failure this product exists to catch.

**Only a group with a direction raises one.** An estimator inside
`NEUTRAL_BAND` is calibrated, and "you are correctly calibrated" is a result but
not an allegation. A finding whose prosecution is "nothing is wrong" would sit
in the same queue as a breach and teach a reader to skim the queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from praxis.agents.bias import (
    BiasDetective,
    BiasDirection,
    CalibrationFactor,
)
from praxis.agents.scoring import Backtest, ScoringAgent, total
from praxis.domain.enums import FindingKind, RecordKind, Severity
from praxis.domain.ids import FindingId
from praxis.domain.records import Estimate, Finding
from praxis.obs.logging import get_logger
from praxis.store.repository import Repository

_log = get_logger(__name__)

CALIBRATION_ACTOR: Final = "calibration"
"""The actor on the audit rows this pass writes.

The pass rather than the agent, as `praxis.agents.estimation` does: the audit
trail records who *wrote* a record, and `created_by` on the finding records
which component produced it. Both questions have an answer and neither is
inferred from the other.
"""

DETECTIVE_ACTOR: Final = "BiasDetective"
"""What `created_by` says on a calibration finding."""

HIGH_ABOVE: Final = Decimal(2)
CRITICAL_ABOVE: Final = Decimal(4)
"""How far from calibrated a factor has to be before a person is chased.

On `magnitude`, so the bands are symmetric between over- and under-estimation --
half as long as predicted is as wrong as twice as long, and reading them
asymmetrically is how an over-estimator stops being reported. Below `HIGH_ABOVE`
a finding is `medium`: it cleared `NEUTRAL_BAND`, so it is real, and a team
routinely 1.5x out is worth knowing about and is not an emergency.
"""


@dataclass(frozen=True, slots=True)
class CalibrationRun:
    """Everything one calibration pass over a store did.

    Attributes:
        factors: One per group, refusals included and in read order. The
            refusals are most of it and are not filtered: which classes are one
            outcome short is the finding a person can act on.
        backtests: One per group, ungraded ones included for the same reason.
        raised: Findings written this pass, at version 1.
        revised: Findings whose numbers moved, written as a new version.
        unchanged: Findings that already stood and said the same thing. A second
            pass over an unchanged store is all of these and nothing else.
        calls: Model calls made. Always zero, and reported rather than omitted.
    """

    factors: tuple[CalibrationFactor, ...] = ()
    backtests: tuple[Backtest, ...] = ()
    raised: tuple[Finding, ...] = ()
    revised: tuple[Finding, ...] = ()
    unchanged: int = 0
    calls: int = 0

    @property
    def measured(self) -> tuple[CalibrationFactor, ...]:
        """The groups with enough history to speak."""
        return tuple(factor for factor in self.factors if factor.speaks)

    @property
    def overall(self) -> Backtest:
        """Every group's grade added into one row."""
        return total(self.backtests)


def calibrate_store(
    repository: Repository, *, at: datetime | None = None, run_id: str | None = None
) -> CalibrationRun:
    """Compute every group's factor, backtest it, and write what has changed.

    Args:
        repository: An open, migrated store.
        at: When this ran. Defaults to now, in UTC.
        run_id: Recorded on every audit row this writes.

    Returns:
        What was found and what was written.
    """
    moment = at or datetime.now(UTC)
    factors = BiasDetective(repository).all_factors()
    backtests = ScoringAgent(repository).backtest()

    raised: list[Finding] = []
    revised: list[Finding] = []
    unchanged = 0
    for factor in factors:
        if not _worth_raising(factor):
            continue
        written = _record(repository, factor, at=moment, run_id=run_id)
        if written is None:
            # Nothing to write: what stands already says exactly this. The
            # second pass over an unchanged store is entirely this branch.
            unchanged += 1
        elif written.version == 1:
            raised.append(written)
        else:
            revised.append(written)

    _log.info(
        "calibration_pass",
        groups=len(factors),
        measured=sum(1 for factor in factors if factor.speaks),
        raised=len(raised),
        revised=len(revised),
        unchanged=unchanged,
    )
    return CalibrationRun(
        factors=factors,
        backtests=backtests,
        raised=tuple(raised),
        revised=tuple(revised),
        unchanged=unchanged,
    )


def bias_finding(
    factor: CalibrationFactor, anchor: Estimate, *, finding_id: str, at: datetime
) -> Finding:
    """Build the finding a measured bias raises.

    Args:
        factor: A verdict that speaks and carries a direction.
        anchor: The group's earliest estimate. An anchor, not a subject -- see
            the module docstring.
        finding_id: Allocated by the caller, which holds the store.
        at: When it was detected. Timezone-aware, invariant 5.

    Returns:
        The finding, at version 1 and undecided. `ChallengerAgent` is what turns
        a prosecution into a verdict and it arrives in a later phase.

    Raises:
        ValueError: if the verdict does not speak or has no direction. A finding
            alleging nothing is the one output this pass must not produce.
    """
    if not _worth_raising(factor):
        message = f"{factor.group.work_class}: nothing to allege"
        raise ValueError(message)
    confidence = factor.confidence
    if confidence is None:  # pragma: no cover -- speaking implies a confidence
        message = "a speaking verdict carries a confidence"
        raise AssertionError(message)
    return Finding(
        id=FindingId(finding_id),
        kind=FindingKind.CALIBRATION_BIAS,
        subject_kind=RecordKind.ESTIMATE,
        subject_id=anchor.id,
        prosecution=prosecution_for(factor),
        severity=severity_for(factor),
        confidence=float(confidence),
        # Empty, and `Finding.evidence_span_ids` says why in the record itself:
        # a calibration finding is computed from the store rather than quoted
        # from a document. There is no passage that says this.
        evidence_span_ids=(),
        detected_at=at,
        created_by=DETECTIVE_ACTOR,
        created_at=at,
    )


def prosecution_for(factor: CalibrationFactor) -> str:
    """The case against a group, stated so it can be argued with.

    Names the group first, so the anchor estimate is never read as the subject.
    Carries every number the verdict rests on, which is also what makes the
    write-on-change comparison exact: if any of them moves, this text moves.
    """
    return (
        f"{factor.group.owner} estimating {factor.group.work_class} work: "
        f"{factor.describe()}. "
        f"Ordinarily between {factor.low}x and {factor.high}x of what is predicted, "
        f"over {factor.n} resolved of {factor.considered} estimates. "
        f"This is an allegation about the group, not about the estimate it is filed "
        f"against -- calibration has no record of its own to point at."
    )


def severity_for(factor: CalibrationFactor) -> Severity:
    """How hard a bias of this size should push for attention."""
    magnitude = factor.magnitude
    if magnitude is None:  # pragma: no cover -- only called for a speaking verdict
        return Severity.LOW
    if magnitude >= CRITICAL_ABOVE:
        return Severity.CRITICAL
    if magnitude >= HIGH_ABOVE:
        return Severity.HIGH
    return Severity.MEDIUM


def _worth_raising(factor: CalibrationFactor) -> bool:
    """Whether there is an allegation here at all.

    A calibrated estimator is a result and not a charge. Filing "nothing is
    wrong" into the same queue as a breach is how a queue stops being read.
    """
    return factor.speaks and factor.direction is not BiasDirection.NONE


def _record(
    repository: Repository, factor: CalibrationFactor, *, at: datetime, run_id: str | None
) -> Finding | None:
    """Write or revise the finding for one group, or leave it alone.

    The caller tells a new finding from a revised one by its `version`, which is
    the store's own answer rather than a second flag that could disagree with it.

    Returns:
        What was written, or `None` when nothing was -- because what stands
        already says exactly this.
    """
    anchor = _anchor(repository, factor)
    if anchor is None:  # pragma: no cover -- a speaking group has estimates by construction
        return None

    standing = _standing(repository, anchor)
    prosecution = prosecution_for(factor)
    if standing is not None and standing.prosecution == prosecution:
        return None

    if standing is None:
        return repository.add(
            bias_finding(factor, anchor, finding_id=repository.next_id(RecordKind.FINDING), at=at),
            actor=CALIBRATION_ACTOR,
            reason=f"calibration bias measured for {factor.group.work_class}",
            at=at,
            run_id=run_id,
        )

    return repository.revise(
        standing.model_copy(
            update={
                "prosecution": prosecution,
                "severity": severity_for(factor),
                "confidence": float(factor.confidence or 0),
                "detected_at": at,
            }
        ),
        actor=CALIBRATION_ACTOR,
        reason=f"calibration factor moved for {factor.group.work_class}",
        at=at,
        run_id=run_id,
    )


def _standing(repository: Repository, anchor: Estimate) -> Finding | None:
    """The calibration finding already filed against this group, if any.

    Found by anchor rather than by group, because the anchor is the only thing
    the two have in common that the store holds -- and it is stable, so a group
    that gains estimates keeps the one finding it already has instead of
    starting a second.
    """
    return next(
        (
            finding
            for finding in repository.list_all(Finding)
            if finding.kind is FindingKind.CALIBRATION_BIAS and finding.subject_id == anchor.id
        ),
        None,
    )


def _anchor(repository: Repository, factor: CalibrationFactor) -> Estimate | None:
    """The group's earliest estimate, which is what a finding is filed against.

    Earliest and not latest, deliberately. Ids are allocated in order, so the
    lowest one in a group never changes as the group grows -- while the most
    recent would move on every write and turn one continuing allegation into a
    trail of findings about different rows, none of which is really the subject.
    """
    owner, work_class = factor.group.owner, factor.group.work_class
    matching = [
        estimate
        for estimate in repository.list_all(Estimate)
        if estimate.owner == owner and estimate.work_class == work_class
    ]
    return min(matching, key=lambda estimate: estimate.id) if matching else None
