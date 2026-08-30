"""Grading Phase 7: does the threshold hold, does the pass-through fire, is the
backtest sane.

The fourth quarter of the metrics table, and the one whose headline number is
mostly a **zero that is correct**. Against the corpus -- and against this
project's own history -- no group reaches five resolved estimates, so nothing is
measured, nothing is corrected, and nothing is backtested. That is what an
honest calibration system does with a small corpus, and the row exists so the
zero is *reported with its cause* rather than left for a reader to interpret.

**Every number is read back out of SQLite**, the rule Phase 4 set and Phase 5
and 6 kept. The factors are recomputed from `calibration_history` rather than
taken from the pass's own result, and the findings are counted in the store
rather than in the run. A run reports what the agents produced; the store is
what survived being written.

Three claims are graded, one per scope item.

**The threshold holds.** Recomputed over every group in the store: no group with
fewer than `MINIMUM_SAMPLE` usable ratios carries a factor. `hypothesis` proves
this over generated distributions in `tests/agents/test_bias.py`; this asserts
it over whatever the corpus actually produced, which is a different claim -- the
property is about the function and this is about the run.

**The pass-through fires exactly where it should.** Each group's own estimates
are put back through `CalibratorAgent`, and the groups it left unchanged must be
exactly the groups `BiasDetective` declined to speak about. Not "most of them":
exactly. A pass-through that fired one group early would be silently ignoring a
factor, and one that fired late would be applying a factor that does not exist.

**The backtest is reported with its denominator.** `scored` and `graded` travel
beside `score`, because a corpus below the threshold produces a `0.0` that means
"nothing was scored" and a badly calibrated one produces a `0.0` that means "no
correction helped", and only the second is a grade.

There is no answer key for any of this, and that is not an omission. The corpus
labels estimates and outcomes; a *calibration factor* is not something a
document can state, so there is nothing to compare against. What can be checked
is internal consistency -- the threshold, the pass-through boundary, the
arithmetic -- and those are checked here rather than approximated with a
ground truth that would have to be computed by the same code being graded.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from praxis.agents.bias import MINIMUM_SAMPLE, BiasDetective, CalibrationFactor
from praxis.agents.calibrator import CalibratorAgent
from praxis.agents.scoring import Backtest, ScoringAgent, total
from praxis.domain.enums import FindingKind, Unit
from praxis.domain.records import Finding
from praxis.store.repository import Repository

PROBE_QUANTITY = Decimal(10)
"""The raw estimate each group is calibrated with, to see which path fires.

An arbitrary positive number, and it has to be arbitrary: the question is
whether the *pass-through* fired, which depends on the group's history and not
on the size of the estimate. Ten rather than one so that a correction moves it
visibly, and positive so that the zero-estimate path -- which passes through for
a different reason -- is never the one being measured.
"""


@dataclass(frozen=True, slots=True)
class CalibrationScore:
    """What the store's calibration says about itself.

    Attributes:
        groups: Every `(owner, work_class)` group the store holds.
        measured: Groups with enough resolved history to state a factor.
        by_verdict: How many groups landed on each verdict. The refusals are
            most of this and are the point of it.
        threshold_holds: Whether every group below `MINIMUM_SAMPLE` carries no
            factor. False is a failure of the phase's central claim.
        pass_through: Groups the calibrator left unchanged.
        corrected: Groups it adjusted.
        pass_through_exact: Whether those two sets are exactly the groups that
            refused and the groups that spoke. Anything else is a boundary bug.
        findings: Calibration findings standing in the store.
        backtest: Every group's grade, added.
    """

    groups: int = 0
    measured: int = 0
    by_verdict: Mapping[str, int] = field(default_factory=dict)
    threshold_holds: bool = True
    pass_through: int = 0
    corrected: int = 0
    pass_through_exact: bool = True
    findings: int = 0
    backtest: Backtest = field(default_factory=Backtest)

    @property
    def refused(self) -> int:
        """Groups with nothing to say. The ordinary outcome, and usually all of them."""
        return self.groups - self.measured

    @property
    def measured_rate(self) -> Decimal:
        """Share of groups that could speak.

        Not a quality score in either direction. A low rate means the corpus is
        young, which is a fact about the corpus; a high one on a small corpus
        would mean the threshold was not being enforced.
        """
        if not self.groups:
            return Decimal(0)
        return (Decimal(self.measured) / Decimal(self.groups)).quantize(Decimal("0.0001"))


def grade_calibration(repository: Repository) -> CalibrationScore:
    """Grade what the store's calibration half concluded about itself.

    Takes no run and no answer key. Everything here is recomputed from the store
    -- which is the point: a grade taken from the pass's own return value would
    report what the agents said rather than what survived being written.

    Args:
        repository: The store, after a calibration pass.

    Returns:
        The score, populated even for an empty store.
    """
    factors = BiasDetective(repository).all_factors()
    if not factors:
        return CalibrationScore(findings=_findings(repository))

    calibrator = CalibratorAgent(repository)
    passed = tuple(
        factor
        for factor in factors
        if not calibrator.calibrate(
            owner=factor.group.owner,
            work_class=factor.group.work_class,
            quantity=PROBE_QUANTITY,
            unit=Unit.HOURS,
        ).adjusted
    )
    spoke = frozenset(factor.group for factor in factors if factor.speaks)
    silent = frozenset(factor.group for factor in factors if not factor.speaks)

    return CalibrationScore(
        groups=len(factors),
        measured=len(spoke),
        by_verdict=_by_verdict(factors),
        threshold_holds=all(_holds(factor) for factor in factors),
        pass_through=len(passed),
        corrected=len(factors) - len(passed),
        pass_through_exact=frozenset(factor.group for factor in passed) == silent,
        findings=_findings(repository),
        backtest=total(ScoringAgent(repository).backtest()),
    )


def _holds(factor: CalibrationFactor) -> bool:
    """Whether this group respects the threshold, in both directions.

    Both directions on purpose. "No factor below the threshold" is satisfied by
    a detective that never speaks at all, so the converse is checked too: a
    group at or above the threshold with a usable sample must carry one.

    `n` is the only test needed, including for an unclassified group. Those
    never form a sample at all, so they arrive here with `n == 0` and are
    covered by the first branch -- a second check on the verdict would be
    unreachable code asserting something already true.
    """
    if factor.n < MINIMUM_SAMPLE:
        return factor.factor is None
    return factor.factor is not None


def _by_verdict(factors: tuple[CalibrationFactor, ...]) -> dict[str, int]:
    """How many groups landed on each verdict, as the enum spells them.

    A dict rather than zeros for every member: "no group was unclassified" and
    "the split was not reported" are different, and a table of zeros claims the
    second while meaning the first.
    """
    counted: dict[str, int] = {}
    for factor in factors:
        counted[factor.verdict.value] = counted.get(factor.verdict.value, 0) + 1
    return counted


def _findings(repository: Repository) -> int:
    """Calibration findings standing in the store, counted in SQLite.

    Counted by kind, so the assumption breaches Phase 5 writes and the
    contradictions Phase 5 detects are not folded into this number.
    """
    return sum(
        1
        for finding in repository.list_all(Finding)
        if finding.kind is FindingKind.CALIBRATION_BIAS
    )
