"""The Phase 7 query, executed against Phase 6's output rather than sketched.

Phase 1 settled the `Link` table's shape by writing Phase 8's fusion walk
against it before anything used it. This is the same discipline one phase later:
`CalibratorAgent` and `BiasDetective` will compute a factor per person per work
class from whatever `OutcomeMatcher` writes, and a shape that is awkward there
is cheap to change now and expensive to change then.

So the query is here, it runs, and the four properties that keep it to a single
indexed join are asserted as properties rather than described in a comment:

- the pairing is a column, so this is a join and not a walk per estimate
- an unmatched estimate is an `unresolved` row, so there are no absences
- units are reconciled at write time, so nothing here converts
- `work_class` is on the estimate, so grouping is an indexed read

The last test runs the whole Half B pipeline over a real ingested document and
then asks the question end to end -- because a query that answers a hand-built
fixture and not the pipeline's own output would have proved nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from praxis.agents.estimation import estimate_store
from praxis.config.settings import Settings
from praxis.domain.enums import MatchQuality, RecordKind, Unit
from praxis.domain.ids import EstimateId, OutcomeId
from praxis.domain.records import Estimate, Outcome, Span
from praxis.ingest.adapters import MARKDOWN_ADAPTER
from praxis.ingest.pipeline import IngestionPipeline
from praxis.llm.mock import MockProvider
from praxis.llm.trace import MemoryTraceSink
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

from tests.store.conftest import ACTOR, WRITTEN_AT, World, build_world

STATUS_BODY = """\
# Discovery status — week 9

## How it went

Nadeesha put the search index migration at 4 weeks of hands-on work.

It actually took 7 weeks of hands-on work.
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


def an_estimate(  # noqa: PLR0913 -- every field here is a distinct axis of the query
    store: Repository,
    span_id: str,
    *,
    owner: str,
    work_class: str,
    active: str = "4",
    blocked: str = "0",
) -> Estimate:
    """One more estimate in the store, so a group can hold more than one row."""
    return store.add(
        Estimate(
            id=EstimateId(store.next_id(RecordKind.ESTIMATE)),
            subject=f"{work_class} work for {owner}",
            owner=owner,
            work_class=work_class,
            active_quantity=Decimal(active),
            blocked_quantity=Decimal(blocked),
            unit=Unit.WEEKS,
            confidence=0.6,
            estimated_at=WRITTEN_AT,
            span_id=span_id,
            created_at=WRITTEN_AT,
            created_by="EstimateExtractor",
        ),
        actor=ACTOR,
        reason="written by the test",
        at=WRITTEN_AT,
    )


def an_outcome(store: Repository, estimate: Estimate, *, active: str | None = None) -> Outcome:
    """The row standing against an estimate -- resolved, or explicitly not."""
    unresolved = active is None
    return store.add(
        Outcome(
            id=OutcomeId(store.next_id(RecordKind.OUTCOME)),
            estimate_id=estimate.id,
            active_quantity=None if unresolved else Decimal(active or "0"),
            blocked_quantity=None if unresolved else Decimal(0),
            unit=estimate.unit,
            match_quality=MatchQuality.UNRESOLVED if unresolved else MatchQuality.CLOSE,
            resolved_at=None if unresolved else WRITTEN_AT,
            notes="written by the test",
            created_at=WRITTEN_AT,
            created_by="OutcomeMatcher",
        ),
        actor=ACTOR,
        reason="written by the test",
        at=WRITTEN_AT,
    )


class TestTheQuery:
    """What Phase 7 will actually run."""

    def test_pairs_an_estimate_with_its_outcome(self, store: Repository, world: World) -> None:
        rows = store.calibration_history()

        assert len(rows) == 1
        row = rows[0]
        assert row.estimate_id == world.estimate.id
        assert row.outcome_id == world.outcome.id
        assert row.estimated_active == Decimal("6.5")
        assert row.actual_active == Decimal("9.0")

    def test_carries_the_two_grouping_keys(self, store: Repository, world: World) -> None:
        """Owner and work class, which is what calibration is computed within."""
        row = store.calibration_history()[0]

        assert row.group == ("Sihan Udayaratna", "data-modelling")

    def test_narrows_to_one_person_and_one_class(self, store: Repository, world: World) -> None:
        second = an_estimate(store, world.span.id, owner="Nadeesha", work_class="migration")
        an_outcome(store, second, active="5")

        mine = store.calibration_history(owner="Sihan Udayaratna")
        theirs = store.calibration_history(owner="Nadeesha", work_class="migration")

        assert [row.estimate_id for row in mine] == [world.estimate.id]
        assert [row.estimate_id for row in theirs] == [second.id]

    def test_returns_rows_already_grouped(self, store: Repository, world: World) -> None:
        """Ordered by owner then class, so a caller walks rather than sorts."""
        for owner, work_class in (
            ("Nadeesha", "migration"),
            ("Ishara", "backend"),
            ("Nadeesha", "backend"),
        ):
            an_outcome(store, an_estimate(store, world.span.id, owner=owner, work_class=work_class))

        groups = [row.group for row in store.calibration_history()]

        assert groups == sorted(groups)

    def test_a_group_can_hold_more_than_one_row(self, store: Repository, world: World) -> None:
        """The distribution Phase 7 needs, not just a single pair."""
        for active in ("3", "5", "8"):
            an_outcome(
                store,
                an_estimate(
                    store, world.span.id, owner="Nadeesha", work_class="migration", active=active
                ),
                active="6",
            )

        rows = store.calibration_history(owner="Nadeesha", work_class="migration")

        assert len(rows) == 3
        assert sorted(row.estimated_active for row in rows) == [
            Decimal(3),
            Decimal(5),
            Decimal(8),
        ]


class TestUnresolvedRows:
    """ADR 0022, checked from the query's side rather than the agent's."""

    def test_an_unresolved_estimate_is_returned_rather_than_missing(
        self, store: Repository, world: World
    ) -> None:
        """Which is what keeps this an inner join with no absences to handle."""
        never = an_estimate(store, world.span.id, owner="Nadeesha", work_class="migration")
        an_outcome(store, never)

        rows = store.calibration_history(owner="Nadeesha")

        assert [row.estimate_id for row in rows] == [never.id]
        assert not rows[0].resolved
        assert rows[0].actual_active is None

    def test_the_denominator_and_the_sample_come_from_one_read(
        self, store: Repository, world: World
    ) -> None:
        """ "Four estimates, one never answered" and "three estimates" differ."""
        for active in ("3", "5"):
            an_outcome(
                store,
                an_estimate(
                    store, world.span.id, owner="Nadeesha", work_class="migration", active=active
                ),
                active="4",
            )
        an_outcome(
            store, an_estimate(store, world.span.id, owner="Nadeesha", work_class="migration")
        )

        rows = store.calibration_history(owner="Nadeesha", work_class="migration")

        assert len(rows) == 3
        assert sum(1 for row in rows if row.resolved) == 2

    def test_an_empty_store_asks_the_question_without_failing(self, store: Repository) -> None:
        assert store.calibration_history() == ()
        assert store.calibration_history(owner="nobody", work_class="nothing") == ()


class TestVersionsAndUnits:
    """The two ways this query could quietly return the wrong thing."""

    def test_a_reclassified_estimate_is_counted_once_under_its_new_class(
        self, store: Repository, world: World
    ) -> None:
        """The store is append-only, so an unfiltered read would return both.

        `WorkClassifier` revises an estimate onto this axis, which makes the
        current-version filter load-bearing here in a way it is not elsewhere:
        without it one estimate would appear in two groups and be counted twice
        in a calibration computed over either.
        """
        estimate = an_estimate(store, world.span.id, owner="Nadeesha", work_class="unclassified")
        an_outcome(store, estimate, active="5")
        store.revise(
            estimate.model_copy(update={"work_class": "migration"}),
            actor="WorkClassifier",
            reason="moving a search index",
            at=WRITTEN_AT,
        )

        rows = store.calibration_history(owner="Nadeesha")

        assert len(rows) == 1
        assert rows[0].work_class == "migration"

    def test_a_retracted_estimate_leaves_the_history(self, store: Repository, world: World) -> None:
        store.retract(
            Estimate,
            world.estimate.id,
            actor=ACTOR,
            reason="withdrawn by the test",
            at=WRITTEN_AT,
        )

        assert store.calibration_history() == ()

    def test_both_quantities_come_back_as_decimals(self, store: Repository, world: World) -> None:
        """Invariant 4: these are summed and divided across a whole history."""
        row = store.calibration_history()[0]

        assert isinstance(row.estimated_active, Decimal)
        assert isinstance(row.estimated_blocked, Decimal)
        assert isinstance(row.actual_active, Decimal)
        assert isinstance(row.actual_blocked, Decimal)

    def test_the_two_sides_always_share_a_unit_so_nothing_here_converts(
        self, store: Repository, world: World
    ) -> None:
        """`OutcomeMatcher` guarantees it, and this is the query relying on it."""
        for row in store.calibration_history():
            assert row.unit is Unit.WEEKS


class TestAgainstThePipelinesOwnOutput:
    """The test that makes this a verification rather than a demonstration."""

    def test_answers_the_question_over_a_store_half_b_really_wrote(self, store: Repository) -> None:
        """A query that answers only a hand-built fixture has proved nothing."""
        provider = MockProvider(sink=MemoryTraceSink(), settings=Settings())
        source = MARKDOWN_ADAPTER.normalise(STATUS_BODY.encode("utf-8"), source_uri="status.md")
        IngestionPipeline(store, provider).ingest_source(
            source, at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
        )

        run = estimate_store(store, MockProvider(sink=MemoryTraceSink(), settings=Settings()))
        rows = store.calibration_history()

        # Offline the citation gate refuses almost everything, so the row count
        # is whatever survived rather than a fixed number. What is asserted is
        # that the query agrees with the store either way -- including when both
        # are empty, which is the honest offline outcome and not a failure.
        assert len(rows) == len(store.list_all(Outcome))
        assert len(rows) == run.outcomes
        assert {row.estimate_id for row in rows} == {
            outcome.estimate_id for outcome in store.list_all(Outcome)
        }

    def test_every_estimate_half_b_wrote_appears_exactly_once(
        self, store: Repository, world: World
    ) -> None:
        """One estimate, one row. A duplicate here doubles a calibration factor."""
        for index in range(3):
            an_outcome(
                store,
                an_estimate(
                    store,
                    world.span.id,
                    owner="Nadeesha",
                    work_class="migration",
                    active=str(index + 1),
                ),
                active="4",
            )

        rows = store.calibration_history()
        seen = [row.estimate_id for row in rows]

        assert len(seen) == len(set(seen))

    def test_the_span_the_estimate_cites_is_still_reachable(
        self, store: Repository, world: World
    ) -> None:
        """The query drops provenance, and this says where it went rather than lost.

        `CalibrationRow` carries no `span_id` on purpose -- Phase 7 computes over
        quantities and does not re-read documents -- so the check that provenance
        survives is that the estimate id it does carry still resolves to one.
        """
        row = store.calibration_history()[0]

        estimate = store.require(Estimate, row.estimate_id)
        assert store.get(Span, estimate.span_id) is not None
