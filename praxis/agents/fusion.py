"""The bridge between the two halves: what an assumption's estimate is worth, corrected.

This is the component the project exists to reach, and the first thing worth
saying about it is how little it does. `ARCHITECTURE.md`'s fusion mechanism has
five steps and **two of them shipped in earlier phases**:

1. `AssumptionFormalizer` compiles an assumption into a predicate -- Phase 5.
2. Something recognises that the predicate's subject is a quantified
   forward-looking claim and writes an `estimated_as` edge -- **Phase 4**, in
   `praxis.agents.extractor`, inside a call that was already holding the
   assumption and its quantity. ADR 0016.
3. That edge now carries a second source of evidence: the estimator's
   calibration history for the estimate's work class.
4. `BiasDetective` answers with a factor, an `n`, a band and a confidence, or
   refuses below `n = 5` with no override -- Phase 7, ADR 0024.
5. If the calibrated value violates the predicate, a finding is raised against
   the decisions resting on the assumption.

So `FusionBridge` is **not** detecting that an assumption is secretly an
estimate. That detection is four phases old and the eval harness has been
grading it since Phase 4. This module walks the edges that already exist, asks
one question per edge, and decides whether the answer changes anything.

## Only arithmetic can breach, and here that line is sharp

ADR 0019 constrains this module directly. Recognising that an assumption is an
estimate in disguise is judgement -- and it already happened, in a model call,
in Phase 4. Deciding whether the calibrated number violates the predicate is
`praxis.predicates`, which is a total evaluator over a three-valued logic, and
no model can reach it. **`FusionBridge` therefore calls no model at all** and is
named in `NON_LLM_AGENTS`: everything left after Phase 4 took the judgement is a
graph walk, a multiplication and two evaluations. See ADR 0026.

## The finding fires on a flip, not on a correction

The common case is that a factor exists, changes the number, and changes
nothing else: an assumption that six weeks is enough, corrected to four weeks,
is still an assumption that six weeks is enough. Emitting a finding there would
produce one per priced assumption per run and bury the case that matters.

The case that matters is a **flip**: the raw estimate satisfies the predicate
and the calibrated one violates it. That is `ARCHITECTURE.md`'s sentence --
"Decision D-0042 is probably built on a 40% under-estimate" -- and it is the
only outcome here that is worth waking somebody for.

`UNKNOWN` is not a flip. A predicate that moves from `TRUE` to `UNKNOWN` has
lost information rather than gained a violation, so it gets its own verdict and
raises nothing -- a projection dressed as a breach is the most convincing kind
of wrong answer this system could give.

That branch is currently **unreachable through this module**, and the reason is
worth knowing rather than discovering. `subject_of` accepts a predicate only
when `constraints_of` yields exactly one name, and `constraints_of` yields names
only for simple `name OP literal` comparisons -- a division, a string comparison
or a second identifier makes it yield nothing at all, which is `NO_SUBJECT`
long before any evaluation happens. So every predicate that reaches the
comparison contains exactly one identifier, that identifier is the one the
evaluation binds, and the evaluator is total over it. The guard is kept and
tested through `moved` directly; see that function for why it is a standing
constraint rather than dead code.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from praxis.agents.bias import BiasDetective, CalibrationFactor
from praxis.agents.classifier import UNCLASSIFIED
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Estimate
from praxis.monitor.facts import subject_of
from praxis.predicates.ast import Evaluation, Formula, Truth
from praxis.predicates.errors import PredicateSyntaxError
from praxis.predicates.evaluator import evaluate
from praxis.predicates.parser import parse
from praxis.predicates.world import WorldState
from praxis.store.repository import Repository

FUSION_NAME: Final = "FusionBridge"
"""Spelled as `praxis.config.models.NON_LLM_AGENTS` spells it, so the set that
refuses to route this agent and the agent itself cannot drift under a rename."""


class FusionVerdict(StrEnum):
    """What one `estimated_as` edge turned out to be worth.

    Every outcome is a member, including every refusal. `FusionBridge` returns a
    result per edge in all cases rather than filtering as it walks, because "no
    factor for this group" and "this edge was never looked at" are different
    facts and a report that shows only findings cannot tell them apart.
    """

    FLIPPED = "flipped"
    """The raw estimate satisfied the predicate and the calibrated one violates
    it. The only verdict that raises a finding."""

    RELIEVED = "relieved"
    """The reverse flip: the raw estimate violated the predicate and the
    calibrated one satisfies it. Reported, never a finding -- calibration
    excusing an assumption is not evidence that the assumption is sound."""

    UNCHANGED = "unchanged"
    """A factor applied and the verdict did not move. The common case."""

    UNDECIDED = "undecided"
    """One side or both evaluated `UNKNOWN`, so there is no flip to speak of."""

    NO_FACTOR = "no_factor"
    """`BiasDetective` refused this group -- below `n = 5`, or nothing resolved.
    Not an error: the threshold working."""

    UNCLASSIFIED_WORK = "unclassified_work"
    """The estimate carries no work class, so it belongs to no group and there
    is nothing to ask about."""

    NO_SUBJECT = "no_subject"
    """The predicate does not parse, names nothing, or names more than one
    quantity. Binding one of two names would produce a finding with a number
    attached and no way to know it was the wrong number."""

    MISSING_ESTIMATE = "missing_estimate"
    """The edge points at an estimate the store no longer holds as standing --
    in practice a retracted one, since the store's own foreign key refuses an
    edge to a record that never existed."""


_FINDING_VERDICTS: Final[frozenset[FusionVerdict]] = frozenset({FusionVerdict.FLIPPED})
"""The verdicts a finding is raised for. A set of one, deliberately: `RELIEVED`
reads like a result and is not one, and keeping the membership here rather than
in an `if` is what stops it quietly growing."""


@dataclass(frozen=True, slots=True)
class PricedAssumption:
    """One `estimated_as` edge, priced against its estimator's track record.

    Attributes:
        assumption: The record whose predicate was evaluated.
        estimate: The estimate the edge points at, when the store still holds
            it. `None` only for `MISSING_ESTIMATE`.
        verdict: What this edge turned out to be worth.
        subject: The single quantity the predicate is about, or `None` when the
            predicate never yielded one.
        raw: The estimate's own active quantity, or `None`.
        calibrated: `raw` multiplied by the factor, or `None` when no factor
            spoke. This is the number the second evaluation was run against.
        low: The calibrated quantity at the least-corrected end of the band.
        high: The calibrated quantity at the most-corrected end.
        factor: What `BiasDetective` said, in every case including refusals --
            it never returns `None`, so this field never has to mean two things.
        raw_evaluation: The predicate against the estimate as written.
        calibrated_evaluation: The predicate against the corrected number.
        reason: Why this verdict, in a sentence. Never empty.
    """

    assumption: Assumption
    estimate: Estimate | None
    verdict: FusionVerdict
    subject: str | None
    raw: Decimal | None
    calibrated: Decimal | None
    low: Decimal | None
    high: Decimal | None
    factor: CalibrationFactor | None
    raw_evaluation: Evaluation | None
    calibrated_evaluation: Evaluation | None
    reason: str

    @property
    def flips(self) -> bool:
        """Whether calibration moved the predicate's verdict into a violation."""
        return self.verdict in _FINDING_VERDICTS

    @property
    def priced(self) -> bool:
        """Whether a factor was actually applied to a number here."""
        return self.calibrated is not None

    def describe(self) -> str:
        """One line, in the shape `ARCHITECTURE.md`'s fusion sentence uses.

        Built off the same `CalibrationFactor.describe()` Phase 7 fixed, so the
        factor is spelled one way in the whole system rather than two.
        """
        if self.factor is None or not self.priced:
            return f"{self.assumption.id}: {self.reason}"
        return (
            f"{self.assumption.id} assumed {self.subject} = {self.raw}; "
            f"{self.factor.describe()} implies {self.calibrated} "
            f"(band {self.high} to {self.low}) -- {self.verdict.value}"
        )


class FusionBridge:
    """Walks the `estimated_as` edges and prices each one against its history.

    Deterministic and calls no model: everything judgemental about this
    relationship was decided in Phase 4 when the edge was written. See ADR 0026.
    """

    name: Final = FUSION_NAME

    def __init__(self, repository: Repository) -> None:
        """Bind the bridge to a store.

        Args:
            repository: The store holding the assumptions, the edges, the
                estimates and the history behind the factor.
        """
        self._repository = repository
        self._detective = BiasDetective(repository)

    def price(self, assumption: Assumption, *, now: datetime) -> tuple[PricedAssumption, ...]:
        """Price every estimate this assumption is linked to.

        Args:
            assumption: The record to price.
            now: The clock the predicate is evaluated against. Timezone-aware,
                invariant 5.

        Returns:
            One result per `estimated_as` edge, in the order the store returns
            them. Empty when the assumption carries no such edge, which is the
            ordinary case for an assumption that is not an estimate in disguise.
        """
        return tuple(self._priced(assumption, now=now))

    def price_all(self, *, now: datetime) -> tuple[PricedAssumption, ...]:
        """Price every `estimated_as` edge in the store.

        Args:
            now: The clock the predicates are evaluated against.

        Returns:
            Every result, assumptions in store order. Assumptions with no edge
            contribute nothing rather than a row saying so -- an assumption that
            is not an estimate is not a refusal, it is a different kind of thing.
        """
        return tuple(
            result
            for assumption in self._repository.list_all(Assumption)
            for result in self._priced(assumption, now=now)
        )

    def _priced(self, assumption: Assumption, *, now: datetime) -> Iterator[PricedAssumption]:
        """One result per edge, refusals included."""
        links = self._repository.links_from(assumption.id, types=(LinkType.ESTIMATED_AS,))
        for link in links:
            estimate = self._repository.get(Estimate, link.target_id)
            # `retracted` is checked as well as `None`, and it is the case that
            # actually happens: the store refuses a dangling edge outright
            # (`DanglingEdgeError`), so an edge whose estimate is gone is
            # normally an edge whose estimate was **withdrawn**. `get` returns
            # the retracted version rather than nothing -- retraction is a
            # statement about a record, not the disappearance of one -- so a
            # bridge that only tested for `None` would price a withdrawn
            # estimate as though it still stood.
            if estimate is None or estimate.retracted:
                yield _missing(assumption, link.target_id)
                continue
            yield self._price_one(assumption, estimate, now=now)

    def _price_one(
        self, assumption: Assumption, estimate: Estimate, *, now: datetime
    ) -> PricedAssumption:
        """The whole mechanism for one edge: subject, factor, two evaluations."""
        if estimate.work_class == UNCLASSIFIED:
            return _refused(
                assumption,
                estimate,
                FusionVerdict.UNCLASSIFIED_WORK,
                f"{estimate.id} carries no work class, so it belongs to no calibration group",
            )
        formula = _parsed(assumption)
        subject = subject_of(assumption)
        if formula is None or subject is None:
            return _refused(
                assumption,
                estimate,
                FusionVerdict.NO_SUBJECT,
                f"the predicate `{assumption.predicate}` names no single quantity to correct",
            )
        factor = self._detective.factor_for(owner=estimate.owner, work_class=estimate.work_class)
        if not factor.speaks:
            return _refused(
                assumption, estimate, FusionVerdict.NO_FACTOR, factor.reason, factor=factor
            )
        return _compared(assumption, estimate, formula, subject, factor, now=now)


def _compared(  # noqa: PLR0913 -- each part is irreducible and all six are needed at once
    assumption: Assumption,
    estimate: Estimate,
    formula: Formula,
    subject: str,
    factor: CalibrationFactor,
    *,
    now: datetime,
) -> PricedAssumption:
    """Evaluate the predicate twice and say what moved between the two.

    The multiplication is the whole correction: `CalibrationFactor.factor` is
    `actual / estimated`, so it is applied by multiplying and the caller never
    has to remember which way round it goes.
    """
    # A factor that speaks always carries a spread, so these are never None
    # here -- but they are typed optional on the factor, and asserting the
    # invariant with a local rather than an `assert` keeps mypy --strict happy
    # without putting a runtime check on a path that cannot fail.
    central = factor.factor or Decimal(1)
    raw = estimate.active_quantity
    calibrated = raw * central
    before = _evaluated(formula, subject, raw, now=now)
    after = _evaluated(formula, subject, calibrated, now=now)
    verdict, reason = moved(before, after, subject=subject, raw=raw, calibrated=calibrated)
    return PricedAssumption(
        assumption=assumption,
        estimate=estimate,
        verdict=verdict,
        subject=subject,
        raw=raw,
        calibrated=calibrated,
        low=raw * (factor.low or central),
        high=raw * (factor.high or central),
        factor=factor,
        raw_evaluation=before,
        calibrated_evaluation=after,
        reason=reason,
    )


def moved(
    before: Evaluation, after: Evaluation, *, subject: str, raw: Decimal, calibrated: Decimal
) -> tuple[FusionVerdict, str]:
    """Which of the four outcomes two evaluations describe. The rule of this module.

    Public, and tested directly, because of what the `UNDECIDED` branch turns
    out to be. `UNKNOWN` on either side is tested **first**, so no flip is ever
    read out of a verdict the evaluator declined to reach -- and as the language
    stands today that branch cannot be reached through `FusionBridge` at all.
    `subject_of` accepts a predicate only when `constraints_of` yields exactly
    one name, and `constraints_of` yields names only for simple `name OP
    literal` comparisons: a division, a string comparison or a second identifier
    makes it yield **nothing**, which is `NO_SUBJECT` several steps earlier. So
    every predicate that reaches here contains one identifier, that identifier is
    the one `_evaluated` binds, and the evaluation is total over it.

    The guard stays, and is tested through this function rather than through the
    bridge. It is a standing constraint on the predicate language rather than
    dead code: if `constraints_of` ever learns to name the subject of a
    compound predicate, a `TRUE`-to-`UNKNOWN` transition becomes reachable
    overnight, and without this branch it would arrive as a flip. A projection
    dressed as a breach is the most convincing kind of wrong answer this system
    could give, and it is not worth leaving one import away.

    Args:
        before: The predicate against the estimate as written.
        after: The predicate against the corrected number.
        subject: The quantity both were evaluated over. For the reason text.
        raw: The estimate's own quantity. For the reason text.
        calibrated: The corrected quantity. For the reason text.

    Returns:
        The verdict, and the sentence explaining it.
    """
    correction = f"{subject} {raw} corrected to {calibrated}"
    if Truth.UNKNOWN in (before.truth, after.truth):
        undecided = after if after.truth is Truth.UNKNOWN else before
        return (
            FusionVerdict.UNDECIDED,
            f"{correction}, but the predicate is undecided: {undecided.reason}",
        )
    if before.truth is after.truth:
        return (
            FusionVerdict.UNCHANGED,
            f"{correction}, and the predicate still evaluates {after.truth.value}",
        )
    if after.violated:
        return (
            FusionVerdict.FLIPPED,
            f"{correction}, which violates a predicate the raw estimate satisfied",
        )
    return (
        FusionVerdict.RELIEVED,
        f"{correction}, which satisfies a predicate the raw estimate violated",
    )


def _evaluated(formula: Formula, subject: str, value: Decimal, *, now: datetime) -> Evaluation:
    """The predicate against a world binding exactly one name.

    One name and no others: the world is built here rather than taken from the
    caller so that nothing measured, supplied or inferred can reach this
    evaluation. The only difference between the two runs is the number, which is
    what makes a difference between their verdicts attributable to the factor
    and to nothing else -- a world carrying a measured fact would let a breach be
    attributed to a correction that did not cause it.
    """
    return evaluate(formula, WorldState(now=now, facts={subject: value}))


def _parsed(assumption: Assumption) -> Formula | None:
    """The assumption's predicate as a tree, or `None` if it will not parse."""
    try:
        return parse(assumption.predicate)
    except PredicateSyntaxError:
        return None


def _refused(
    assumption: Assumption,
    estimate: Estimate,
    verdict: FusionVerdict,
    reason: str,
    *,
    factor: CalibrationFactor | None = None,
) -> PricedAssumption:
    """A result carrying no number, and the reason there is none."""
    return PricedAssumption(
        assumption=assumption,
        estimate=estimate,
        verdict=verdict,
        subject=subject_of(assumption),
        raw=estimate.active_quantity,
        calibrated=None,
        low=None,
        high=None,
        factor=factor,
        raw_evaluation=None,
        calibrated_evaluation=None,
        reason=reason,
    )


def _missing(assumption: Assumption, estimate_id: str) -> PricedAssumption:
    """An edge whose estimate the store no longer holds."""
    return PricedAssumption(
        assumption=assumption,
        estimate=None,
        verdict=FusionVerdict.MISSING_ESTIMATE,
        subject=subject_of(assumption),
        raw=None,
        calibrated=None,
        low=None,
        high=None,
        factor=None,
        raw_evaluation=None,
        calibrated_evaluation=None,
        reason=(
            f"the estimated_as edge points at {estimate_id}, "
            f"which the store no longer holds as a standing estimate"
        ),
    )


def flipped(results: Sequence[PricedAssumption]) -> tuple[PricedAssumption, ...]:
    """Only the results a finding is raised for.

    Args:
        results: Everything a run priced.

    Returns:
        The flips, in the order given.
    """
    return tuple(result for result in results if result.flips)
