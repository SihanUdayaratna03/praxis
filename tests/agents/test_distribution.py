"""The arithmetic under every calibration number, tested as arithmetic.

Split from `test_bias.py` the way the module is split from `bias.py`: nothing
here builds a record, opens a store or knows what a work class is, so a
falsifying example is a list of numbers a person can check by hand rather than a
fixture they have to reconstruct.

**Every strategy here is built to reach the case the property is about.** A
property saying "the band always contains the centre" is satisfied by a
generator that only ever produces one ratio, and a property about scatter is
worthless against ratios that never scatter. So the ratio strategy spans four
orders of magnitude either side of one, several tests assert that the interesting
condition was actually reached, and the two worked examples from this project's
own history are pinned as explicit cases beside the generated ones.
"""

from __future__ import annotations

from decimal import Decimal, getcontext, localcontext

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from praxis.agents.distribution import (
    CALIBRATION_PRECISION,
    CONFIDENCE_HALF_AT,
    INTERVAL_LOG_SIGMAS,
    REPORTED_PLACES,
    confidence_from,
    ratio_of,
    spread_of,
)

ratios = st.decimals(
    min_value=Decimal("0.0001"),
    max_value=Decimal("10000"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
).filter(lambda value: value > 0)
"""Ratios spanning four orders of magnitude either side of one.

Wide on purpose. A calibration factor is a multiplier, so `0.01` and `100` are
the same distance from `1` in the space the maths actually works in, and a
strategy clustered near one would never exercise the logarithms this module
exists for. The filter is belt and braces: `min_value` already excludes zero,
and a rounding surprise there would silently disarm every property below.
"""

samples = st.lists(ratios, min_size=1, max_size=40)
"""Samples from one up to comfortably past the refusal threshold."""

DOGFOOD = [Decimal("0.7636"), Decimal("0.4583"), Decimal("0.7077"), Decimal("0.56")]
"""This project's own `agent-implementation` ratios at the close of Phase 6.

Actual over estimated, so all four are below one: four over-estimates in a row.
Pinned as a literal because the properties below should hold on the real sample
that motivated them and not only on generated ones -- and because a change that
moves these numbers should have to say so.
"""


class TestARatio:
    """`actual / estimated`, and the cases where there isn't one."""

    def test_is_the_multiplier_you_apply_to_a_raw_estimate(self) -> None:
        """Above one means the work ran longer than predicted."""
        assert ratio_of(Decimal(4), Decimal(7)) == Decimal("1.75")

    def test_below_one_means_the_estimator_over_estimated(self) -> None:
        assert ratio_of(Decimal(4), Decimal(2)) == Decimal("0.5")

    @pytest.mark.parametrize(
        ("estimated", "actual"),
        [("0", "5"), ("5", "0"), ("0", "0")],
        ids=["no-estimate", "no-actual", "neither"],
    )
    def test_a_zero_on_either_side_has_no_ratio(self, estimated: str, actual: str) -> None:
        """ADR 0021's rule about a band, applied to the quantity underneath it.

        An estimate of no hands-on effort answered by real hands-on effort is not
        infinitely wrong; it is a pairing this arithmetic cannot describe, and a
        caller that gets `None` can exclude the row instead of averaging in a
        fabrication.
        """
        assert ratio_of(Decimal(estimated), Decimal(actual)) is None

    @given(estimated=ratios, actual=ratios)
    def test_is_positive_whenever_it_exists(self, estimated: Decimal, actual: Decimal) -> None:
        """Which is what makes every logarithm below defined."""
        ratio = ratio_of(estimated, actual)

        assert ratio is not None
        assert ratio > 0

    @given(estimated=ratios, actual=ratios)
    def test_inverts_when_the_two_sides_swap(self, estimated: Decimal, actual: Decimal) -> None:
        """A property of the orientation, which is the thing easiest to get backwards."""
        forward = ratio_of(estimated, actual)
        backward = ratio_of(actual, estimated)

        assert forward is not None
        assert backward is not None
        with localcontext() as context:
            context.prec = 12
            assert +(forward * backward) == Decimal(1)


class TestTheCentralTendency:
    """Why this is a geometric mean, held as a property rather than a comment."""

    def test_twice_over_and_twice_under_cancel_exactly(self) -> None:
        """The headline argument for log space, as one assertion.

        An estimator who was twice over once and twice under once is, on
        average, exactly right. The arithmetic mean of 0.5 and 2.0 is 1.25 and
        would report a 25% under-estimation bias that does not exist.
        """
        spread = spread_of([Decimal("0.5"), Decimal("2.0")])

        assert spread is not None
        assert spread.central == Decimal("1.0000")

    def test_a_sample_that_agrees_reports_what_it_agrees_on(self) -> None:
        spread = spread_of([Decimal("1.5")] * 6)

        assert spread is not None
        assert spread.central == Decimal("1.5000")
        assert spread.dispersion == Decimal("0.0000")

    def test_reproduces_this_projects_own_factor(self) -> None:
        """The worked example, computed rather than recalled.

        Four over-estimates, geometric mean 0.6103 -- which is 1.64x over when
        read the other way round, against a plain mean of 1.67x. The two differ
        by enough to matter and the geometric one is the honest number.
        """
        spread = spread_of(DOGFOOD)

        assert spread is not None
        assert spread.central == Decimal("0.6103")
        assert spread.n == 4

    @given(sample=samples)
    def test_always_lies_between_the_smallest_and_largest_ratio(
        self, sample: list[Decimal]
    ) -> None:
        """A mean outside its own sample would be a sign error, and this catches it."""
        spread = spread_of(sample)

        assert spread is not None
        assert min(sample) * Decimal("0.999") <= spread.central
        assert spread.central <= max(sample) * Decimal("1.001")

    @given(sample=samples)
    def test_does_not_depend_on_the_order_the_rows_arrived_in(self, sample: list[Decimal]) -> None:
        """Determinism at the level it would actually be lost.

        The store returns rows ordered by owner, then class, then id. Nothing
        guarantees that order survives a schema change, and under `float` the
        sum of the logarithms would quietly depend on it. Under `Decimal` at a
        pinned precision it does not, and this is where that is checked.
        """
        forward = spread_of(sample)
        backward = spread_of(list(reversed(sample)))

        assert forward == backward


class TestTheSpread:
    """The measure that stops a mean being reported alone."""

    def test_widens_as_the_sample_scatters(self) -> None:
        """Two samples with the same centre and different scatter must differ."""
        tight = spread_of([Decimal("0.9"), Decimal("1.0"), Decimal("1.1")])
        loose = spread_of([Decimal("0.2"), Decimal("1.0"), Decimal("5.0")])

        assert tight is not None
        assert loose is not None
        assert loose.dispersion > tight.dispersion

    @given(sample=samples)
    def test_is_never_negative(self, sample: list[Decimal]) -> None:
        spread = spread_of(sample)

        assert spread is not None
        assert spread.dispersion >= 0

    @given(sample=samples)
    def test_the_band_is_multiplicative_and_contains_the_centre(
        self, sample: list[Decimal]
    ) -> None:
        """A band you divide and multiply by, because the quantity is a multiplier."""
        spread = spread_of(sample)

        assert spread is not None
        assert spread.low <= spread.central <= spread.high
        assert spread.band == (spread.low, spread.high)

    @given(sample=samples)
    def test_the_band_is_only_a_point_when_the_sample_agrees(self, sample: list[Decimal]) -> None:
        """The property that stops the interval being decorative.

        If a scattered sample could report `low == high`, the whole dispersion
        design would be a field nobody reads. `assume` here narrows to samples
        that really do scatter, and the assertion is that the band responds.
        """
        spread = spread_of(sample)
        assert spread is not None
        assume(spread.dispersion > Decimal("0.01"))

        assert spread.low < spread.central < spread.high

    def test_reaches_the_scattered_case_on_a_sample_a_person_can_check(self) -> None:
        """The control for the `assume` above: a hand-written scattered sample.

        A property guarded by `assume` proves nothing if the assumption is never
        satisfiable, so one case that certainly satisfies it is asserted
        directly rather than left to the generator.
        """
        spread = spread_of([Decimal("0.25"), Decimal("1.0"), Decimal("4.0")])

        assert spread is not None
        assert spread.dispersion > Decimal("0.01")
        assert spread.low < spread.central < spread.high

    def test_the_band_widens_by_exactly_one_log_sigma(self) -> None:
        """The constant is a decision, so it is asserted rather than assumed.

        Changing `INTERVAL_LOG_SIGMAS` should fail a test that says what the
        number means, not silently widen every interval the product prints.
        """
        assert Decimal(1) == INTERVAL_LOG_SIGMAS

        spread = spread_of(DOGFOOD)
        assert spread is not None
        with localcontext() as context:
            context.prec = CALIBRATION_PRECISION
            widen = (spread.dispersion * INTERVAL_LOG_SIGMAS).exp()
            assert abs(spread.high - spread.central * widen) <= REPORTED_PLACES

    def test_uses_n_minus_one_so_a_small_sample_is_not_flattered(self) -> None:
        """Dividing by `n` would report a spread biased low.

        Checked against a sample whose two divisors differ visibly: with `n`,
        the deviation of these three would come out about 18% smaller, and the
        estimator whose scatter most needs reporting is the one that would
        benefit.
        """
        spread = spread_of([Decimal("0.5"), Decimal(1), Decimal(2)])

        assert spread is not None
        assert spread.dispersion == Decimal("0.6931")


class TestConfidence:
    """How much of a signal a sample is, in one bounded number."""

    @given(n=st.integers(min_value=0, max_value=500), dispersion=ratios)
    def test_is_always_a_probability(self, n: int, dispersion: Decimal) -> None:
        assert Decimal(0) <= confidence_from(n, dispersion) <= Decimal(1)

    @given(n=st.integers(min_value=1, max_value=500), dispersion=ratios)
    def test_never_reaches_certainty(self, n: int, dispersion: Decimal) -> None:
        """No finite sample of a person's estimates justifies a 1.0, so none is printable."""
        assert confidence_from(n, dispersion) < Decimal(1)

    def test_reaches_one_half_exactly_at_the_refusal_threshold(self) -> None:
        """The tie between the two constants, asserted rather than left to coincidence.

        A group that has only just cleared the threshold deserves to read as a
        coin-flip-grade signal. A formula returning 0.9 there would contradict,
        in a number, the caution the threshold exists to express.
        """
        assert confidence_from(int(CONFIDENCE_HALF_AT), Decimal(0)) == Decimal("0.5")

    @given(
        smaller=st.integers(min_value=1, max_value=200),
        extra=st.integers(min_value=1, max_value=200),
        dispersion=ratios,
    )
    def test_rises_with_the_sample_size(
        self, smaller: int, extra: int, dispersion: Decimal
    ) -> None:
        assert confidence_from(smaller, dispersion) < confidence_from(smaller + extra, dispersion)

    @given(n=st.integers(min_value=1, max_value=200), tight=ratios, extra=ratios)
    def test_falls_as_the_sample_scatters(self, n: int, tight: Decimal, extra: Decimal) -> None:
        """The half of the formula that makes the dispersion decision real.

        If confidence ignored scatter, "widen the interval rather than refuse"
        would be a design that reported a wide band and a confident number
        beside it, which is worse than refusing.
        """
        assert confidence_from(n, tight) > confidence_from(n, tight + extra)

    def test_an_empty_sample_is_no_confidence_rather_than_an_error(self) -> None:
        assert confidence_from(0, Decimal(0)) == Decimal(0)

    def test_refuses_a_negative_deviation(self) -> None:
        """Not reachable through `spread_of`, and cheap to say out loud."""
        with pytest.raises(ValueError, match="cannot be negative"):
            confidence_from(5, Decimal(-1))


class TestTheEdgesOfTheSample:
    """The three inputs that are not a distribution."""

    def test_an_empty_sample_is_an_absence_rather_than_a_summary_of_nothing(self) -> None:
        assert spread_of([]) is None

    def test_one_point_reports_itself_with_a_confidence_that_says_so(self) -> None:
        """Zero dispersion here means unmeasurable, and `confidence` is what says it.

        Safe only because no caller can act on it: `BiasDetective` refuses below
        the threshold, so a one-point summary reaches a listing and never a
        correction.
        """
        spread = spread_of([Decimal(2)])

        assert spread is not None
        assert spread.central == Decimal("2.0000")
        assert spread.dispersion == Decimal("0.0000")
        assert spread.confidence == Decimal("0.1667")

    @pytest.mark.parametrize("bad", [Decimal(0), Decimal(-1)], ids=["zero", "negative"])
    def test_refuses_a_ratio_that_has_no_logarithm(self, bad: Decimal) -> None:
        """Refused rather than dropped, so `n` cannot disagree with what was handed over."""
        with pytest.raises(ValueError, match="must be positive"):
            spread_of([Decimal(1), bad, Decimal(2)])


class TestDeterminism:
    """The property Phase 5's monitor proved for itself, proved for the maths."""

    @given(sample=samples)
    def test_the_same_sample_always_gives_the_same_answer(self, sample: list[Decimal]) -> None:
        assert spread_of(sample) == spread_of(sample)

    @given(sample=samples, precision=st.integers(min_value=6, max_value=60))
    def test_an_ambient_precision_change_moves_nothing(
        self, sample: list[Decimal], precision: int
    ) -> None:
        """Why the context is pinned, checked by breaking the thing it protects against.

        `Decimal.ln` reads a thread-local context any caller may write. A
        determinism property computed under the ambient context would be a
        property about the environment, so this test changes the environment and
        asserts the answer does not move.
        """
        baseline = spread_of(sample)

        original = getcontext().prec
        try:
            getcontext().prec = precision
            assert spread_of(sample) == baseline
        finally:
            getcontext().prec = original

    @given(sample=samples)
    def test_every_reported_quantity_is_rounded_the_same_way(self, sample: list[Decimal]) -> None:
        """Four places everywhere, so the CLI and the eval table cannot disagree."""
        spread = spread_of(sample)

        assert spread is not None
        for value in (spread.central, spread.dispersion, spread.low, spread.high):
            assert value == value.quantize(REPORTED_PLACES)
