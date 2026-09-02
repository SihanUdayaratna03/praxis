"""What this project's own calibration history says, pinned so it cannot drift.

`docs/reports/phase-12.md` quotes these numbers. They are computed from the
seeded store rather than written into the report by hand, so a number that moves
breaks a test instead of quietly disagreeing with the prose.

Repinned when an outcome lands. That is the corpus growing, not the test
breaking -- the same note `test_seed.py` carries for `frontend`.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from decimal import Decimal
from pathlib import Path

import praxis
import pytest
from praxis.agents.bias import MINIMUM_SAMPLE, BiasDetective, CalibrationGroup, summarise
from praxis.agents.distribution import precise, ratio_of, spread_of
from praxis.demo.seed import seed
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.reports import CalibrationRow
from praxis.store.repository import Repository

REPO = Path(praxis.__file__).resolve().parent.parent
ADRS = REPO / "docs" / "adr"
DOGFOOD = REPO / "docs" / "dogfood"

ONE = Decimal(1)


@pytest.fixture
def resolved() -> Iterator[list[CalibrationRow]]:
    """Every dogfood estimate an outcome has answered, seeded from the files."""
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    seed(repository, adr_dir=ADRS, dogfood_dir=DOGFOOD)
    yield [row for row in repository.calibration_history() if row.resolved]
    repository.close()


def ratios_of(rows: Sequence[CalibrationRow]) -> list[Decimal]:
    """Active against active, the comparison `BiasDetective` makes."""
    return [
        ratio
        for row in rows
        if row.actual_active is not None
        and (ratio := ratio_of(row.estimated_active, row.actual_active)) is not None
    ]


def log_error(predicted: Decimal, actual: Decimal) -> Decimal:
    """How far a prediction was out, in log space, unsigned."""
    with precise():
        return abs((actual / predicted).ln())


def leave_one_out(rows: Sequence[CalibrationRow], *, by_class: bool) -> Decimal:
    """Mean absolute log error predicting each row from all the others.

    `by_class` picks the key: ADR 0024's `(owner, work_class)` group, refusing
    below `MINIMUM_SAMPLE` and falling back to no correction, or one pooled
    factor over every class at once.
    """
    total = Decimal(0)
    for held_out in rows:
        others = [row for row in rows if row.estimate_id != held_out.estimate_id]
        if by_class:
            others = [row for row in others if row.work_class == held_out.work_class]
        sample = ratios_of(others)
        spread = spread_of(sample) if len(sample) >= MINIMUM_SAMPLE or not by_class else None
        factor = ONE if spread is None else spread.central
        total += log_error(factor, ratios_of([held_out])[0])
    with precise():
        return +(total / Decimal(len(rows)))


class TestWhichWayTheEstimatorIsWrong:
    """`OUT-0012` said every outcome was an over-estimate. It is not true."""

    def test_nine_outcomes_are_over_and_three_are_under(self, resolved):
        ratios = ratios_of(resolved)
        assert len(ratios) == 12
        assert sum(1 for r in ratios if r < ONE) == 9
        assert sum(1 for r in ratios if r > ONE) == 3

    def test_the_three_under_estimates_are_named(self, resolved):
        """Two whole classes and one row inside the class that speaks."""
        under = {
            row.work_class
            for row in resolved
            if row.actual_active is not None and row.actual_active > row.estimated_active
        }
        assert under == {"data-modelling", "llm-integration", "agent-implementation"}


class TestWhetherTheGroupingKeyEarnsItsPlace:
    """ADR 0024 groups by work class. Three measurements of whether it should."""

    def test_the_pooled_band_contains_one_and_the_class_band_does_not(self, resolved):
        """The class group establishes a direction. Pooled, nothing does."""
        pooled = spread_of(ratios_of(resolved))
        agents = spread_of(
            ratios_of([row for row in resolved if row.work_class == "agent-implementation"])
        )
        assert pooled is not None
        assert agents is not None
        assert pooled.low < ONE < pooled.high
        assert agents.high < ONE

    def test_the_class_group_scatters_less_than_the_pool(self, resolved):
        pooled = spread_of(ratios_of(resolved))
        agents = spread_of(
            ratios_of([row for row in resolved if row.work_class == "agent-implementation"])
        )
        assert pooled is not None
        assert agents is not None
        assert agents.dispersion < pooled.dispersion

    def test_any_correction_beats_no_correction(self, resolved):
        """And the two keys are close enough together to be noise at this n."""
        ratios = ratios_of(resolved)
        with precise():
            raw = +(sum((log_error(ONE, r) for r in ratios), Decimal(0)) / Decimal(len(ratios)))
        pooled = leave_one_out(resolved, by_class=False)
        per_class = leave_one_out(resolved, by_class=True)
        assert pooled < raw
        assert per_class < raw
        assert abs(per_class - pooled) < Decimal("0.05")

    def test_the_only_group_that_speaks_is_still_agent_implementation(self, resolved):
        """`EST-0013` takes `scaffolding` to two estimates and it still refuses."""
        rows = list(resolved)
        group = CalibrationGroup(owner="claude-opus-5", work_class="scaffolding")
        assert summarise(group, [r for r in rows if r.work_class == "scaffolding"]).speaks is False


class TestWhetherTheCorrectionWorked:
    """`EST-0009` onward applied a factor. `EST-0004`-`0008` did not."""

    def test_corrected_estimates_land_closer_than_uncorrected_ones(self, resolved):
        corrected = {"EST-0009", "EST-0010", "EST-0011"}
        rows = [row for row in resolved if row.work_class == "agent-implementation"]
        with_factor = spread_of(ratios_of([r for r in rows if r.estimate_id in corrected]))
        without = spread_of(ratios_of([r for r in rows if r.estimate_id not in corrected]))
        assert with_factor is not None
        assert without is not None
        assert (with_factor.n, without.n) == (3, 5)
        assert abs(with_factor.central - ONE) < abs(without.central - ONE)


class TestTheDetectiveAgrees:
    """The claims above are read off the shipped agent, not a parallel one."""

    def test_one_group_speaks_and_the_rest_refuse_by_name(self, resolved):
        connection = connect(MEMORY)
        migrate(connection)
        repository = Repository(connection)
        seed(repository, adr_dir=ADRS, dogfood_dir=DOGFOOD)
        factors = BiasDetective(repository).all_factors()
        speaking = [f for f in factors if f.speaks]
        assert [f.group.work_class for f in speaking] == ["agent-implementation"]
        assert all(f.reason for f in factors if not f.speaks)
        repository.close()
