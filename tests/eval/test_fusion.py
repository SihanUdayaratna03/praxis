"""Grading the fusion layer, which is mostly grading what it refused to say.

Two booleans in `FusionScore` carry a claim and the rest is context, so both get
a class -- and each is asserted in **both** directions, which is the lesson
Phase 7 recorded about refusal properties: "every refusal has a reason" is
satisfied trivially by a layer that refuses everything, so the speaking case has
to be constrained too, and a boolean that is never false proves nothing.

`cross_document` is a count with no answer key. The corpus plants no
cross-document `estimated_as` edges on purpose, so what is tested here is that
the counter recognises one when the store holds it -- built by hand, since the
generator will not produce it.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from praxis.agents.fusion import FusionVerdict
from praxis.domain.enums import RecordKind, SourceKind
from praxis.domain.ids import DocumentId, EstimateId, SpanId
from praxis.domain.links import LinkType
from praxis.domain.records import Document, Estimate, Span
from praxis.eval.fusion import FusionScore, grade_fusion
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

from tests.agents.test_fusion import (
    RUNS_LONG,
    RUNS_SHORT,
    a_disguised_estimate,
    an_assumption,
    write_history,
)
from tests.store.conftest import ACTOR, WRITTEN_AT, World, build_world, link


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


class TestTheEmptyStore:
    """A grade that cannot be computed is still a grade, not a crash."""

    def test_an_empty_store_grades_to_zeros_that_hold(self, store: Repository) -> None:
        score = grade_fusion(store)

        assert score == FusionScore(refusals_hold=True, flips_hold=True)
        assert score.flip_rate == Decimal(0)
        assert score.damaging_rate == Decimal(0)


class TestTheFlipClaim:
    """`flips_hold` is the phase's central claim, asserted both ways."""

    def test_a_real_flip_holds(self, store: Repository, world: World) -> None:
        write_history(store, world, RUNS_LONG)
        a_disguised_estimate(store, world, predicate="migration_weeks <= 6", quantity="4")

        score = grade_fusion(store)

        assert score.flips == 1
        assert score.flips_hold
        assert score.by_verdict[FusionVerdict.FLIPPED.value] == 1

    def test_the_control_a_store_with_no_flips_still_holds(
        self, store: Repository, world: World
    ) -> None:
        """A boolean that is true because nothing was checked proves nothing.

        Same store shape, an over-estimator instead of an under-estimator, so
        edges are priced and none of them flips. `flips_hold` must still be
        true, and `flips` must be zero -- otherwise the test above is measuring
        the presence of edges rather than the correctness of the flips.
        """
        write_history(store, world, RUNS_SHORT)
        a_disguised_estimate(store, world, predicate="migration_weeks <= 6", quantity="4")

        score = grade_fusion(store)

        assert score.priced
        assert score.flips == 0
        assert score.flips_hold
        assert score.flip_rate == Decimal(0)


class TestTheRefusalClaim:
    """`refusals_hold` checks the threshold survived one more caller."""

    def test_a_group_below_the_threshold_refuses_and_that_holds(
        self, store: Repository, world: World
    ) -> None:
        write_history(store, world, RUNS_LONG[:2])
        a_disguised_estimate(store, world, predicate="migration_weeks <= 6", quantity="4")

        score = grade_fusion(store)

        assert score.by_verdict[FusionVerdict.NO_FACTOR.value] >= 1
        assert score.refusals_hold

    def test_the_control_a_store_where_everything_speaks_also_holds(
        self, store: Repository, world: World
    ) -> None:
        """The other direction: a factor that spoke must carry a speaking factor.

        Without this, `refusals_hold` would be satisfied by a bridge that never
        speaks at all, which is the failure mode Phase 7 named.
        """
        write_history(store, world, RUNS_LONG)
        a_disguised_estimate(store, world, predicate="migration_weeks <= 6", quantity="4")

        score = grade_fusion(store)

        assert score.flips == 1
        assert score.refusals_hold

    def test_an_edge_with_no_subject_is_counted_and_still_holds(
        self, store: Repository, world: World
    ) -> None:
        """A refusal with no factor at all is a different shape and must not fail."""
        write_history(store, world, RUNS_LONG)
        a_disguised_estimate(
            store, world, predicate="migration_weeks <= 6 and disk_gb <= 5", quantity="4"
        )

        score = grade_fusion(store)

        assert score.by_verdict[FusionVerdict.NO_SUBJECT.value] == 1
        assert score.refusals_hold


class TestCrossDocumentIsCountedNeverScored:
    """The number `BACKLOG.md` refuses to plant as ground truth."""

    def test_an_edge_within_one_document_is_not_counted(
        self, store: Repository, world: World
    ) -> None:
        write_history(store, world, RUNS_LONG)
        a_disguised_estimate(store, world, predicate="migration_weeks <= 6", quantity="4")

        assert grade_fusion(store).cross_document == 0

    def test_an_edge_spanning_two_documents_is_counted(
        self, store: Repository, world: World
    ) -> None:
        """Built by hand, because the generator deliberately produces none.

        Planting it as ground truth would fix the answer before anyone asked the
        question, so the only way to test the counter is to assemble the shape
        the counter is looking for.
        """
        elsewhere = store.add(
            Document(
                id=DocumentId(store.next_id(RecordKind.DOCUMENT)),
                source_uri="file://retro.md",
                source_kind=SourceKind.MARKDOWN,
                title="Migration retro",
                content="The ledger migration took nine weeks in the end.",
                ingested_at=WRITTEN_AT,
                created_at=WRITTEN_AT,
                created_by=ACTOR,
            ),
            actor=ACTOR,
            reason="a second document",
            at=WRITTEN_AT,
        )
        far_span = store.add(
            Span.covering(elsewhere, 0, 20, created_by=ACTOR, created_at=WRITTEN_AT),
            actor=ACTOR,
            reason="a span in it",
            at=WRITTEN_AT,
        )
        assumption = an_assumption(store, world, "migration_weeks <= 6")
        estimate = store.add(
            Estimate(
                id=EstimateId(store.next_id(RecordKind.ESTIMATE)),
                subject="the ledger migration",
                owner="Nadeesha",
                work_class="migration",
                active_quantity=Decimal(4),
                blocked_quantity=Decimal(0),
                unit=world.estimate.unit,
                confidence=0.5,
                estimated_at=WRITTEN_AT,
                span_id=SpanId(far_span.id),
                created_at=WRITTEN_AT,
                created_by="EstimateExtractor",
            ),
            actor=ACTOR,
            reason="stated in the other document",
            at=WRITTEN_AT,
        )
        link(store, LinkType.ESTIMATED_AS, assumption.id, estimate.id)

        assert grade_fusion(store).cross_document == 1


class TestTheBackwardDirection:
    """Misses, and how many of them damaged anything."""

    def test_a_damaging_miss_is_counted_on_both_axes(self, store: Repository, world: World) -> None:
        """The fixture's outcome is a `MISS` with two decisions resting on it."""
        score = grade_fusion(store)

        assert score.misses == 1
        assert score.damaging == 1
        assert score.damaging_rate == Decimal(1)


class TestTheRatesReadAsFacts:
    """Numbers that invite being read as quality scores, and are not."""

    def test_the_verdict_map_omits_verdicts_nothing_landed_on(
        self, store: Repository, world: World
    ) -> None:
        """A table of six zeros hides the one row that happened."""
        write_history(store, world, RUNS_LONG)
        a_disguised_estimate(store, world, predicate="migration_weeks <= 6", quantity="4")

        by_verdict = grade_fusion(store).by_verdict

        assert all(count > 0 for count in by_verdict.values())
        assert FusionVerdict.RELIEVED.value not in by_verdict


class TestTheEdgesThatCannotBeCompared:
    """A withdrawn estimate is neither same-document nor cross-document."""

    def test_a_withdrawn_estimate_is_not_counted_either_way(
        self, store: Repository, world: World
    ) -> None:
        """Guessing would put a number in a column that has no answer key."""
        write_history(store, world, RUNS_LONG)
        _, estimate = a_disguised_estimate(
            store, world, predicate="migration_weeks <= 6", quantity="4"
        )
        store.retract(Estimate, estimate.id, actor=ACTOR, reason="withdrawn", at=WRITTEN_AT)

        score = grade_fusion(store)

        assert score.by_verdict[FusionVerdict.MISSING_ESTIMATE.value] == 1
        assert score.cross_document == 0
        assert score.refusals_hold

    def test_refused_is_everything_that_did_not_flip(self, store: Repository, world: World) -> None:
        """The ordinary outcome, and usually all of it."""
        write_history(store, world, RUNS_LONG)
        a_disguised_estimate(store, world, predicate="migration_weeks <= 6", quantity="4")

        score = grade_fusion(store)

        assert score.refused == score.priced - score.flips
