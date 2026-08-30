"""How a sample of estimate-to-actual ratios is summarised. Arithmetic, nothing else.

Split out of `praxis.agents.bias` on the line this phase draws. `BiasDetective`
decides *which* rows form a group and *whether* a group may speak; this decides
what a sample of ratios says once it has been formed. Nothing here knows what an
`Estimate` is, what a work class is, or that a store exists: every function takes
a sequence of `Decimal` and returns a value, which is what makes the property
tests about the maths rather than about record construction.

`praxis.agents.reconciliation` made the same split in Phase 6 to keep a provider
out of reach. There is no provider anywhere in Phase 7, so this split buys
something else, and it is worth naming: a total function over a list of numbers
can be handed a distribution by `hypothesis` directly, and the four properties
this phase rests on are then claims about arithmetic rather than claims about a
fixture that happened to be built a particular way.

**A ratio is multiplicative, so the summary is computed in log space.** This is
the whole reason this module is not four lines. An estimator who is twice over
on one job and twice under on the next is, on average, exactly right -- and the
arithmetic mean of `0.5` and `2.0` is `1.25`, which says they under-estimate by a
quarter. The geometric mean of the same two is `1.0`. Every quantity here
follows from that: the central tendency is a geometric mean, the spread is a
standard deviation of logarithms, and the interval is therefore multiplicative --
a band you divide and multiply by, not one you add and subtract.

**All of it in `Decimal`, and never `float`.** Invariant 4 names money; the
reason applies with more force here. `Decimal.ln`, `.exp` and `.sqrt` give a
result that does not depend on the order the rows came back in, which is what
makes "re-running over an unchanged store produces an identical result" a
property that can be tested rather than a hope. Binary floating point would make
that claim false in a way no test would reliably catch.

**The precision is pinned, because `Decimal.ln` reads a global.** The decimal
context is thread-local and any caller may change it, so a determinism property
that depended on it would be a property about the ambient environment. Every
computation here runs inside an explicit `localcontext` at `CALIBRATION_PRECISION`.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Final

CALIBRATION_PRECISION: Final = 28
"""Significant digits every calculation here runs at.

Pinned rather than inherited. `Decimal.ln()` and `Decimal.exp()` take their
precision from the active context, which is thread-local and writable by anyone,
so a result computed under the ambient context would be reproducible only as
long as nothing else in the process had an opinion. 28 is Python's own default,
chosen so that this pin changes no answer today and cannot be changed by anyone
else tomorrow.
"""

_CONTEXT: Final = Context(prec=CALIBRATION_PRECISION, rounding=ROUND_HALF_EVEN)
"""The one context every calibration computation runs inside. Reached through
`precise`, never exported directly -- a `Context` is mutable, and a shared
mutable settings object handed out to callers is the global this pin exists to
escape."""


def precise() -> AbstractContextManager[Context]:
    """Enter the pinned decimal context.

    Public because `praxis.agents.scoring` computes log errors that have to
    agree with the factors computed here to the last place. Two modules using
    two precisions would put a rounding difference underneath a backtest and
    call it a result.

    Returns:
        A context manager for the pinned precision.
    """
    return localcontext(_CONTEXT)


REPORTED_PLACES: Final = Decimal("0.0001")
"""How precisely a summarised quantity is reported.

Four places, as `praxis.agents.estimation` and `praxis.eval.metrics` already use,
so a factor printed by the CLI and the same factor in the eval table cannot
disagree by rounding. Quantisation happens once, on the way out, rather than
between steps -- rounding a geometric mean before taking an interval around it
would move the interval.
"""

INTERVAL_LOG_SIGMAS: Final = Decimal(1)
"""How wide the reported band is, in standard deviations of the logarithm.

One rather than two, and the reason is the sample size this ever runs on. The
band is meant to be read as *the ordinary range* -- where this estimator's next
ratio will land if nothing changes -- and one sigma is about the middle two
thirds of a symmetric distribution. A 95% band computed from five or six points
would be a wider number carrying less information, because at that `n` the tail
is not being measured at all, only extrapolated. Reporting it would be precision
about a shape nobody has seen.

It is a **prediction** band and not a confidence interval on the mean, which is
why the dispersion is not divided by the square root of `n`. The question a
calibrator asks is "how far out is the next estimate likely to be", not "how
precisely do we know the average". The second is a narrower number and the wrong
one: it shrinks towards zero as the sample grows even when the estimator stays
wildly erratic.
"""

CONFIDENCE_HALF_AT: Final = Decimal(5)
"""The sample size at which the sample term of `confidence` reaches one half.

Deliberately the same number as `praxis.agents.bias.MINIMUM_SAMPLE`, and tied to
it by that module's own test rather than by coincidence. A group that has only
just cleared the threshold is a group whose factor deserves to be read as a
coin-flip-grade signal, and a formula that returned 0.9 there would be
contradicting, in a number, the caution the threshold exists to express.
"""


@dataclass(frozen=True, slots=True)
class Spread:
    """What a sample of ratios says, with how much to trust it attached.

    One object rather than four returns, and that is load-bearing. A point
    factor that can travel without its band is a point factor that will: the
    caller who wants one number takes `central` and the interval is never
    printed. Binding them means `CalibratorAgent` cannot cite a correction
    without also having the spread it was drawn from, and `FusionBridge` cannot
    read a factor without reading how firm it is.

    Attributes:
        n: How many ratios went in. Never zero -- `spread_of` returns `None`
            rather than an empty summary.
        central: The geometric mean of the ratios. The factor a raw estimate is
            multiplied by.
        dispersion: The sample standard deviation of the natural logarithms.
            Zero when every ratio agrees, and zero at `n = 1`, where it means
            "unmeasurable" rather than "none" -- see `spread_of`.
        low: `central` divided by `e ** (dispersion * INTERVAL_LOG_SIGMAS)`.
        high: `central` multiplied by the same. The band is multiplicative
            because the quantity is.
        confidence: How much of a signal this is, in `[0, 1)`. Falls with a
            small sample and with a wide spread, independently.
    """

    n: int
    central: Decimal
    dispersion: Decimal
    low: Decimal
    high: Decimal
    confidence: Decimal

    @property
    def band(self) -> tuple[Decimal, Decimal]:
        """The interval as a pair, for a caller that formats rather than computes."""
        return self.low, self.high


def ratio_of(estimated: Decimal, actual: Decimal) -> Decimal | None:
    """How much longer the work really took, as a multiplier on the estimate.

    `actual / estimated`, and that orientation is the whole point: the number
    that comes back is the one you **multiply a raw estimate by**. Above one the
    estimator under-estimates and reality runs longer; below one they
    over-estimate. The inverse reads more naturally in a sentence and requires
    every caller to remember to divide, which is a bug waiting for a tired
    afternoon.

    A zero on either side has no ratio and `None` is returned rather than an
    invented one -- the same rule `praxis.agents.reconciliation` applies to a
    band, and for the same reason. An estimate of no hands-on effort answered by
    real hands-on effort is not "infinitely wrong", it is a pairing this
    arithmetic cannot describe, and a caller that sees `None` can exclude the row
    instead of averaging in a fabrication.

    Args:
        estimated: What was predicted. Hands-on effort, not wall clock.
        actual: What it really took, in the same unit.

    Returns:
        The multiplier, or `None` where one does not exist.
    """
    if estimated <= 0 or actual <= 0:
        return None
    with precise():
        return +(actual / estimated)


def spread_of(ratios: Sequence[Decimal]) -> Spread | None:
    """Summarise a sample of ratios in log space.

    Every quantity is computed before anything is rounded, and rounding happens
    once on the way out. Taking the interval around an already-quantised mean
    would move the interval by up to half a reported place in each direction,
    which is small and is also the kind of small that shows up as a flapping
    test three phases later.

    At `n = 1` the dispersion is zero, and that is a value with a caveat rather
    than a measurement: one point has no spread to measure. It is safe here only
    because no caller can act on it -- `BiasDetective` refuses below
    `MINIMUM_SAMPLE`, so a single-point summary reaches a listing and never a
    correction. `confidence` at `n = 1` is `1/6`, which says the same thing in
    the field a caller is more likely to read.

    Args:
        ratios: The sample. Each must be positive; `ratio_of` is what produces
            them and returns `None` rather than a non-positive one.

    Returns:
        The summary, or `None` for an empty sample -- which is an absence rather
        than a distribution of nothing.

    Raises:
        ValueError: if any ratio is zero or negative. A logarithm of one is not
            a number, and silently dropping the row would make `n` disagree with
            the sample the caller handed over.
    """
    if not ratios:
        return None
    if any(ratio <= 0 for ratio in ratios):
        message = "a ratio must be positive; use ratio_of, which refuses to invent one"
        raise ValueError(message)

    with precise():
        logs = [ratio.ln() for ratio in ratios]
        mean = sum(logs, start=Decimal(0)) / len(logs)
        dispersion = _sample_deviation(logs, mean)
        central = mean.exp()
        widen = (dispersion * INTERVAL_LOG_SIGMAS).exp()
        return Spread(
            n=len(ratios),
            central=_reported(central),
            dispersion=_reported(dispersion),
            low=_reported(central / widen),
            high=_reported(central * widen),
            confidence=_reported(confidence_from(len(ratios), dispersion)),
        )


def confidence_from(n: int, dispersion: Decimal) -> Decimal:
    """How much of a signal a sample of this size and this scatter is.

    Two factors multiplied, because they fail independently: eight tightly
    agreeing points and eight wildly scattered ones are not equally informative,
    and neither are two tight points and twenty tight ones.

    - **Sample term**, `n / (n + CONFIDENCE_HALF_AT)`. Rises with `n`, reaches
      one half exactly at the refusal threshold, and never reaches one.
    - **Agreement term**, `1 / (1 + dispersion)`. One when every ratio agrees,
      falling as the logarithms scatter.

    The product is in `[0, 1)` and **never exactly 1**, which is the honest
    shape: no finite sample of a person's estimates justifies certainty about
    their next one, and a formula that could return 1.0 would eventually print
    it.

    Args:
        n: Sample size. Zero returns zero rather than dividing by zero.
        dispersion: The log-space standard deviation. Must not be negative.

    Returns:
        A confidence in `[0, 1)`, unquantised -- `spread_of` rounds it.
    """
    if n <= 0:
        return Decimal(0)
    if dispersion < 0:
        message = "a standard deviation cannot be negative"
        raise ValueError(message)
    with precise():
        sample = Decimal(n) / (Decimal(n) + CONFIDENCE_HALF_AT)
        agreement = Decimal(1) / (Decimal(1) + dispersion)
        return +(sample * agreement)


def _sample_deviation(logs: Sequence[Decimal], mean: Decimal) -> Decimal:
    """The sample standard deviation of already-computed logarithms.

    Divided by `n - 1` rather than by `n`: this is a sample of an estimator's
    behaviour standing in for every estimate they will make, not the whole of
    it, and dividing by `n` would report a spread biased low -- flattering
    exactly the estimator whose scatter most needs reporting.

    One point returns zero rather than dividing by zero. See `spread_of` for why
    that is safe and what says so in the output.
    """
    if len(logs) < 2:  # noqa: PLR2004 -- a spread needs two points, and that is the fact
        return Decimal(0)
    squares = sum(((value - mean) ** 2 for value in logs), start=Decimal(0))
    return (squares / (len(logs) - 1)).sqrt()


def _reported(value: Decimal) -> Decimal:
    """One quantity, rounded to what gets printed and stored."""
    return value.quantize(REPORTED_PLACES, rounding=ROUND_HALF_EVEN)
