"""Would the correction have helped? And the two ways that question can lie.

The first lie is fitting on the point being scored, which this design forbids by
walking forward -- and which is checked here by a test that would pass under the
dishonest version and fails under it only because a second test pins what the
honest one produces.

The second lie is a rate with no denominator. A backtest that scored nothing and
one that scored badly are different facts and `score` alone cannot tell them
apart, so `graded` is asserted everywhere a score is.

The last class backtests this project's own seven outcomes. It is a **curiosity
and not a result**, and the honest thing it produces is a zero: walking forward,
no group ever reaches the threshold, so no correction is ever formed to grade.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.agents.bias import MINIMUM_SAMPLE, CalibrationGroup
from praxis.agents.scoring import Backtest, ScoringAgent, total, walk
from praxis.domain.enums import MatchQuality, Unit
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.reports import CalibrationRow
from praxis.store.repository import Repository

from tests.agents.test_bias import (
    GROUP,
    OWNER,
    WORK_CLASS,
    a_row,
    resolved_rows,
    write_history,
)
from tests.store.conftest import World, build_world

DOGFOOD_DIR = Path(__file__).resolve().parents[2] / "docs" / "dogfood"

UNDER_BY = Decimal("1.8")
"""A consistent under-estimator: every actual is 1.8x the estimate."""


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


def consistent(count: int, *, wobble: Decimal = Decimal("0.3")) -> list[CalibrationRow]:
    """A history with a real bias and a little noise around it.

    The wobble matters: a perfectly consistent estimator makes the correction
    trivially right and would prove nothing about the arithmetic. The noise
    alternates sign so the underlying factor stays 1.8x.
    """
    return [
        a_row("6", str(Decimal(6) * UNDER_BY + (wobble if index % 2 else -wobble)), index=index)
        for index in range(count)
    ]


class TestTheWalkIsForward:
    """A factor never sees the row it is grading."""

    def test_nothing_is_scored_before_the_threshold_is_reached(self) -> None:
        """The first four rows join the history and are never graded on it."""
        result = walk(GROUP, consistent(MINIMUM_SAMPLE))

        assert result.considered == MINIMUM_SAMPLE
        assert result.scored == 0
        assert not result.graded

    def test_the_first_scored_row_is_the_one_after_the_threshold(self) -> None:
        """Five rows of history, the sixth graded. Not the fifth."""
        result = walk(GROUP, consistent(MINIMUM_SAMPLE + 1))

        assert result.considered == MINIMUM_SAMPLE + 1
        assert result.scored == 1
        assert result.graded

    @given(count=st.integers(min_value=0, max_value=30))
    def test_scored_is_always_the_rows_past_the_threshold(self, count: int) -> None:
        """`considered - scored` is exactly the prefix that had nothing to use."""
        result = walk(GROUP, consistent(count))

        assert result.scored == max(0, count - MINIMUM_SAMPLE)
        assert result.considered == count

    def test_an_unresolved_row_neither_scores_nor_teaches(self) -> None:
        """A row with no actual says nothing about a factor, so it is not history."""
        rows = [*consistent(MINIMUM_SAMPLE), a_row("6", None, index=98), *consistent(1)]

        result = walk(GROUP, rows)

        assert result.considered == MINIMUM_SAMPLE + 1
        assert result.scored == 1

    def test_the_counts_always_add_up(self) -> None:
        result = walk(GROUP, consistent(12))

        assert result.improved + result.worsened + result.unchanged == result.scored


class TestWhatItFinds:
    """The measurement, against histories whose answer is known in advance."""

    def test_a_consistent_bias_is_corrected_on_every_scored_row(self) -> None:
        """The case calibration exists for, and the strongest signal available."""
        result = walk(GROUP, consistent(12))

        assert result.score == Decimal("1.0000")
        assert result.worsened == 0
        assert result.corrected_error < result.raw_error

    def test_the_error_falls_by_a_visible_margin(self) -> None:
        """Rate and magnitude are separate claims, so both are checked."""
        result = walk(GROUP, consistent(12))

        assert result.raw_error > Decimal("0.5")
        assert result.corrected_error < Decimal("0.1")
        assert result.error_reduction > Decimal("0.4")

    def test_a_well_calibrated_estimator_is_not_improved_by_correcting_them(self) -> None:
        """Correcting someone who is already right should not look like a win.

        A backtest that scored well here would be measuring its own arithmetic
        rather than a bias, so this is the control on the test above.
        """
        exact = [a_row("6", "6", index=index) for index in range(12)]

        result = walk(GROUP, exact)

        assert result.graded
        assert result.improved == 0
        assert result.error_reduction == Decimal(0)

    def test_noise_around_no_bias_does_not_score_well(self) -> None:
        """Scatter with no direction gives a correction nothing to find.

        The ratios here run from 0.5x to 2x with a geometric mean near one, so a
        factor fitted to the prefix is fitting noise. Anything close to 1.0
        would mean the walk was cheating.
        """
        scattered = ["3", "12", "5", "9", "4", "11", "7", "6", "13", "3.5", "10", "8"]
        rows = [a_row("6", actual, index=index) for index, actual in enumerate(scattered)]

        result = walk(GROUP, rows)

        assert result.graded
        assert result.score < Decimal("0.8")

    def test_a_correction_that_changes_nothing_is_counted_apart(self) -> None:
        """Neither evidence for nor against, so it is not folded into either."""
        exact = [a_row("6", "6", index=index) for index in range(9)]

        result = walk(GROUP, exact)

        assert result.unchanged == result.scored
        assert result.improved == result.worsened == 0

    def test_being_twice_over_and_twice_under_are_the_same_size_of_miss(self) -> None:
        """Log-space error, checked where an absolute difference would disagree.

        In hours, a 6-hour estimate answered by 12 is 6 hours out and one
        answered by 3 is 3 hours out. In the space a multiplier lives in they
        are the same miss, and this is a history where those two alternate: a
        correction has nothing to bite on and the mean errors say so.
        """
        symmetric = [a_row("6", "12" if index % 2 else "3", index=index) for index in range(12)]

        result = walk(GROUP, symmetric)

        assert result.graded
        assert result.raw_error == Decimal("0.6931")


class TestARateNeedsItsDenominator:
    """`graded` beside `score`, everywhere, because 0.0 means two things."""

    def test_an_ungraded_backtest_scores_zero_and_says_it_is_ungraded(self) -> None:
        result = walk(GROUP, consistent(3))

        assert result.score == Decimal(0)
        assert not result.graded

    def test_a_graded_backtest_that_helped_nobody_also_scores_zero(self) -> None:
        """The other meaning of the same number. Only `graded` separates them."""
        exact = [a_row("6", "6", index=index) for index in range(9)]

        result = walk(GROUP, exact)

        assert result.score == Decimal(0)
        assert result.graded

    def test_an_empty_history_is_answered_rather_than_divided_by_zero(self) -> None:
        result = walk(GROUP, [])

        assert result == Backtest(group=GROUP)
        assert result.score == Decimal(0)
        assert not result.graded

    @given(rows=st.lists(resolved_rows, min_size=0, max_size=25))
    def test_the_score_is_always_a_proportion(self, rows: list[CalibrationRow]) -> None:
        result = walk(GROUP, rows)

        assert Decimal(0) <= result.score <= Decimal(1)

    @given(rows=st.lists(resolved_rows, min_size=0, max_size=25))
    def test_both_errors_are_zero_when_nothing_was_scored(self, rows: list[CalibrationRow]) -> None:
        """No scored rows means no error to report, not an error of zero.

        `graded` is the field that says which, and this holds the invariant that
        keeps the two readable: an ungraded row never carries a number that
        would be mistaken for a measurement.
        """
        result = walk(GROUP, rows)

        if not result.graded:
            assert result.raw_error == result.corrected_error == Decimal(0)


class TestOverAStore:
    """The agent, against records that were really written."""

    def test_walks_each_group_separately(self, store: Repository, world: World) -> None:
        """A correction is formed within a group, so grades are never pooled."""
        write_history(store, world, [("6", "10.8") for _ in range(9)])
        write_history(store, world, [("6", "10.8") for _ in range(9)], work_class="refactor")

        results = ScoringAgent(store).backtest()
        graded = [result for result in results if result.graded]

        assert len(graded) == 2
        assert all(result.scored == 4 for result in graded)

    def test_ungraded_groups_are_returned_rather_than_filtered(
        self, store: Repository, world: World
    ) -> None:
        """How many groups had nothing to score is the finding, not the noise."""
        write_history(store, world, [("6", "10.8") for _ in range(9)])
        write_history(store, world, [("6", "10.8")], work_class="refactor")

        results = ScoringAgent(store).backtest()

        assert any(not result.graded for result in results)

    def test_one_group_can_be_asked_about_directly(self, store: Repository, world: World) -> None:
        write_history(store, world, [("6", "10.8") for _ in range(9)])

        result = ScoringAgent(store).backtest_group(owner=OWNER, work_class=WORK_CLASS)

        assert result.group == CalibrationGroup(OWNER, WORK_CLASS)
        assert result.scored == 4

    def test_a_total_adds_the_counts_and_weights_the_errors(
        self, store: Repository, world: World
    ) -> None:
        """A group with four scored rows must not weigh the same as one with one."""
        write_history(store, world, [("6", "10.8") for _ in range(9)])
        write_history(store, world, [("6", "10.8") for _ in range(6)], work_class="refactor")

        results = ScoringAgent(store).backtest()
        summed = total(results)

        assert summed.group is None
        assert summed.scored == sum(result.scored for result in results)
        assert summed.considered == sum(result.considered for result in results)

    def test_a_total_over_nothing_is_an_empty_row_rather_than_a_failure(self) -> None:
        assert total([]) == Backtest()

    def test_re_running_over_an_unchanged_store_gives_an_identical_answer(
        self, store: Repository, world: World
    ) -> None:
        write_history(store, world, [("6", "10.8") for _ in range(9)])
        agent = ScoringAgent(store)

        assert agent.backtest() == agent.backtest()


class TestThisProjectsOwnHistory:
    """A curiosity at n = 10, reported as one. Two rows now graded.

    Ten outcomes across four classes, with the largest -- `agent-implementation`
    -- at seven. Six is where this project's own history became *scoreable*:
    `BiasDetective` needs five resolved estimates to speak, and a prequential
    walk needs five *before* the row it is grading, so it needs six. Seven grades
    two.

    **These assertions are pinned to the real log on purpose, and they are
    supposed to fail when it grows.** They did at the close of Phase 7, when
    `OUT-0008` took the class from four to five, and again at the close of Phase
    8, when `OUT-0009` took it to six. That is the failure mode they exist for: a
    change to `docs/dogfood/` is a change to fixture data, and the pin is what
    makes it visible rather than silent.

    **`OUT-0010` is the row worth reading**, and it is the uncomfortable one. It
    is the second bias-corrected estimate in the log and it came in **1.8000x
    over** -- to four figures, the very factor that was applied to it. That is
    exactly the shape that would argue for correcting twice, and it is refused:
    one row is not a trend, and the prequential walk below is what answers the
    question. The walk says the correction **helped on both graded rows**, more
    than halving the error, which is what "correct once" predicts and
    double-correcting would not.

    **`OUT-0009` also broke something the pins were not designed to catch, and
    that is the more useful thing recorded here.** `EST-0009` is the first
    estimate in this log to have had a bias correction applied before it was
    logged, so its `active_quantity` is *already* corrected. A prequential
    backtest asks whether applying the correction would have helped, which means
    it must be handed the **uncorrected** figure -- and handed the corrected one
    it applied the factor a second time and reported the correction as harmful
    (`improved=0, worsened=1`). The raw number was never lost, but it lived only
    in `calibration_note` prose where nothing could read it. It is now
    `raw_active_quantity`, `dogfood_rows` prefers it, and the walk answers the
    question it was actually asking.
    """

    @staticmethod
    def dogfood_rows() -> list[CalibrationRow]:
        """This project's estimates beside its outcomes, read from the log.

        Loaded here rather than by anything in `praxis/`: the package has no
        business knowing where this repository keeps its own diary, and the
        Phase 12 self-analysis is supposed to ingest these files through the
        ordinary path rather than through a private reader.
        """
        estimates = {record["id"]: record for record in _jsonl(DOGFOOD_DIR / "estimates.jsonl")}
        rows = []
        for outcome in _jsonl(DOGFOOD_DIR / "outcomes.jsonl"):
            estimate = estimates[outcome["estimate_id"]]
            rows.append(
                CalibrationRow(
                    estimate_id=estimate["id"],
                    owner=estimate["owner"],
                    work_class=estimate["work_class"],
                    unit=Unit(estimate["unit"]),
                    subject=estimate["subject"][:80],
                    # `raw_active_quantity` first, and this is the whole point
                    # of that field. A backtest asks whether applying the
                    # correction would have helped, so it has to be handed the
                    # *uncorrected* number. EST-0009 is the first row in this
                    # log whose `active_quantity` is already corrected; reading
                    # that as raw makes the walk apply the factor twice and
                    # report the correction as harmful. Rows with no such field
                    # were never corrected, so their `active_quantity` is raw.
                    estimated_active=Decimal(
                        str(
                            estimate.get("raw_active_quantity")
                            or estimate.get("active_quantity", estimate.get("quantity"))
                        )
                    ),
                    estimated_blocked=Decimal(str(estimate.get("blocked_quantity", 0))),
                    outcome_id=outcome["id"],
                    actual_active=Decimal(str(outcome["active_quantity"])),
                    actual_blocked=Decimal(str(outcome["blocked_quantity"])),
                    match_quality=MatchQuality(outcome["match_quality"]),
                )
            )
        return rows

    def test_the_log_holds_ten_outcomes_across_four_classes(self) -> None:
        """The premise, checked rather than recalled, so the conclusion is real."""
        rows = self.dogfood_rows()

        assert len(rows) == 10
        assert len({row.work_class for row in rows}) == 4

    def test_the_largest_class_is_two_past_the_threshold(self) -> None:
        """`OUT-0009` took it to six and `OUT-0010` to seven.

        Six is where the scorer grades its first row -- at five the detective can
        speak, at six the walk has five rows *before* one to grade. Seven grades
        a second, which is the first time the correction has been tested rather
        than illustrated.
        """
        rows = self.dogfood_rows()
        by_class: dict[str, int] = {}
        for row in rows:
            by_class[row.work_class] = by_class.get(row.work_class, 0) + 1

        assert max(by_class.values()) == MINIMUM_SAMPLE + 2
        assert by_class["agent-implementation"] == MINIMUM_SAMPLE + 2

    def test_backtesting_this_projects_history_now_scores_two_rows(self) -> None:
        """Two rows. Still not evidence, and closer to it than one was.

        Worth stating precisely, because the number is easy to over-read. Two
        scored rows are two data points that could have gone either way and did
        not; they are not a demonstration that the correction is the right size.
        The class that used to assert "scores exactly nothing" is this one,
        repinned twice.
        """
        rows = sorted(self.dogfood_rows(), key=lambda row: row.estimate_id)
        results = [
            walk(
                CalibrationGroup(owner, work_class),
                [r for r in rows if r.group == (owner, work_class)],
            )
            for owner, work_class in sorted({row.group for row in rows})
        ]

        summed = total(results)

        assert summed.considered == 10
        assert summed.scored == 2
        assert summed.graded

    def test_both_backtested_rows_say_the_correction_helped(self) -> None:
        """The question `OUT-0010` made answerable, as arithmetic rather than a hope.

        `EST-0010` was corrected and still came in 1.8000x over, which reads as
        an argument for correcting twice. The walk is what settles it: over both
        graded rows the corrected error is well under the raw one and neither row
        was made worse, so the correction is helping at the size it is applied.
        A second application would be fitted to two observations, and the honest
        response to a factor that keeps under-correcting is to let the *factor*
        move as the sample grows -- which is what the next `summarise` does on
        its own, without anybody deciding to double anything.

        **Pinned as `improved` and not as the numbers**, deliberately. The exact
        errors move whenever the log grows and pinning them would make this test
        a tripwire for arithmetic that is already property-tested elsewhere. What
        is worth holding is the direction and the fact that anything is graded
        at all.
        """
        rows = [row for row in self.dogfood_rows() if row.work_class == "agent-implementation"]
        group = CalibrationGroup(rows[0].owner, "agent-implementation")

        result = walk(group, rows)

        assert result.scored == 2
        assert result.improved == 2
        assert result.worsened == 0
        assert result.corrected_error < result.raw_error

    def test_the_corrected_estimate_is_not_what_the_backtest_is_handed(self) -> None:
        """The trap `OUT-0009` sprang, held open so nobody falls in it twice.

        `EST-0009` carries both figures: `active_quantity` is the 3.6h that was
        actually predicted, and `raw_active_quantity` is the 6.5h the correction
        was applied to. The backtest must read the second. If this ever reads the
        first again, the walk will double-correct and report a working correction
        as a harmful one.
        """
        rows = {row.estimate_id: row for row in self.dogfood_rows()}

        assert rows["EST-0009"].estimated_active == Decimal("6.5")


def _jsonl(path: Path) -> list[dict[str, object]]:
    """Every record in a JSONL file, blank lines skipped."""
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
