"""The refusal, proved rather than asserted, and the shape Phase 8 reads.

Four claims this phase rests on, and each has a class here:

- no distribution of inputs makes a group below the threshold emit a factor;
- the number is not merely withheld below the threshold, it is never computed;
- a re-run over an unchanged store gives an identical answer;
- `ARCHITECTURE.md`'s fusion sentence renders off this object, in one call.

`summarise` is tested against lists of rows rather than through a store wherever
the claim is about the decision, because a property generated over stores would
spend its budget writing records instead of exploring the space the property is
about. The store is used where the claim is about the read -- grouping, the
narrowing, determinism across two reads.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from praxis.agents import bias
from praxis.agents.bias import (
    MINIMUM_SAMPLE,
    NEUTRAL_BAND,
    BiasDetective,
    BiasDirection,
    BiasVerdict,
    CalibrationFactor,
    CalibrationGroup,
    direction_of,
    summarise,
)
from praxis.agents.classifier import UNCLASSIFIED
from praxis.agents.distribution import CONFIDENCE_HALF_AT
from praxis.config.models import NON_LLM_AGENTS
from praxis.domain.enums import MatchQuality, RecordKind, Unit
from praxis.domain.ids import EstimateId, OutcomeId
from praxis.domain.records import Estimate, Outcome
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.reports import CalibrationRow
from praxis.store.repository import Repository

from tests.store.conftest import ACTOR, WRITTEN_AT, World, build_world

OWNER = "Nadeesha"
WORK_CLASS = "migration"
GROUP = CalibrationGroup(owner=OWNER, work_class=WORK_CLASS)


def a_row(
    estimated: str,
    actual: str | None,
    *,
    index: int = 0,
    owner: str = OWNER,
    work_class: str = WORK_CLASS,
) -> CalibrationRow:
    """One history row, resolved or explicitly not.

    Built directly rather than through the store, because the properties below
    are about the decision `summarise` makes and a generated store would spend
    its example budget on record construction.
    """
    return CalibrationRow(
        estimate_id=f"EST-{index:04d}",
        owner=owner,
        work_class=work_class,
        unit=Unit.WEEKS,
        subject=f"{work_class} work",
        estimated_active=Decimal(estimated),
        estimated_blocked=Decimal(0),
        outcome_id=f"OUT-{index:04d}",
        actual_active=None if actual is None else Decimal(actual),
        actual_blocked=None if actual is None else Decimal(0),
        match_quality=MatchQuality.UNRESOLVED if actual is None else MatchQuality.CLOSE,
    )


quantities = st.decimals(
    min_value=Decimal("0.1"),
    max_value=Decimal("500"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)
"""Estimated and actual quantities, wide enough that ratios reach both extremes."""

resolved_rows = st.builds(
    lambda estimated, actual, index: a_row(str(estimated), str(actual), index=index),
    estimated=quantities,
    actual=quantities,
    index=st.integers(min_value=0, max_value=9999),
)

any_rows = st.one_of(
    resolved_rows,
    st.builds(
        lambda estimated, index: a_row(str(estimated), None, index=index),
        estimated=quantities,
        index=st.integers(min_value=0, max_value=9999),
    ),
)

DOGFOOD = [
    a_row("5.5", "4.2", index=1),
    a_row("6.0", "2.75", index=2),
    a_row("6.5", "4.6", index=3),
    a_row("5.0", "2.8", index=4),
]
"""This project's own `agent-implementation` history at the close of Phase 6.

Four points, one short of the threshold. The sample that made this phase's
estimate awkward to price, and the one the refusal has to hold on.
"""


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


def write_history(
    store: Repository,
    world: World,
    pairs: list[tuple[str, str | None]],
    *,
    owner: str = OWNER,
    work_class: str = WORK_CLASS,
) -> None:
    """Put real records in the store, so the read is exercised end to end."""
    for estimated, actual in pairs:
        estimate = store.add(
            Estimate(
                id=EstimateId(store.next_id(RecordKind.ESTIMATE)),
                subject=f"{work_class} work",
                owner=owner,
                work_class=work_class,
                active_quantity=Decimal(estimated),
                blocked_quantity=Decimal(0),
                unit=Unit.WEEKS,
                confidence=0.5,
                estimated_at=WRITTEN_AT,
                span_id=world.span.id,
                created_at=WRITTEN_AT,
                created_by="EstimateExtractor",
            ),
            actor=ACTOR,
            reason="written by the test",
            at=WRITTEN_AT,
        )
        store.add(
            Outcome(
                id=OutcomeId(store.next_id(RecordKind.OUTCOME)),
                estimate_id=estimate.id,
                active_quantity=None if actual is None else Decimal(actual),
                blocked_quantity=None if actual is None else Decimal(0),
                unit=Unit.WEEKS,
                match_quality=MatchQuality.UNRESOLVED if actual is None else MatchQuality.CLOSE,
                resolved_at=None if actual is None else WRITTEN_AT,
                notes="written by the test",
                created_at=WRITTEN_AT,
                created_by="OutcomeMatcher",
            ),
            actor=ACTOR,
            reason="written by the test",
            at=WRITTEN_AT,
        )


class TestTheRefusal:
    """The claim this phase is judged on, under any distribution of inputs."""

    @given(rows=st.lists(any_rows, min_size=0, max_size=MINIMUM_SAMPLE - 1))
    def test_never_emits_a_factor_below_the_threshold(self, rows: list[CalibrationRow]) -> None:
        """No sample smaller than the threshold produces a number, ever.

        The headline property. Generated over rows that may be resolved or not,
        at every size the refusal covers, with quantities spanning three orders
        of magnitude -- so the sample can be tight, scattered, or entirely
        unanswered, and the answer is the same.
        """
        result = summarise(GROUP, rows)

        assert not result.speaks
        assert result.factor is None
        assert result.spread is None
        assert result.n < MINIMUM_SAMPLE

    @given(rows=st.lists(resolved_rows, min_size=MINIMUM_SAMPLE, max_size=40))
    def test_speaks_once_the_sample_is_large_enough(self, rows: list[CalibrationRow]) -> None:
        """The control the property above needs to mean anything.

        A refusal test passes trivially against a detective that refuses
        everything. This is the assertion that it does not.
        """
        result = summarise(GROUP, rows)

        assert result.speaks
        assert result.factor is not None
        assert result.n >= MINIMUM_SAMPLE

    def test_exactly_one_short_still_refuses(self) -> None:
        """The case this phase was written in, pinned as a literal.

        `agent-implementation` stood at n = 4 when EST-0008 was logged. If the
        threshold were ever going to bend, it would bend here.
        """
        result = summarise(GROUP, DOGFOOD)

        assert result.verdict is BiasVerdict.INSUFFICIENT_SAMPLE
        assert result.n == MINIMUM_SAMPLE - 1
        assert result.factor is None
        assert "1 short" in result.reason

    def test_the_fifth_outcome_is_what_changes_the_answer(self) -> None:
        """One more point, and the same rows now speak. Nothing else moved."""
        result = summarise(GROUP, [*DOGFOOD, a_row("5.5", "3.4", index=5)])

        assert result.verdict is BiasVerdict.MEASURED
        assert result.n == MINIMUM_SAMPLE
        assert result.factor is not None

    def test_the_threshold_takes_no_argument(self) -> None:
        """There is no override, and the signature is where that is enforced.

        A threshold that can be lowered by whoever wants an answer is not a
        threshold. `summarise` takes a group and rows and nothing else, so
        there is no keyword to pass and no default to change at a call site.
        """
        parameters = list(inspect.signature(summarise).parameters)

        assert parameters == ["group", "rows"]

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(rows=st.lists(any_rows, min_size=0, max_size=MINIMUM_SAMPLE - 1))
    def test_the_arithmetic_is_never_run_below_the_threshold(
        self, rows: list[CalibrationRow], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not computed and withheld -- not computed.

        The difference matters: a factor computed and hidden in a private field
        is one refactor from being printed, and a number nobody was supposed to
        see is exactly what a threshold exists to prevent. Checked by making the
        summary function explode rather than by trusting the ordering of the
        checks.
        """

        def explode(_: object) -> None:
            message = "spread_of must not be reached below the threshold"
            raise AssertionError(message)

        monkeypatch.setattr(bias, "spread_of", explode)

        assert not summarise(GROUP, rows).speaks


class TestWhatKindOfSilence:
    """Four verdicts rather than a boolean, because they are acted on differently."""

    def test_no_estimates_at_all_is_an_insufficient_sample(self) -> None:
        result = summarise(GROUP, [])

        assert result.verdict is BiasVerdict.INSUFFICIENT_SAMPLE
        assert result.considered == 0

    def test_estimates_nobody_ever_answered_are_reported_apart(self) -> None:
        """A fact about follow-through, not about time. Different row, different fix."""
        result = summarise(GROUP, [a_row("6", None, index=i) for i in range(9)])

        assert result.verdict is BiasVerdict.NO_RESOLVED_OUTCOMES
        assert result.considered == 9
        assert result.resolved == 0
        assert "not one resolved outcome" in result.reason

    def test_an_unclassified_group_is_never_summarised(self) -> None:
        """`work_class` is a key, so `unclassified` is the absence of one.

        Pooling these would compute one factor over migrations, refactors and
        incident response together and call it a person's bias.
        """
        rows = [a_row("6", "9", index=i, work_class=UNCLASSIFIED) for i in range(20)]

        result = summarise(CalibrationGroup(OWNER, UNCLASSIFIED), rows)

        assert result.verdict is BiasVerdict.UNCLASSIFIED
        assert result.factor is None
        assert result.n == 0
        assert result.considered == 20

    def test_an_unclassified_group_is_reported_rather_than_dropped(self) -> None:
        """The row a person can act on this afternoon, so it is not silence."""
        result = summarise(CalibrationGroup(OWNER, UNCLASSIFIED), [a_row("6", "9")])

        assert "belong to no group" in result.reason

    def test_a_resolved_row_measuring_only_blocked_time_yields_no_ratio(self) -> None:
        """A gap the record model allows and this has to survive.

        `Outcome` requires a resolved row to carry *a* quantity, not both -- so
        an outcome that recorded only waiting is a resolved row with no active
        figure. It is excluded rather than treated as zero, because zero
        hands-on effort is a measurement and "nobody wrote it down" is not.
        """
        blocked_only = CalibrationRow(
            estimate_id="EST-9001",
            owner=OWNER,
            work_class=WORK_CLASS,
            unit=Unit.WEEKS,
            subject="migration work",
            estimated_active=Decimal(6),
            estimated_blocked=Decimal(2),
            outcome_id="OUT-9001",
            actual_active=None,
            actual_blocked=Decimal(4),
            match_quality=MatchQuality.PARTIAL,
        )

        result = summarise(GROUP, [*DOGFOOD, blocked_only])

        assert result.resolved == 5
        assert result.n == 4
        assert result.excluded == 1
        assert not result.speaks

    def test_asking_about_an_unclassified_class_never_reads_the_store(
        self, store: Repository, world: World
    ) -> None:
        """The exclusion happens before the query, not after it.

        There is no group to read, so reading would be paying for rows that
        cannot form one. Checked by breaking the read: if the query is reached,
        this raises instead of returning a verdict.
        """

        def explode(**_: object) -> None:
            message = "no read should happen for an unclassified group"
            raise AssertionError(message)

        detective = BiasDetective(store)
        detective._repository = type(
            "Unreadable", (), {"calibration_history": staticmethod(explode)}
        )()

        result = detective.factor_for(owner=OWNER, work_class=UNCLASSIFIED)

        assert result.verdict is BiasVerdict.UNCLASSIFIED

    def test_a_resolved_row_with_a_zero_side_is_excluded_and_counted(self) -> None:
        """`n` may fall below `resolved`, and never silently.

        A zero has no ratio -- ADR 0021's rule about a band, applied to the
        quantity underneath it -- so the row leaves the sample and says so.
        """
        rows = [*DOGFOOD, a_row("0", "4", index=5), a_row("5", "0", index=6)]

        result = summarise(GROUP, rows)

        assert result.resolved == 6
        assert result.n == 4
        assert result.excluded == 2
        assert "zero on one side" in result.reason

    @given(rows=st.lists(any_rows, min_size=0, max_size=30))
    def test_the_three_counts_never_contradict_each_other(self, rows: list[CalibrationRow]) -> None:
        """`n <= resolved <= considered`, always, whatever the verdict."""
        result = summarise(GROUP, rows)

        assert result.n <= result.resolved <= result.considered
        assert result.considered == len(rows)
        assert result.excluded == result.resolved - result.n

    @given(rows=st.lists(any_rows, min_size=0, max_size=30))
    def test_a_reason_is_always_given(self, rows: list[CalibrationRow]) -> None:
        """Every path says why, so no caller has to render "no data" itself."""
        assert summarise(GROUP, rows).reason.strip()


class TestTheDirection:
    """Which way an estimator is wrong, with a band around calibrated."""

    def test_above_the_band_means_the_work_runs_long(self) -> None:
        assert direction_of(Decimal("1.8")) is BiasDirection.UNDER

    def test_below_the_band_means_the_work_finishes_early(self) -> None:
        assert direction_of(Decimal("0.6")) is BiasDirection.OVER

    @pytest.mark.parametrize(
        "factor", ["1.00", "1.04", "0.96"], ids=["exact", "just-over", "just-under"]
    )
    def test_inside_the_band_is_no_direction_and_that_is_a_result(self, factor: str) -> None:
        """Reporting "1.02x under" would be reading noise as a finding."""
        assert direction_of(Decimal(factor)) is BiasDirection.NONE

    @given(offset=st.decimals(min_value=Decimal("0.001"), max_value=Decimal(20), places=3))
    def test_the_band_is_symmetric_about_one(self, offset: Decimal) -> None:
        over = direction_of(Decimal(1) + NEUTRAL_BAND + offset)
        under = direction_of(Decimal(1) - NEUTRAL_BAND - offset)

        assert over is BiasDirection.UNDER
        assert under is BiasDirection.OVER

    def test_this_projects_own_history_reads_as_over_estimation(self) -> None:
        """Four over-estimates in a row, and the sign has to come out right."""
        result = summarise(GROUP, [*DOGFOOD, a_row("5.5", "3.4", index=5)])

        assert result.direction is BiasDirection.OVER
        assert result.factor is not None
        assert result.factor < 1


class TestTheTwoReadingsOfOneNumber:
    """`factor` for arithmetic, `magnitude` for prose, bound so they cannot drift."""

    def test_an_over_estimator_reads_above_one_in_prose(self) -> None:
        """0.61x and "1.64x over" are one measurement.

        A system printing the first would be describing an over-estimator with a
        number below one while its own documentation says "1.8x under".
        """
        result = summarise(GROUP, [*DOGFOOD, a_row("5.5", "3.4", index=5)])

        assert result.factor is not None
        assert result.magnitude is not None
        assert result.factor < 1
        assert result.magnitude > 1

    def test_an_under_estimator_reads_the_same_either_way(self) -> None:
        """Above one there is nothing to invert, so the two agree exactly."""
        rows = [a_row("4", "7", index=i) for i in range(MINIMUM_SAMPLE)]

        result = summarise(GROUP, rows)

        assert result.factor == result.magnitude

    @given(rows=st.lists(resolved_rows, min_size=MINIMUM_SAMPLE, max_size=25))
    def test_the_prose_reading_is_never_below_one(self, rows: list[CalibrationRow]) -> None:
        """ "0.6x under" is a sentence nobody can act on, so it cannot be printed.

        This property found a real defect: `magnitude` was keyed on the
        direction rather than on the factor, so a factor inside `NEUTRAL_BAND`
        -- direction `none`, factor still a shade under one -- came back below
        one. Exactly the case a hand-written example would have skipped.
        """
        result = summarise(GROUP, rows)

        assert result.magnitude is not None
        assert result.magnitude >= Decimal(1)

    def test_neither_reading_exists_when_the_detective_declines(self) -> None:
        result = summarise(GROUP, DOGFOOD)

        assert result.factor is None
        assert result.magnitude is None
        assert result.low is None
        assert result.high is None
        assert result.confidence is None


class TestPhaseEightsFusionQuery:
    """The one thing this phase has to get right for the next one.

    `ARCHITECTURE.md` describes the mechanism as: given a work class and an
    owner, get the factor, `n` and the confidence. These tests are that query,
    executed against the real read rather than sketched on paper -- the
    discipline Phase 1 applied to the `Link` table and Phase 6 applied to
    `calibration_history`.
    """

    def test_one_call_answers_with_a_factor_an_n_and_a_confidence(
        self, store: Repository, world: World
    ) -> None:
        write_history(store, world, [("6", str(6 * 1.8)) for _ in range(6)])

        factor = BiasDetective(store).factor_for(owner=OWNER, work_class=WORK_CLASS)

        assert factor.speaks
        assert factor.factor is not None
        assert factor.n == 6
        assert factor.confidence is not None

    def test_renders_the_sentence_the_architecture_promises(
        self, store: Repository, world: World
    ) -> None:
        """ "migration work is 1.8x under, n=6, confidence 0.52", off these fields.

        Written as a test rather than as a docstring because the shape is what
        Phase 8 depends on, and a shape only described in prose is one nobody
        finds out has changed.
        """
        write_history(store, world, [("6", "10.8") for _ in range(6)])

        sentence = BiasDetective(store).factor_for(owner=OWNER, work_class=WORK_CLASS).describe()

        assert sentence.startswith("migration work is 1.8")
        assert "x under" in sentence
        assert "n=6" in sentence
        assert "confidence=" in sentence

    def test_a_group_with_no_history_answers_rather_than_raising(
        self, store: Repository, world: World
    ) -> None:
        """Phase 8 must never have to tell an absence from an exception."""
        factor = BiasDetective(store).factor_for(owner="nobody", work_class="nothing")

        assert isinstance(factor, CalibrationFactor)
        assert not factor.speaks
        assert factor.verdict is BiasVerdict.INSUFFICIENT_SAMPLE

    def test_asking_about_one_group_reads_only_that_group(
        self, store: Repository, world: World
    ) -> None:
        """The narrowing reaches the database, which is what makes this one call.

        Another estimator's history exists and does not move the answer -- and
        after the Phase 7 store fix, does not even cross the boundary.
        """
        write_history(store, world, [("6", "10.8") for _ in range(6)])
        write_history(store, world, [("6", "1") for _ in range(9)], owner="somebody else")

        factor = BiasDetective(store).factor_for(owner=OWNER, work_class=WORK_CLASS)

        assert factor.n == 6
        assert factor.considered == 6

    def test_a_describing_line_exists_for_every_refusal_too(
        self, store: Repository, world: World
    ) -> None:
        """A caller formatting a table needs one line per group, not one per answer."""
        write_history(store, world, [("6", "9")])

        line = BiasDetective(store).factor_for(owner=OWNER, work_class=WORK_CLASS).describe()

        assert line.startswith("migration:")
        assert "short of the" in line


class TestReadingEveryGroup:
    """One read, grouped by a walk, refusals included."""

    def test_splits_by_owner_and_by_work_class(self, store: Repository, world: World) -> None:
        write_history(store, world, [("6", "9")], work_class="migration")
        write_history(store, world, [("6", "9")], work_class="refactor")
        write_history(store, world, [("6", "9")], owner="Ruwan", work_class="migration")

        groups = {result.group for result in BiasDetective(store).all_factors()}

        assert groups >= {
            CalibrationGroup("Nadeesha", "migration"),
            CalibrationGroup("Nadeesha", "refactor"),
            CalibrationGroup("Ruwan", "migration"),
        }

    def test_a_group_appears_exactly_once(self, store: Repository, world: World) -> None:
        """The walk must not split a group that the ordering already gathered."""
        write_history(store, world, [("6", "9") for _ in range(7)])
        write_history(store, world, [("6", "9") for _ in range(4)], work_class="refactor")

        results = BiasDetective(store).all_factors()

        assert len(results) == len({result.group for result in results})

    def test_every_estimate_lands_in_exactly_one_group(
        self, store: Repository, world: World
    ) -> None:
        """No row is counted twice and none is lost, which the walk could do either of.

        Counted against the store's own total rather than against a literal, so
        the shared fixture's estimate is included rather than assumed away --
        which is the point: a row this test forgot about is exactly the row a
        grouping walk would drop.
        """
        write_history(store, world, [("6", "9") for _ in range(7)])
        write_history(store, world, [("6", None) for _ in range(3)], work_class="refactor")
        write_history(store, world, [("6", "9")], owner="Ruwan")

        results = BiasDetective(store).all_factors()

        assert sum(result.considered for result in results) == len(store.calibration_history())

    def test_refusals_are_returned_beside_the_answers(
        self, store: Repository, world: World
    ) -> None:
        """Filtering them here would hide the classes that are one outcome short."""
        write_history(store, world, [("6", "10.8") for _ in range(6)])
        write_history(store, world, [("6", "9")], work_class="refactor")

        verdicts = {result.verdict for result in BiasDetective(store).all_factors()}

        assert verdicts == {BiasVerdict.MEASURED, BiasVerdict.INSUFFICIENT_SAMPLE}

    def test_unclassified_rows_are_their_own_row_and_no_ones_sample(
        self, store: Repository, world: World
    ) -> None:
        """The exclusion, checked where it would actually go wrong.

        Six unclassified estimates and four classified ones must not become one
        group of ten that clears the threshold.
        """
        write_history(store, world, [("6", "9") for _ in range(4)])
        write_history(store, world, [("6", "1") for _ in range(6)], work_class=UNCLASSIFIED)

        results = {result.group.work_class: result for result in BiasDetective(store).all_factors()}

        assert results[WORK_CLASS].n == 4
        assert not results[WORK_CLASS].speaks
        assert results[UNCLASSIFIED].verdict is BiasVerdict.UNCLASSIFIED
        assert not results[UNCLASSIFIED].speaks

    def test_an_empty_store_reports_no_groups(self, store: Repository) -> None:
        assert BiasDetective(store).all_factors() == ()


class TestDeterminism:
    """The property Phase 5's monitor proved for itself."""

    def test_re_running_over_an_unchanged_store_gives_an_identical_answer(
        self, store: Repository, world: World
    ) -> None:
        write_history(store, world, [("6", "9"), ("4", "7"), ("8", "9"), ("5", "6"), ("3", "5")])
        write_history(store, world, [("6", None) for _ in range(3)], work_class="refactor")

        detective = BiasDetective(store)

        assert detective.all_factors() == detective.all_factors()

    def test_two_detectives_over_one_store_agree(self, store: Repository, world: World) -> None:
        """The result must not depend on anything an instance carries."""
        write_history(store, world, [("6", "10.8") for _ in range(6)])

        first = BiasDetective(store).factor_for(owner=OWNER, work_class=WORK_CLASS)
        second = BiasDetective(store).factor_for(owner=OWNER, work_class=WORK_CLASS)

        assert first == second

    @given(rows=st.lists(any_rows, min_size=0, max_size=25))
    def test_the_decision_is_a_function_of_its_rows(self, rows: list[CalibrationRow]) -> None:
        assert summarise(GROUP, rows) == summarise(GROUP, list(rows))

    def test_a_narrowed_read_and_a_full_read_agree_about_a_group(
        self, store: Repository, world: World
    ) -> None:
        """Two entry points, one answer. Neither is built on the other, so this is real."""
        write_history(store, world, [("6", "10.8") for _ in range(6)])
        write_history(store, world, [("6", "9")], owner="Ruwan")

        detective = BiasDetective(store)
        narrowed = detective.factor_for(owner=OWNER, work_class=WORK_CLASS)
        walked = next(r for r in detective.all_factors() if r.group == GROUP)

        assert narrowed == walked


class TestItIsDeterministicByConstruction:
    """Invariant 3, checked in the place it would actually be broken."""

    def test_the_agent_is_named_in_the_set_that_refuses_to_route_it(self) -> None:
        assert bias.DETECTIVE_NAME in NON_LLM_AGENTS

    def test_the_threshold_and_the_confidence_half_point_are_the_same_number(self) -> None:
        """Tied by a test rather than left equal by coincidence.

        A group that has only just cleared the bar reports a confidence of at
        most one half. If either constant moves alone, that stops being true and
        this fails.
        """
        assert Decimal(MINIMUM_SAMPLE) == CONFIDENCE_HALF_AT

    def test_active_time_is_what_is_compared(self) -> None:
        """Blocked time is not an estimation error, so it is not in the ratio.

        The two rows here differ only in blocked time, wildly, and must produce
        the same factor -- otherwise the calibrator would learn that an
        estimator is unreliable about work they did correctly.
        """
        patient = [
            CalibrationRow(
                estimate_id=f"EST-{i:04d}",
                owner=OWNER,
                work_class=WORK_CLASS,
                unit=Unit.WEEKS,
                subject="migration work",
                estimated_active=Decimal(6),
                estimated_blocked=Decimal(i * 40),
                outcome_id=f"OUT-{i:04d}",
                actual_active=Decimal("10.8"),
                actual_blocked=Decimal(i * 400),
                match_quality=MatchQuality.CLOSE,
            )
            for i in range(MINIMUM_SAMPLE)
        ]

        assert summarise(GROUP, patient).factor == Decimal("1.8000")
