"""What the calibrator does with a factor, and what it does without one.

The second property this phase is judged on lives here: a calibrated estimate is
always traceable to the exact factor and `n` it was computed from. So does the
third: a correction never produces a quantity of zero or less, for any factor
and any raw estimate the generators can reach.

The pass-through path gets as many tests as the correction path, deliberately.
It is the answer six of this project's own seven estimates would get, and a
suite that tested it once would be describing it as the exception it is not.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.agents.bias import (
    MINIMUM_SAMPLE,
    BiasVerdict,
    CalibrationFactor,
    CalibrationGroup,
    summarise,
)
from praxis.agents.calibrator import (
    MINIMUM_CORRECTED,
    CalibratorAgent,
    apply,
)
from praxis.agents.classifier import UNCLASSIFIED
from praxis.domain.enums import Unit
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

from tests.agents.test_bias import (
    DOGFOOD,
    GROUP,
    OWNER,
    WORK_CLASS,
    a_row,
    quantities,
    resolved_rows,
    write_history,
)
from tests.store.conftest import World, build_world

FIFTH = a_row("5.5", "3.4", index=5)
"""The outcome that takes this project's own class over the threshold."""

SPEAKS = summarise(GROUP, [*DOGFOOD, FIFTH])
"""A verdict that speaks, built from the real history plus one point."""

REFUSES = summarise(GROUP, DOGFOOD)
"""A verdict that does not, built from the real history as it stands."""

verdicts = st.lists(resolved_rows, min_size=MINIMUM_SAMPLE, max_size=20).map(
    lambda rows: summarise(GROUP, rows)
)
"""Speaking verdicts over a wide range of factors, for the correction properties."""


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


@pytest.fixture
def world(store: Repository) -> World:
    return build_world(store)


class TestThePassThrough:
    """The primary path, tested like one."""

    def test_an_estimate_with_no_factor_comes_back_unchanged(self) -> None:
        result = apply(REFUSES, quantity=Decimal("5.5"), unit=Unit.HOURS)

        assert result.corrected == Decimal("5.5")
        assert not result.adjusted

    def test_it_is_an_answer_rather_than_an_error(self) -> None:
        """Fully populated, with the verdict attached. Nothing here is `None`."""
        result = apply(REFUSES, quantity=Decimal("5.5"), unit=Unit.HOURS)

        assert result.owner == OWNER
        assert result.work_class == WORK_CLASS
        assert result.unit is Unit.HOURS
        assert result.n == MINIMUM_SAMPLE - 1
        assert result.basis.verdict is BiasVerdict.INSUFFICIENT_SAMPLE
        assert result.explanation

    def test_it_says_the_unchanged_estimate_is_correct_rather_than_broken(self) -> None:
        """The sentence that stops a reader learning to see this as breakage."""
        result = apply(REFUSES, quantity=Decimal("5.5"), unit=Unit.HOURS)

        assert "unchanged" in result.explanation
        assert "correct answer" in result.explanation

    def test_it_carries_the_detectives_own_reason_rather_than_paraphrasing_it(self) -> None:
        """One explanation of the refusal, in one place, quoted not restated."""
        result = apply(REFUSES, quantity=Decimal("5.5"), unit=Unit.HOURS)

        assert REFUSES.reason in result.explanation

    def test_no_band_is_offered_when_there_is_no_factor(self) -> None:
        """A band implies a distribution, and there isn't one."""
        result = apply(REFUSES, quantity=Decimal("5.5"), unit=Unit.HOURS)

        assert result.low is None
        assert result.high is None
        assert result.factor is None

    def test_an_unclassified_group_passes_through_with_its_own_reason(self) -> None:
        verdict = summarise(
            CalibrationGroup(OWNER, UNCLASSIFIED),
            [a_row("6", "9", index=i, work_class=UNCLASSIFIED) for i in range(9)],
        )

        result = apply(verdict, quantity=Decimal(6), unit=Unit.WEEKS)

        assert not result.adjusted
        assert "belong to no group" in result.explanation

    @given(quantity=quantities)
    def test_nothing_is_ever_changed_by_a_verdict_that_does_not_speak(
        self, quantity: Decimal
    ) -> None:
        """For any raw estimate at all, a refusal leaves it exactly alone."""
        result = apply(REFUSES, quantity=quantity, unit=Unit.HOURS)

        assert result.corrected == quantity
        assert not result.adjusted

    def test_a_zero_estimate_is_not_scaled_even_when_a_factor_exists(self) -> None:
        """Scaling a zero leaves a zero, and a zero estimate predicts nothing.

        `Estimate` says that in a validator. Reporting a "correction" of zero to
        zero would be arithmetic that looks like calibration and is not.
        """
        result = apply(SPEAKS, quantity=Decimal(0), unit=Unit.HOURS)

        assert result.corrected == Decimal(0)
        assert not result.adjusted
        assert "nothing to apply it to" in result.explanation


class TestTheCorrection:
    """What happens when there is a factor to apply."""

    def test_applies_the_factor_the_detective_computed(self) -> None:
        result = apply(SPEAKS, quantity=Decimal("5.5"), unit=Unit.HOURS)

        assert SPEAKS.factor is not None
        assert result.corrected == (Decimal("5.5") * SPEAKS.factor).quantize(Decimal("0.0001"))
        assert result.adjusted

    def test_this_projects_own_history_would_scale_phase_seven_down(self) -> None:
        """The worked example this phase's own estimate declined to apply.

        `EST-0008` was logged at 5.5h uncorrected. Had the fifth outcome existed,
        the product would have said 3.36h. Pinned so the number in the phase
        report is the one the code produces.
        """
        result = apply(SPEAKS, quantity=Decimal("5.5"), unit=Unit.HOURS)

        assert result.corrected == Decimal("3.3649")

    def test_maps_the_raw_estimate_onto_the_band(self) -> None:
        """The band is what stops a point correction being read as a promise."""
        result = apply(SPEAKS, quantity=Decimal("5.5"), unit=Unit.HOURS)

        assert result.low is not None
        assert result.high is not None
        assert result.low < result.corrected < result.high

    def test_the_explanation_cites_n_and_the_confidence_basis(self) -> None:
        """A correction a reader cannot argue with is one they follow blindly."""
        result = apply(SPEAKS, quantity=Decimal("5.5"), unit=Unit.HOURS)

        assert f"n={SPEAKS.n}" in result.explanation
        assert f"confidence={SPEAKS.confidence}" in result.explanation
        assert "log-space spread" in result.explanation

    def test_the_explanation_reads_the_direction_in_words_a_person_uses(self) -> None:
        """ "1.6345x over", not "0.6118x" -- and the raw factor is there too."""
        result = apply(SPEAKS, quantity=Decimal("5.5"), unit=Unit.HOURS)

        assert f"{SPEAKS.magnitude}x over" in result.explanation
        assert f"scaled by {SPEAKS.factor}x" in result.explanation

    def test_a_calibrated_estimator_is_told_so_rather_than_scaled_silently(self) -> None:
        """Inside the neutral band there is no direction, and the prose must not invent one."""
        flat = summarise(GROUP, [a_row("6", "6", index=i) for i in range(MINIMUM_SAMPLE)])

        result = apply(flat, quantity=Decimal(6), unit=Unit.WEEKS)

        assert "came in close to what was predicted" in result.explanation


class TestTraceability:
    """The second property: an output always names the factor and `n` behind it."""

    @given(verdict=verdicts, quantity=quantities)
    def test_every_correction_names_the_exact_factor_it_used(
        self, verdict: CalibrationFactor, quantity: Decimal
    ) -> None:
        """Read through `basis` rather than copied, so the two cannot drift."""
        result = apply(verdict, quantity=quantity, unit=Unit.HOURS)

        assert result.factor == verdict.factor
        assert result.n == verdict.n
        assert result.basis is verdict

    @given(verdict=verdicts, quantity=quantities)
    def test_the_stated_factor_reproduces_the_stated_number(
        self, verdict: CalibrationFactor, quantity: Decimal
    ) -> None:
        """The strongest form of traceable: recompute the answer from what it cites.

        A reader with the raw estimate and the cited factor must arrive at the
        corrected figure. If the explanation ever cited one number while the
        arithmetic used another, this is what would catch it.
        """
        result = apply(verdict, quantity=quantity, unit=Unit.HOURS)
        assert result.factor is not None

        recomputed = (result.raw * result.factor).quantize(Decimal("0.0001"))

        assert result.corrected == max(recomputed, MINIMUM_CORRECTED)

    @given(verdict=verdicts, quantity=quantities)
    def test_an_explanation_is_always_produced(
        self, verdict: CalibrationFactor, quantity: Decimal
    ) -> None:
        assert apply(verdict, quantity=quantity, unit=Unit.HOURS).explanation.strip()

    @given(verdict=verdicts, quantity=quantities)
    def test_the_sample_size_is_always_stated(
        self, verdict: CalibrationFactor, quantity: Decimal
    ) -> None:
        """`n` is what a reader weighs the correction by, so it is never omitted."""
        result = apply(verdict, quantity=quantity, unit=Unit.HOURS)

        assert f"n={result.n}" in result.explanation


class TestACorrectionIsAlwaysAQuantity:
    """The third property: never zero, never negative, whatever goes in."""

    @given(verdict=verdicts, quantity=quantities)
    def test_a_corrected_estimate_is_always_positive(
        self, verdict: CalibrationFactor, quantity: Decimal
    ) -> None:
        result = apply(verdict, quantity=quantity, unit=Unit.HOURS)

        assert result.corrected > 0
        assert result.low is not None
        assert result.high is not None
        assert result.low > 0
        assert result.high > 0

    def test_a_tiny_estimate_and_a_tiny_factor_do_not_round_away(self) -> None:
        """The case the generators reach and a hand-written example would not.

        At four reported places a small enough estimate scaled by a small enough
        factor rounds to zero, and a zero estimate predicts nothing -- so the
        result is held at the smallest figure the precision can express rather
        than handed back as a zero nobody computed.
        """
        steep = summarise(GROUP, [a_row("500", "0.1", index=i) for i in range(MINIMUM_SAMPLE)])

        result = apply(steep, quantity=Decimal("0.1"), unit=Unit.HOURS)

        assert result.corrected == MINIMUM_CORRECTED
        assert result.corrected > 0

    def test_a_negative_estimate_is_refused_rather_than_corrected(self, store: Repository) -> None:
        """Not a quantity, so not something to calibrate."""
        with pytest.raises(ValueError, match="cannot be negative"):
            CalibratorAgent(store).calibrate(
                owner=OWNER, work_class=WORK_CLASS, quantity=Decimal(-1), unit=Unit.HOURS
            )


class TestOverAStore:
    """The agent, rather than the function, against records that were really written."""

    def test_passes_through_when_the_store_is_short_of_the_threshold(
        self, store: Repository, world: World
    ) -> None:
        write_history(store, world, [("6", "9") for _ in range(4)])

        result = CalibratorAgent(store).calibrate(
            owner=OWNER, work_class=WORK_CLASS, quantity=Decimal(6), unit=Unit.WEEKS
        )

        assert not result.adjusted
        assert result.n == 4

    def test_corrects_once_the_store_holds_enough(self, store: Repository, world: World) -> None:
        write_history(store, world, [("6", "10.8") for _ in range(6)])

        result = CalibratorAgent(store).calibrate(
            owner=OWNER, work_class=WORK_CLASS, quantity=Decimal(6), unit=Unit.WEEKS
        )

        assert result.adjusted
        assert result.corrected == Decimal("10.8000")
        assert result.n == 6

    def test_a_person_with_no_history_is_answered_rather_than_failed(
        self, store: Repository
    ) -> None:
        result = CalibratorAgent(store).calibrate(
            owner="somebody new", work_class="migration", quantity=Decimal(3), unit=Unit.WEEKS
        )

        assert not result.adjusted
        assert result.corrected == Decimal(3)
        assert result.explanation

    def test_two_calls_over_an_unchanged_store_agree(self, store: Repository, world: World) -> None:
        """Determinism, at the level a caller would notice it."""
        write_history(store, world, [("6", "10.8") for _ in range(6)])
        agent = CalibratorAgent(store)

        first = agent.calibrate(
            owner=OWNER, work_class=WORK_CLASS, quantity=Decimal(6), unit=Unit.WEEKS
        )
        second = agent.calibrate(
            owner=OWNER, work_class=WORK_CLASS, quantity=Decimal(6), unit=Unit.WEEKS
        )

        assert first == second

    def test_the_unit_is_carried_and_never_converted(self, store: Repository, world: World) -> None:
        """Conversion is `reconciliation`'s job and happens at write time, not here."""
        write_history(store, world, [("6", "10.8") for _ in range(6)])

        result = CalibratorAgent(store).calibrate(
            owner=OWNER, work_class=WORK_CLASS, quantity=Decimal(6), unit=Unit.POINTS
        )

        assert result.unit is Unit.POINTS
        assert "points" in result.explanation
