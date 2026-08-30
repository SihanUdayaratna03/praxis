"""Grading the calibration half, and the two things that grade can get wrong.

The first is grading a run instead of a store. Every number here is recomputed
from SQLite, so a pass that reported a factor the store refused to hold would
show up as a disagreement rather than as a passing row -- and a test writes
records directly, with no pass having run at all, to prove the grade does not
depend on one.

The second is a claim that passes for the wrong reason. "No factor below the
threshold" is satisfied by a detective that never speaks, so `threshold_holds`
is checked in both directions and there are tests here for a store where groups
really do speak.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from praxis.agents.bias import MINIMUM_SAMPLE, BiasVerdict
from praxis.agents.calibration import calibrate_store
from praxis.agents.classifier import UNCLASSIFIED
from praxis.eval.calibration import CalibrationScore, grade_calibration
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

from tests.agents.test_bias import write_history
from tests.store.conftest import World, build_world

UNDER = [("6", "10.8")] * 6
"""Six estimates at 1.8x. Enough to speak, and clearly biased."""

SHORT = [("6", "10.8")] * 4
"""One short of the threshold. The state this project's own history is in."""


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


class TestTheThreshold:
    """The phase's central claim, graded over whatever the store actually holds."""

    def test_holds_when_every_group_is_short(self, store: Repository, world: World) -> None:
        write_history(store, world, SHORT)

        score = grade_calibration(store)

        assert score.threshold_holds
        assert score.measured == 0

    def test_holds_when_a_group_speaks(self, store: Repository, world: World) -> None:
        """The direction a "never speaks" detective would fail.

        Checked in both directions on purpose: a grade that only asserted "no
        factor below the threshold" would pass against an agent that refused
        everything, which is the failure hardest to notice.
        """
        write_history(store, world, UNDER)

        score = grade_calibration(store)

        assert score.threshold_holds
        assert score.measured == 1

    def test_an_unclassified_group_never_counts_as_measured(
        self, store: Repository, world: World
    ) -> None:
        """Nine of them clear the sample size and still may not speak."""
        write_history(store, world, UNDER + UNDER[:3], work_class=UNCLASSIFIED)

        score = grade_calibration(store)

        assert score.threshold_holds
        assert score.measured == 0
        assert score.by_verdict[BiasVerdict.UNCLASSIFIED.value] == 1

    def test_the_verdict_split_is_reported_rather_than_summarised(
        self, store: Repository, world: World
    ) -> None:
        """Four verdicts are four different facts, so the row keeps them apart."""
        write_history(store, world, UNDER)
        write_history(store, world, SHORT, work_class="refactor")
        write_history(store, world, [("6", None)] * 3, work_class="incident-response")

        split = grade_calibration(store).by_verdict

        assert split[BiasVerdict.MEASURED.value] == 1
        assert split[BiasVerdict.NO_RESOLVED_OUTCOMES.value] == 1
        assert split[BiasVerdict.INSUFFICIENT_SAMPLE.value] >= 1

    def test_an_absent_verdict_is_absent_rather_than_zero(
        self, store: Repository, world: World
    ) -> None:
        """ "No group was unclassified" and "the split was not reported" differ."""
        write_history(store, world, UNDER)

        assert BiasVerdict.UNCLASSIFIED.value not in grade_calibration(store).by_verdict


class TestThePassThrough:
    """It has to fire on exactly the groups that refused. Not most of them."""

    def test_fires_on_every_group_that_refused(self, store: Repository, world: World) -> None:
        write_history(store, world, SHORT)
        write_history(store, world, SHORT, work_class="refactor")

        score = grade_calibration(store)

        assert score.pass_through == score.groups
        assert score.corrected == 0
        assert score.pass_through_exact

    def test_does_not_fire_on_a_group_that_speaks(self, store: Repository, world: World) -> None:
        write_history(store, world, UNDER)
        write_history(store, world, SHORT, work_class="refactor")

        score = grade_calibration(store)

        assert score.corrected == 1
        assert score.pass_through == score.groups - 1
        assert score.pass_through_exact

    def test_the_boundary_is_exact_and_not_approximate(
        self, store: Repository, world: World
    ) -> None:
        """A group one either side of the threshold, in one store.

        A pass-through firing one group early is silently ignoring a factor; one
        firing late is applying a factor that does not exist. Only an exact
        comparison catches either.
        """
        write_history(store, world, [("6", "10.8")] * MINIMUM_SAMPLE)
        write_history(store, world, [("6", "10.8")] * (MINIMUM_SAMPLE - 1), work_class="refactor")

        score = grade_calibration(store)

        assert score.corrected == 1
        assert score.pass_through_exact


class TestTheBacktest:
    """Reported with its denominator, because the zero means two things."""

    def test_a_corpus_below_the_threshold_grades_nothing(
        self, store: Repository, world: World
    ) -> None:
        write_history(store, world, SHORT)

        backtest = grade_calibration(store).backtest

        assert not backtest.graded
        assert backtest.scored == 0

    def test_a_long_enough_history_is_graded(self, store: Repository, world: World) -> None:
        """The control: the ungraded case above is a fact about the corpus."""
        write_history(store, world, [("6", "10.8")] * 9)

        backtest = grade_calibration(store).backtest

        assert backtest.graded
        assert backtest.scored == 4


class TestItIsGradedFromTheStore:
    """A run reports what the agents produced; the store is what survived."""

    def test_the_grade_needs_no_pass_to_have_run(self, store: Repository, world: World) -> None:
        """Records written directly, no `calibrate_store` anywhere.

        The number that would differ if this were graded from a run is
        `findings`, and it is zero here precisely because nothing wrote any.
        """
        write_history(store, world, UNDER)

        score = grade_calibration(store)

        assert score.measured == 1
        assert score.findings == 0

    def test_findings_are_counted_after_a_pass(self, store: Repository, world: World) -> None:
        write_history(store, world, UNDER)
        calibrate_store(store)

        assert grade_calibration(store).findings == 1

    def test_only_calibration_findings_are_counted(self, store: Repository, world: World) -> None:
        """The world fixture plants an assumption breach, which is not this row."""
        write_history(store, world, UNDER)
        calibrate_store(store)

        assert grade_calibration(store).findings == 1

    def test_an_empty_store_grades_without_failing(self, store: Repository) -> None:
        score = grade_calibration(store)

        assert score == CalibrationScore()
        assert score.groups == 0
        assert score.measured_rate == Decimal(0)

    def test_the_measured_rate_is_a_proportion_of_groups(
        self, store: Repository, world: World
    ) -> None:
        write_history(store, world, UNDER)
        write_history(store, world, SHORT, work_class="refactor")

        score = grade_calibration(store)

        assert score.refused == score.groups - score.measured
        assert Decimal(0) <= score.measured_rate <= Decimal(1)
