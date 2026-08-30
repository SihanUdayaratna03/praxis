"""`CalibratorAgent`: applies a factor when one exists, and says so when none does.

The half of Phase 7 a person actually talks to. `BiasDetective` answers "what
does this group's history say"; this answers "so what should I write down", and
attaches the sentence explaining why.

**The pass-through is the primary path, not the fallback.** Six of this
project's own seven estimates would take it, and in any real corpus most groups
sit below the threshold most of the time. A design that treated correction as
normal and pass-through as an exception would have the frequencies backwards --
and, worse, would train a reader to see the ordinary answer as a failure. So an
unadjusted estimate comes back as a fully populated result with a reason
attached, never as a `None`, never as an error, and never coloured red by
anything downstream.

**It explains itself in every case, including the cases where it did nothing.**
The explanation names `n`, the confidence, and what the confidence was computed
from, because a correction a person cannot argue with is a correction they will
either follow blindly or ignore entirely. `CalibratorAgent` is the one component
in this phase whose output is read by a human rather than by `FusionBridge`.

**It generates nothing.** The explanation is a template over numbers it was
handed -- see ADR 0025. Everything here is arithmetic and string formatting, so
the module imports no provider and, like `praxis.agents.bias` and
`praxis.agents.reconciliation`, cannot reach one. A sentence that restates a
factor is a sentence that can restate it *wrongly*, and there is nothing in this
output that a model would add except the possibility of a number disagreeing
with the number it came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final

from praxis.agents.bias import BiasDetective, BiasDirection, CalibrationFactor
from praxis.agents.distribution import REPORTED_PLACES
from praxis.domain.enums import Unit
from praxis.store.repository import Repository

CALIBRATOR_NAME: Final = "CalibratorAgent"
"""Spelled as `praxis.config.models.NON_LLM_AGENTS` spells it."""

MINIMUM_CORRECTED: Final = REPORTED_PLACES
"""The smallest quantity a correction may report.

A factor is always positive and so is any quantity worth correcting, so the
product is positive too -- but at four reported places a small enough estimate
scaled by a small enough factor rounds to zero, and `Estimate` already says what
that would mean: "an estimate of zero predicts nothing". A correction that
predicts nothing is not a correction, so the value is held at the smallest
figure this precision can express and the arithmetic never hands back a zero it
did not compute.
"""


@dataclass(frozen=True, slots=True)
class Calibration:
    """One raw estimate, what calibration did to it, and why.

    Returned whether or not anything was adjusted, with `basis` carrying the
    detective's whole verdict either way -- so a caller printing a table has
    `n`, the verdict and the reason available on the row that was left alone,
    which is the row most likely to need explaining.

    Attributes:
        owner: Whose estimate this is.
        work_class: What kind of work, in the store's own spelling.
        unit: What both quantities are measured in. Never converted here.
        raw: The estimate as given.
        corrected: After the factor, or exactly `raw` when none applied.
        low: `raw` through the optimistic end of the band, when there is one.
        high: `raw` through the pessimistic end. Absent together with `low`.
        basis: What `BiasDetective` said, verdict and refusal reason included.
        explanation: Why the number changed, or why it did not. Never empty.
    """

    owner: str
    work_class: str
    unit: Unit
    raw: Decimal
    corrected: Decimal
    basis: CalibrationFactor
    explanation: str
    low: Decimal | None = None
    high: Decimal | None = None

    @property
    def adjusted(self) -> bool:
        """Whether a factor was applied. False is the ordinary answer."""
        return self.corrected != self.raw

    @property
    def factor(self) -> Decimal | None:
        """The exact factor used, or `None` when none was.

        Reads through `basis` rather than copying the number, so a correction is
        always traceable to the verdict it came from and the two cannot drift.
        """
        return self.basis.factor if self.basis.speaks else None

    @property
    def n(self) -> int:
        """How many resolved estimates the answer rests on. Zero is meaningful."""
        return self.basis.n


class CalibratorAgent:
    """Turns a raw estimate into a calibrated one, or explains why it cannot.

    Holds a `BiasDetective` rather than re-implementing the lookup, so there is
    exactly one place the threshold lives and one place the arithmetic happens.
    Takes no provider -- see the module docstring and ADR 0025.
    """

    def __init__(self, repository: Repository) -> None:
        """Bind the calibrator to a store.

        Args:
            repository: The store history is read from. Nothing is written.
        """
        self._detective = BiasDetective(repository)

    def calibrate(
        self, *, owner: str, work_class: str, quantity: Decimal, unit: Unit
    ) -> Calibration:
        """Apply this group's factor to a new estimate, if there is one.

        Args:
            owner: Who is estimating.
            work_class: What kind of work, in the store's own spelling.
            quantity: The raw estimate -- hands-on effort, not wall clock.
            unit: What it is measured in.

        Returns:
            The result, adjusted or not. Never `None`.

        Raises:
            ValueError: if the quantity is negative. Zero is allowed and passes
                through with its reason, because an estimate of no hands-on
                effort is a thing a record can hold; a negative one is not.
        """
        if quantity < 0:
            message = "a raw estimate cannot be negative"
            raise ValueError(message)
        basis = self._detective.factor_for(owner=owner, work_class=work_class)
        return apply(basis, quantity=quantity, unit=unit)


def apply(basis: CalibrationFactor, *, quantity: Decimal, unit: Unit) -> Calibration:
    """Put one raw estimate through one verdict.

    Free rather than a method, so the whole decision can be property-tested
    against a generated verdict without a store behind it -- the same reason
    `praxis.agents.bias.summarise` is free.

    Args:
        basis: What the detective said about this group.
        quantity: The raw estimate.
        unit: What it is measured in.

    Returns:
        The result, with an explanation either way.
    """
    group = basis.group
    if not basis.speaks or quantity <= 0:
        return Calibration(
            owner=group.owner,
            work_class=group.work_class,
            unit=unit,
            raw=quantity,
            corrected=quantity,
            basis=basis,
            explanation=_unchanged_because(basis, quantity=quantity, unit=unit),
        )

    factor = basis.factor
    low, high = basis.low, basis.high
    if (
        factor is None or low is None or high is None
    ):  # pragma: no cover -- speaks implies all three
        message = "a speaking verdict must carry a factor and a band"
        raise AssertionError(message)

    return Calibration(
        owner=group.owner,
        work_class=group.work_class,
        unit=unit,
        raw=quantity,
        corrected=_scaled(quantity, factor),
        low=_scaled(quantity, low),
        high=_scaled(quantity, high),
        basis=basis,
        explanation=_adjusted_because(basis, quantity=quantity, unit=unit),
    )


def _scaled(quantity: Decimal, factor: Decimal) -> Decimal:
    """One quantity through one factor, never rounding down to nothing."""
    scaled = (quantity * factor).quantize(REPORTED_PLACES, rounding=ROUND_HALF_EVEN)
    return max(scaled, MINIMUM_CORRECTED)


def _adjusted_because(basis: CalibrationFactor, *, quantity: Decimal, unit: Unit) -> str:
    """Why the number moved, with everything needed to argue with it.

    Names the factor, the direction in the words a person uses, the sample it
    came from, the band the raw estimate maps onto, and the confidence with what
    the confidence was computed from. A correction whose basis is not stated is
    one a reader either follows blindly or ignores; neither is calibration.
    """
    spread = basis.spread
    factor = basis.factor
    if spread is None or factor is None:  # pragma: no cover -- only called when speaking
        message = "no adjustment to explain"
        raise AssertionError(message)

    corrected = _scaled(quantity, factor)
    low, high = _scaled(quantity, spread.low), _scaled(quantity, spread.high)
    trend = (
        "came in close to what was predicted"
        if basis.direction is BiasDirection.NONE
        else f"ran {basis.magnitude}x {basis.direction}"
    )
    return (
        f"{quantity} {unit} becomes {corrected} {unit}. "
        f"{basis.group.owner}'s last {spread.n} resolved estimates of "
        f"{basis.group.work_class} work {trend}, so this is scaled by {factor}x. "
        f"Ordinarily between {low} and {high} {unit}. "
        f"n={spread.n}, confidence={spread.confidence}, computed from "
        f"{spread.n} resolved estimates with a log-space spread of {spread.dispersion}."
    )


def _unchanged_because(basis: CalibrationFactor, *, quantity: Decimal, unit: Unit) -> str:
    """Why nothing moved, stated as an answer rather than as an absence.

    This is the sentence most readers of this system will see most often, so it
    says the thing that stops it being read as breakage: passing an estimate
    through unchanged is the correct behaviour when there is no factor, not a
    stage that failed to run.
    """
    if quantity <= 0 and basis.speaks:
        return (
            f"{quantity} {unit}, unchanged. There is a factor for "
            f"{basis.group.work_class} work, and nothing to apply it to: scaling "
            f"a zero estimate leaves a zero estimate, which predicts nothing."
        )
    return (
        f"{quantity} {unit}, unchanged. No calibration factor exists for "
        f"{basis.group.owner} on {basis.group.work_class} work -- {basis.reason} "
        f"An unchanged estimate is the correct answer here rather than a stage "
        f"that failed: most groups sit below the threshold most of the time."
    )
