"""`ScoringAgent`: would the correction have helped? Walked forward, never fitted.

Deterministic, and named in `NON_LLM_AGENTS` since Phase 0. The question is
arithmetic: take an estimate that has since been answered, ask what
`CalibratorAgent` would have written down at the time, and see whether that
number sits closer to what really happened than the raw one did.

**The backtest is prequential, and that is the whole design.** Each row is
scored using only the rows *before* it. A factor fitted over the entire history
and then applied to a row inside that history has already seen the answer it is
being graded on, and would report an improvement rate that says nothing except
that a mean is close to the points it was computed from. Walking forward is the
only version of this measurement that can be wrong, which is the only version
worth reporting.

**Error is measured in log space**, for the reason `praxis.agents.distribution`
computes everything there. Being 2x over and 2x under are the same size of miss,
and an absolute difference in hours would call the first twice as bad as the
second on a six-week estimate and half as bad on a six-hour one.

**`scored` is reported beside `score`, always.** A backtest that had nothing to
score and one that scored badly are different facts and a bare rate cannot tell
them apart -- which matters more here than anywhere else in the phase, because
"nothing to score" is what an honest corpus below the threshold produces and it
will be the ordinary result for some time. Run against this project's own seven
outcomes, this scores exactly nothing: walking forward, no group ever reaches
`MINIMUM_SAMPLE`, so no correction is ever formed to grade. That is the correct
answer and it is also the clearest available statement of why seven outcomes are
not evidence of anything.

**Active against active.** `Outcome` carries the split so that a phase which ran
long because it waited on somebody else is not scored as an estimation error.
Folding blocked time in would grade the estimator on the calendar.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from praxis.agents.bias import MINIMUM_SAMPLE, CalibrationGroup, grouped, summarise
from praxis.agents.distribution import REPORTED_PLACES, precise, ratio_of
from praxis.store.reports import CalibrationRow
from praxis.store.repository import Repository

SCORER_NAME: Final = "ScoringAgent"
"""Spelled as `praxis.config.models.NON_LLM_AGENTS` spells it."""


@dataclass(frozen=True, slots=True)
class Backtest:
    """What walking one history forward found.

    Attributes:
        group: The group walked, or `None` for a whole-store total.
        considered: Resolved rows with a usable ratio, walked in order.
        scored: Rows a correction existed for, so `considered - scored` is how
            many fell before the threshold was ever reached.
        improved: Corrections that landed closer to the actual.
        worsened: Corrections that landed further away.
        unchanged: Corrections that moved the error by less than a reported
            place. Counted apart because a correction that changed nothing is
            not evidence either way.
        raw_error: Mean absolute log error before correction, over the scored
            rows only -- so the two errors are comparable by construction.
        corrected_error: The same, after.
    """

    group: CalibrationGroup | None = None
    considered: int = 0
    scored: int = 0
    improved: int = 0
    worsened: int = 0
    unchanged: int = 0
    raw_error: Decimal = Decimal(0)
    corrected_error: Decimal = Decimal(0)

    @property
    def graded(self) -> bool:
        """Whether anything was scored at all.

        The flag that stops a `score` of zero being read as a failing grade. A
        history that never reaches the threshold scores nothing, which is a
        statement about the history rather than about the calibrator.
        """
        return self.scored > 0

    @property
    def score(self) -> Decimal:
        """Share of scored corrections that moved closer to the actual.

        Zero when nothing was scored, and `graded` is how a reader tells that
        apart from a correction that helped no one.
        """
        if not self.scored:
            return Decimal(0)
        with precise():
            return (Decimal(self.improved) / Decimal(self.scored)).quantize(REPORTED_PLACES)

    @property
    def error_reduction(self) -> Decimal:
        """How much the mean log error fell. Negative means it rose.

        Reported beside the count because they can disagree: a correction that
        helps six rows a little and hurts one badly wins on the rate and loses
        on the magnitude, and only saying both makes that visible.
        """
        return self.raw_error - self.corrected_error


class ScoringAgent:
    """Grades whether calibration would have helped, over history the store holds.

    A read and nothing else: no record is written, no id allocated, no provider
    taken. Like `BiasDetective`, it exists to answer a question about the store
    rather than to change it.
    """

    def __init__(self, repository: Repository) -> None:
        """Bind the scorer to a store.

        Args:
            repository: The store to read history from.
        """
        self._repository = repository

    def backtest_group(self, *, owner: str, work_class: str) -> Backtest:
        """Walk one group's history forward and grade each correction.

        Args:
            owner: The estimator.
            work_class: The kind of work, in the store's own spelling.

        Returns:
            The result, `graded` false when the threshold was never reached.
        """
        group = CalibrationGroup(owner=owner, work_class=work_class)
        rows = self._repository.calibration_history(owner=owner, work_class=work_class)
        return walk(group, rows)

    def backtest(self) -> tuple[Backtest, ...]:
        """Every group in the store, walked separately.

        Separately and never pooled: a correction is only ever formed from one
        estimator's history of one kind of work, so pooling the *grades* across
        groups would be scoring a factor nothing would have applied.

        Returns:
            One result per group, ungraded groups included -- the count of
            those is the finding, not noise to be filtered.
        """
        history = self._repository.calibration_history()
        return tuple(walk(group, rows) for group, rows in grouped(history))


def walk(group: CalibrationGroup, rows: Sequence[CalibrationRow]) -> Backtest:
    """Score one group's history, using only what was known before each row.

    The rows arrive in estimate-id order, which is the order they were written,
    so "before" is a prefix rather than a sort. For each resolved row with a
    usable ratio: form a verdict from the rows already walked, and if it speaks,
    compare the corrected estimate's log error against the raw one's.

    A row that falls before the threshold is counted in `considered` and not in
    `scored`. It still joins the prefix, so it contributes to every later row's
    factor -- an unscored row is history rather than a gap.

    Args:
        group: The key these rows share.
        rows: That group's history, in order. Unresolved rows are skipped and
            do not join the prefix, because a row with no actual teaches the
            factor nothing.

    Returns:
        The grade, with the sample it rests on.
    """
    seen: list[CalibrationRow] = []
    considered = improved = worsened = unchanged = 0
    raw_total = corrected_total = Decimal(0)

    for row in rows:
        ratio = _usable(row)
        if ratio is None:
            continue
        considered += 1
        verdict = summarise(group, seen) if len(seen) >= MINIMUM_SAMPLE else None
        seen.append(row)
        if verdict is None or not verdict.speaks or verdict.factor is None:
            continue

        raw_error = _log_error(ratio)
        corrected_error = _log_error(_corrected_ratio(ratio, verdict.factor))
        raw_total += raw_error
        corrected_total += corrected_error
        if corrected_error < raw_error - REPORTED_PLACES:
            improved += 1
        elif corrected_error > raw_error + REPORTED_PLACES:
            worsened += 1
        else:
            unchanged += 1

    scored = improved + worsened + unchanged
    return Backtest(
        group=group,
        considered=considered,
        scored=scored,
        improved=improved,
        worsened=worsened,
        unchanged=unchanged,
        raw_error=_mean(raw_total, scored),
        corrected_error=_mean(corrected_total, scored),
    )


def total(results: Iterable[Backtest]) -> Backtest:
    """Add up per-group grades into one row, without pooling their samples.

    The counts add because they are counts of independent judgements. The two
    errors are re-weighted by `scored` rather than averaged, so a group with
    twenty scored rows does not carry the same weight as one with two.

    Args:
        results: Per-group backtests.

    Returns:
        One `Backtest` with `group` unset, meaning "all of them".
    """
    counted = list(results)
    scored = sum(result.scored for result in counted)
    return Backtest(
        group=None,
        considered=sum(result.considered for result in counted),
        scored=scored,
        improved=sum(result.improved for result in counted),
        worsened=sum(result.worsened for result in counted),
        unchanged=sum(result.unchanged for result in counted),
        raw_error=_weighted(counted, scored, attribute="raw_error"),
        corrected_error=_weighted(counted, scored, attribute="corrected_error"),
    )


def _usable(row: CalibrationRow) -> Decimal | None:
    """This row's `actual / estimated`, or nothing if it has none."""
    if not row.resolved or row.actual_active is None:
        return None
    return ratio_of(row.estimated_active, row.actual_active)


def _corrected_ratio(ratio: Decimal, factor: Decimal) -> Decimal:
    """What the ratio becomes once the raw estimate is scaled by the factor.

    `actual / (estimated * factor)`, which is `ratio / factor` -- so the whole
    comparison is two divisions and never needs the estimate itself. A perfect
    factor moves the ratio to exactly one, which is what makes the log error the
    natural distance.
    """
    with precise():
        return +(ratio / factor)


def _log_error(ratio: Decimal) -> Decimal:
    """How far a ratio sits from one, symmetrically.

    `|ln(ratio)|`, so 2x over and 2x under are the same size of miss. An
    absolute difference in hours would call the first twice as bad as the second
    on a six-week estimate and half as bad on a six-hour one.
    """
    with precise():
        return abs(ratio.ln())


def _mean(totalled: Decimal, count: int) -> Decimal:
    """A mean, or zero where there is nothing to average."""
    if not count:
        return Decimal(0)
    with precise():
        return (totalled / count).quantize(REPORTED_PLACES)


def _weighted(results: Sequence[Backtest], scored: int, *, attribute: str) -> Decimal:
    """One error column across groups, weighted by how much each group graded."""
    if not scored:
        return Decimal(0)
    with precise():
        weighted = sum(
            (getattr(result, attribute) * result.scored for result in results),
            start=Decimal(0),
        )
        return (weighted / scored).quantize(REPORTED_PLACES)
