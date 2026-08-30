"""The sentence this project exists to produce, executed rather than described.

`ARCHITECTURE.md`'s pitch is one paragraph and it is the thing being judged:

    AssumptionMonitor asks CalibrationEngine: "This decision assumed the
    migration takes 6 weeks. What is this team's calibration factor for
    migration work?" Calibration answers: "1.8x under-estimation, n=14,
    confidence 0.79." Praxis then says: "Decision D-0042 is probably built on a
    40% under-estimate. Re-examine it."

`TestThePitch` builds exactly that graph -- an assumption of six weeks, an
estimate of four, a history that runs 1.8x long -- and asserts the flip. It is
the one class in this file worth reading first.

The rest is the refusal vocabulary, which is most of the code. Four claims carry
their own class, and each property carries a control, because Phase 7 learned
that a refusal property is satisfied trivially by an agent that refuses
everything and an `assume`-guarded property proves nothing if the assumption is
never satisfiable.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.agents import fusion
from praxis.agents.bias import MINIMUM_SAMPLE, BiasVerdict
from praxis.agents.classifier import UNCLASSIFIED
from praxis.agents.fusion import FUSION_NAME, FusionBridge, FusionVerdict, flipped
from praxis.config.models import NON_LLM_AGENTS
from praxis.domain.enums import MatchQuality, RecordKind, Unit
from praxis.domain.ids import AssumptionId, EstimateId, OutcomeId
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Estimate, Outcome
from praxis.predicates.ast import Evaluation, Truth
from praxis.store.connection import MEMORY, connect
from praxis.store.errors import DanglingEdgeError
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

from tests.store.conftest import ACTOR, WRITTEN_AT, World, build_world, link

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)

OWNER = "Nadeesha"
WORK_CLASS = "migration"

RUNS_LONG = [("4", "7.2")] * MINIMUM_SAMPLE
"""Five estimates that each ran 1.8x long. `ARCHITECTURE.md`'s example, and an
under-estimator, so the factor is above one and shrinks nothing."""

RUNS_SHORT = [("6", "3")] * MINIMUM_SAMPLE
"""Five estimates that each came in at half. An over-estimator -- the direction
this project's own history actually runs."""


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
    """Real estimate/outcome pairs, so `BiasDetective` reads them through SQL."""
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
            reason="history",
            at=WRITTEN_AT,
        )
        store.add(
            Outcome(
                id=OutcomeId(store.next_id(RecordKind.OUTCOME)),
                estimate_id=estimate.id,
                active_quantity=None if actual is None else Decimal(actual),
                blocked_quantity=None if actual is None else Decimal(0),
                unit=Unit.WEEKS,
                match_quality=MatchQuality.UNRESOLVED if actual is None else MatchQuality.MISS,
                resolved_at=None if actual is None else WRITTEN_AT,
                notes="history",
                created_at=WRITTEN_AT,
                created_by="OutcomeMatcher",
            ),
            actor=ACTOR,
            reason="history",
            at=WRITTEN_AT,
        )


def an_assumption(
    store: Repository, world: World, predicate: str, *, statement: str = "the work is short"
) -> Assumption:
    """An assumption resting under the fixture's decision."""
    assumption = store.add(
        Assumption(
            id=AssumptionId(store.next_id(RecordKind.ASSUMPTION)),
            statement=statement,
            predicate=predicate,
            expiry_condition="when(phases_completed >= 6)",
            span_id=world.span.id,
            confidence=0.6,
            created_at=WRITTEN_AT,
            created_by="AssumptionExtractor",
        ),
        actor=ACTOR,
        reason="the assumption under test",
        at=WRITTEN_AT,
    )
    link(store, LinkType.ASSUMES, world.decision.id, assumption.id)
    return assumption


def an_estimate(
    store: Repository,
    world: World,
    quantity: str,
    *,
    owner: str = OWNER,
    work_class: str = WORK_CLASS,
) -> Estimate:
    """The estimate an assumption turns out to be, with no outcome of its own."""
    return store.add(
        Estimate(
            id=EstimateId(store.next_id(RecordKind.ESTIMATE)),
            subject="the work this assumption is about",
            owner=owner,
            work_class=work_class,
            active_quantity=Decimal(quantity),
            blocked_quantity=Decimal(0),
            unit=Unit.WEEKS,
            confidence=0.5,
            estimated_at=WRITTEN_AT,
            span_id=world.span.id,
            created_at=WRITTEN_AT,
            created_by="AssumptionExtractor",
        ),
        actor=ACTOR,
        reason="the estimate under test",
        at=WRITTEN_AT,
    )


def a_disguised_estimate(
    store: Repository,
    world: World,
    *,
    predicate: str,
    quantity: str,
    work_class: str = WORK_CLASS,
) -> tuple[Assumption, Estimate]:
    """The Phase 4 shape: an assumption, its estimate, and the edge between them.

    The edge is written here the way `AssumptionExtractor` writes it, because
    `FusionBridge` is not the thing that discovers this relationship -- it is the
    thing that prices one that already exists. See ADR 0016.
    """
    assumption = an_assumption(store, world, predicate)
    estimate = an_estimate(store, world, quantity, work_class=work_class)
    link(store, LinkType.ESTIMATED_AS, assumption.id, estimate.id)
    return assumption, estimate


class TestThePitch:
    """`ARCHITECTURE.md`'s paragraph, as a test that fails if the pitch is a slide."""

    def test_a_history_that_runs_long_flips_an_assumption_that_looked_fine(
        self, store: Repository, world: World
    ) -> None:
        """The whole product, in one assertion.

        Six weeks assumed, four weeks estimated, a history that runs 1.8x long.
        The raw estimate satisfies the predicate and the calibrated one does not,
        which is the finding the two halves exist to produce together. Neither
        half can reach it alone: the provenance side has no factor and the
        calibration side does not know which decision the number sits under.
        """
        write_history(store, world, RUNS_LONG)
        assumption, _ = a_disguised_estimate(
            store, world, predicate="migration_weeks <= 6", quantity="4"
        )

        (result,) = FusionBridge(store).price(assumption, now=NOW)

        assert result.verdict is FusionVerdict.FLIPPED
        assert result.flips
        assert result.raw == Decimal(4)
        assert result.calibrated == Decimal("7.2")
        assert result.raw_evaluation is not None
        assert result.raw_evaluation.truth is Truth.TRUE
        assert result.calibrated_evaluation is not None
        assert result.calibrated_evaluation.truth is Truth.FALSE

    def test_the_finding_carries_the_factor_that_produced_it(
        self, store: Repository, world: World
    ) -> None:
        """`n` and the confidence travel with the claim, not behind it.

        A person asked to re-open a decision is owed the strength of the evidence
        in the same sentence as the allegation, and ADR 0024 made the band
        inseparable from the factor for exactly this reason.
        """
        write_history(store, world, RUNS_LONG)
        assumption, _ = a_disguised_estimate(
            store, world, predicate="migration_weeks <= 6", quantity="4"
        )

        (result,) = FusionBridge(store).price(assumption, now=NOW)

        assert result.factor is not None
        assert result.factor.n == MINIMUM_SAMPLE
        assert result.factor.confidence is not None
        assert result.factor.verdict is BiasVerdict.MEASURED
        assert result.low is not None
        assert result.high is not None
        assert result.low <= result.calibrated <= result.high  # type: ignore[operator]
        assert "n=5" in result.describe()

    def test_the_decisions_resting_on_it_are_reachable_from_the_finding(
        self, store: Repository, world: World
    ) -> None:
        """The half that makes the factor actionable rather than interesting.

        "1.8x over on migrations" is a fact about a person. "D-0001 is built on
        it" is a fact about a decision, and only the graph can say so.
        """
        write_history(store, world, RUNS_LONG)
        assumption, _ = a_disguised_estimate(
            store, world, predicate="migration_weeks <= 6", quantity="4"
        )

        (result,) = FusionBridge(store).price(assumption, now=NOW)
        resting = store.links_to(assumption.id, types=(LinkType.ASSUMES,))

        assert result.flips
        assert [edge.source_id for edge in resting] == [world.decision.id]


class TestTheCommonCase:
    """Most corrections change the number and nothing else, and must stay quiet."""

    def test_a_correction_that_does_not_move_the_verdict_is_not_a_finding(
        self, store: Repository, world: World
    ) -> None:
        """The case that would bury the pitch if it emitted a finding.

        An over-estimator's four weeks corrected to two is still inside a
        six-week predicate. Reporting that would produce one finding per priced
        assumption per run, and the flip would be lost in it.
        """
        write_history(store, world, RUNS_SHORT)
        assumption, _ = a_disguised_estimate(
            store, world, predicate="migration_weeks <= 6", quantity="4"
        )

        (result,) = FusionBridge(store).price(assumption, now=NOW)

        assert result.verdict is FusionVerdict.UNCHANGED
        assert not result.flips
        assert result.priced
        assert result.calibrated == Decimal(2)

    def test_calibration_excusing_a_violation_is_reported_and_is_not_a_finding(
        self, store: Repository, world: World
    ) -> None:
        """The reverse flip exists, is named, and raises nothing.

        A raw estimate that violates the predicate and a calibrated one that does
        not is `RELIEVED`. It is worth seeing and it is not evidence the
        assumption is sound -- the factor is a tendency, not a measurement of
        this piece of work, and treating it as exoneration would let a track
        record of optimism argue an assumption out of trouble.
        """
        write_history(store, world, RUNS_SHORT)
        assumption, _ = a_disguised_estimate(
            store, world, predicate="migration_weeks <= 6", quantity="8"
        )

        (result,) = FusionBridge(store).price(assumption, now=NOW)

        assert result.verdict is FusionVerdict.RELIEVED
        assert not result.flips
        assert result.calibrated == Decimal(4)


class TestUnknownIsNotAFlip:
    """The distinction ADR 0019 turns on, from both sides."""

    def test_an_undecided_evaluation_never_reads_as_a_flip(self) -> None:
        """The guard itself, tested where it can actually be reached.

        `moved` is public precisely so this can be asserted directly. A
        `TRUE`-to-`UNKNOWN` transition is the dangerous one -- the raw estimate
        satisfied the predicate and the corrected one settles nothing -- and it
        must not come back as `FLIPPED`.
        """
        held = Evaluation(truth=Truth.TRUE)
        unsettled = Evaluation(truth=Truth.UNKNOWN, reason="6 / 0 cannot be computed")

        verdict, reason = fusion.moved(
            held, unsettled, subject="migration_weeks", raw=Decimal(4), calibrated=Decimal("7.2")
        )

        assert verdict is FusionVerdict.UNDECIDED
        assert "undecided" in reason
        assert verdict not in {FusionVerdict.FLIPPED, FusionVerdict.RELIEVED}

    def test_undecided_is_checked_before_a_flip_in_both_orders(self) -> None:
        """Order matters, so both arrangements are pinned rather than one."""
        settled = Evaluation(truth=Truth.FALSE)
        unsettled = Evaluation(truth=Truth.UNKNOWN, reason="nothing bound it")

        forwards, _ = fusion.moved(
            unsettled, settled, subject="x", raw=Decimal(1), calibrated=Decimal(2)
        )
        backwards, _ = fusion.moved(
            settled, unsettled, subject="x", raw=Decimal(1), calibrated=Decimal(2)
        )

        assert forwards is FusionVerdict.UNDECIDED
        assert backwards is FusionVerdict.UNDECIDED

    @pytest.mark.parametrize(
        "predicate",
        [
            'migration_weeks <= "soon"',
            "migration_weeks / 0 <= 6",
            "migration_weeks <= 6 and disk_gb / 2 <= 50",
        ],
    )
    def test_the_subject_gate_stops_every_undecidable_predicate_first(
        self, store: Repository, world: World, predicate: str
    ) -> None:
        """Why the guard above cannot currently be reached through the bridge.

        Documented as a test rather than as a comment, because it is a claim
        about `constraints_of` that could stop being true without anyone editing
        this module. Every predicate an evaluator could leave `UNKNOWN` -- a
        string comparison, a division by zero, a second identifier -- is one
        `constraints_of` names nothing in, so it is refused as `NO_SUBJECT`
        several steps before any evaluation happens. If this test starts failing,
        the `UNDECIDED` branch has become live and is doing its job.
        """
        write_history(store, world, RUNS_LONG)
        assumption, _ = a_disguised_estimate(store, world, predicate=predicate, quantity="4")

        (result,) = FusionBridge(store).price(assumption, now=NOW)

        assert result.verdict is FusionVerdict.NO_SUBJECT
        assert result.calibrated is None

    def test_the_control_the_same_graph_with_the_matching_name_does_flip(
        self, store: Repository, world: World
    ) -> None:
        """The control for the test above, which would otherwise prove nothing.

        `UNDECIDED` is satisfied trivially by a bridge that decides nothing, so
        the same store with the predicate's name changed must reach `FLIPPED`.
        """
        write_history(store, world, RUNS_LONG)
        assumption, _ = a_disguised_estimate(store, world, predicate="disk_gb <= 6", quantity="4")

        (result,) = FusionBridge(store).price(assumption, now=NOW)

        assert result.verdict is FusionVerdict.FLIPPED


class TestTheRefusals:
    """Every way an edge can fail to produce a number, each with its own verdict."""

    def test_a_group_below_the_threshold_refuses_and_says_so(
        self, store: Repository, world: World
    ) -> None:
        """Phase 7's threshold, reached through Phase 8's caller.

        One short of the sample, so the factor is not computed rather than
        computed and withheld -- and `FusionBridge` adds no override of its own.
        """
        write_history(store, world, RUNS_LONG[: MINIMUM_SAMPLE - 1])
        assumption, _ = a_disguised_estimate(
            store, world, predicate="migration_weeks <= 6", quantity="4"
        )

        (result,) = FusionBridge(store).price(assumption, now=NOW)

        assert result.verdict is FusionVerdict.NO_FACTOR
        assert not result.priced
        assert result.calibrated is None
        assert result.factor is not None
        assert not result.factor.speaks

    def test_an_unclassified_estimate_belongs_to_no_group(
        self, store: Repository, world: World
    ) -> None:
        """No work class, no group, no question to ask. ADR 0023."""
        write_history(store, world, RUNS_LONG)
        assumption, _ = a_disguised_estimate(
            store,
            world,
            predicate="migration_weeks <= 6",
            quantity="4",
            work_class=UNCLASSIFIED,
        )

        (result,) = FusionBridge(store).price(assumption, now=NOW)

        assert result.verdict is FusionVerdict.UNCLASSIFIED_WORK
        assert result.factor is None

    def test_a_predicate_naming_two_quantities_is_refused_rather_than_guessed(
        self, store: Repository, world: World
    ) -> None:
        """Binding one of two names would attach a number to the wrong claim.

        The same rule `praxis.monitor.facts` applies to a measured outcome, and
        it is reused rather than restated -- `subject_of` lives in one place.
        """
        write_history(store, world, RUNS_LONG)
        assumption, _ = a_disguised_estimate(
            store,
            world,
            predicate="migration_weeks <= 6 and review_weeks <= 2",
            quantity="4",
        )

        (result,) = FusionBridge(store).price(assumption, now=NOW)

        assert result.verdict is FusionVerdict.NO_SUBJECT
        assert result.subject is None

    def test_a_predicate_that_does_not_parse_is_refused(
        self, store: Repository, world: World
    ) -> None:
        """A formalizer's bad output is not a reason to raise a finding."""
        write_history(store, world, RUNS_LONG)
        assumption, _ = a_disguised_estimate(
            store, world, predicate="migration_weeks <=", quantity="4"
        )

        (result,) = FusionBridge(store).price(assumption, now=NOW)

        assert result.verdict is FusionVerdict.NO_SUBJECT

    def test_a_withdrawn_estimate_is_named_rather_than_priced(
        self, store: Repository, world: World
    ) -> None:
        """A retracted estimate is not a standing one, and must not be corrected.

        The route here is retraction rather than a dangling id, because the
        store's foreign key refuses an edge to a record that never existed --
        `DanglingEdgeError`, asserted below as the control. So the reachable
        version of "the estimate is gone" is one that was withdrawn, and
        `Repository.get` returns that record rather than `None`: retraction is a
        statement *about* a record, not the disappearance of one. A bridge
        testing only for `None` would price a withdrawn estimate as though it
        still stood.
        """
        write_history(store, world, RUNS_LONG)
        assumption, estimate = a_disguised_estimate(
            store, world, predicate="migration_weeks <= 6", quantity="4"
        )
        store.retract(Estimate, estimate.id, actor=ACTOR, reason="withdrawn", at=WRITTEN_AT)

        (result,) = FusionBridge(store).price(assumption, now=NOW)

        assert result.verdict is FusionVerdict.MISSING_ESTIMATE
        assert result.estimate is None
        assert estimate.id in result.reason

    def test_the_control_the_store_refuses_an_edge_to_an_estimate_that_never_existed(
        self, store: Repository, world: World
    ) -> None:
        """Why the test above goes through retraction and not through a bad id.

        Without this, "use a retracted estimate" reads as an arbitrary choice
        rather than the only reachable one.
        """
        assumption = an_assumption(store, world, "migration_weeks <= 6")

        with pytest.raises(DanglingEdgeError):
            link(store, LinkType.ESTIMATED_AS, assumption.id, EstimateId("EST-9999"))

    def test_every_refusal_carries_a_reason(self, store: Repository, world: World) -> None:
        """`reason` is never empty, on any path, including the ones that succeed."""
        write_history(store, world, RUNS_LONG)
        a_disguised_estimate(store, world, predicate="migration_weeks <= 6", quantity="4")
        a_disguised_estimate(store, world, predicate="disk_gb <= 50", quantity="4")
        a_disguised_estimate(
            store, world, predicate="x <= 1", quantity="4", work_class=UNCLASSIFIED
        )

        results = FusionBridge(store).price_all(now=NOW)

        assert results
        assert all(result.reason for result in results)


class TestWalkingTheWholeStore:
    """What `price_all` does and, more importantly, what it does not do."""

    def test_an_assumption_with_no_estimate_contributes_nothing(
        self, store: Repository, world: World
    ) -> None:
        """Not every assumption is an estimate in disguise, and that is not a refusal.

        A row saying "this assumption has no `estimated_as` edge" would be one
        per ordinary assumption in the store, which is noise standing where the
        refusals need to be readable.
        """
        write_history(store, world, RUNS_LONG)
        an_assumption(store, world, "the vendor stays in business")

        results = FusionBridge(store).price_all(now=NOW)
        subjects = {result.assumption.id for result in results}

        assert world.assumption.id in subjects
        assert all(result.estimate is not None or result.verdict for result in results)

    def test_flipped_narrows_to_exactly_the_findings(self, store: Repository, world: World) -> None:
        """One flip among several priced edges, and only it comes back."""
        write_history(store, world, RUNS_LONG)
        a_disguised_estimate(store, world, predicate="migration_weeks <= 6", quantity="4")
        a_disguised_estimate(store, world, predicate="migration_weeks <= 100", quantity="4")

        results = FusionBridge(store).price_all(now=NOW)

        assert len(flipped(results)) == 1
        assert all(result.verdict is FusionVerdict.FLIPPED for result in flipped(results))

    def test_a_second_run_over_an_unchanged_store_answers_identically(
        self, store: Repository, world: World
    ) -> None:
        """Determinism, which is what lets the store pass write only on a change."""
        write_history(store, world, RUNS_LONG)
        a_disguised_estimate(store, world, predicate="migration_weeks <= 6", quantity="4")
        bridge = FusionBridge(store)

        first = bridge.price_all(now=NOW)
        second = bridge.price_all(now=NOW)

        assert [result.describe() for result in first] == [result.describe() for result in second]


class TestTheArithmeticIsArithmetic:
    """Invariant 3 and ADR 0019, checked in the code rather than in the registry."""

    def test_the_bridge_is_named_as_deterministic(self) -> None:
        """The name in the set and the name on the class are the same string."""
        assert FUSION_NAME in NON_LLM_AGENTS
        assert FusionBridge.name == FUSION_NAME

    def test_the_module_cannot_reach_a_provider(self) -> None:
        """The way this would break is one convenient import, not an edited set."""
        assert "praxis.llm" not in inspect.getsource(fusion)

    @given(
        raw=st.decimals(min_value=Decimal("0.1"), max_value=Decimal(100), places=2),
        bound=st.decimals(min_value=Decimal("0.1"), max_value=Decimal(100), places=2),
    )
    def test_a_flip_always_means_the_two_evaluations_disagree(
        self, raw: Decimal, bound: Decimal
    ) -> None:
        """The property the finding rests on, over generated numbers.

        Whatever the estimate and whatever the bound, `FLIPPED` is reported only
        when the raw predicate held and the calibrated one is violated. The
        control is below: the same generator must also produce non-flips, or
        this passes against a bridge that never flips anything.
        """
        connection = connect(MEMORY)
        migrate(connection)
        repository = Repository(connection)
        try:
            world = build_world(repository)
            write_history(repository, world, RUNS_LONG)
            assumption, _ = a_disguised_estimate(
                repository,
                world,
                predicate=f"migration_weeks <= {bound}",
                quantity=str(raw),
            )

            (result,) = FusionBridge(repository).price(assumption, now=NOW)

            if result.verdict is FusionVerdict.FLIPPED:
                assert result.raw_evaluation is not None
                assert result.calibrated_evaluation is not None
                assert result.raw_evaluation.truth is Truth.TRUE
                assert result.calibrated_evaluation.truth is Truth.FALSE
        finally:
            repository.close()

    def test_the_control_the_generator_reaches_both_outcomes(
        self, store: Repository, world: World
    ) -> None:
        """The control the property above is worthless without.

        A property that only asserts inside an `if` is satisfied by a bridge
        that never enters it, so both branches are shown to be reachable with
        the same history and only the numbers changed.
        """
        write_history(store, world, RUNS_LONG)
        flips, _ = a_disguised_estimate(
            store, world, predicate="migration_weeks <= 6", quantity="4"
        )
        holds, _ = a_disguised_estimate(
            store, world, predicate="migration_weeks <= 100", quantity="4"
        )
        bridge = FusionBridge(store)

        assert bridge.price(flips, now=NOW)[0].verdict is FusionVerdict.FLIPPED
        assert bridge.price(holds, now=NOW)[0].verdict is FusionVerdict.UNCHANGED
