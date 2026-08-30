"""Both directions over a store, and the rule that a second pass writes nothing.

The claims worth holding here are the ones a store pass gets wrong:

- **a projection and a measurement are different findings.** A calibrated flip
  is `STALE_DECISION` and a missed estimate is `COLLATERAL_IMPACT`, and neither
  is `ASSUMPTION_BREACH` -- which `praxis.monitor.breach` writes from facts a
  run was actually given. ADR 0028.
- **a second pass over an unchanged store writes nothing at all.** Not "writes
  the same thing again", which an append-only store would happily record as a
  new version every time anyone ran the command.
- **a miss that damages nothing raises no finding** and still appears in the
  survey, because those are different questions.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from praxis.agents.fusion import FusionBridge
from praxis.agents.fusion_pass import (
    FUSION_ACTOR,
    STALE_ACTOR,
    fuse_store,
    stale_finding,
    stale_prosecution,
)
from praxis.domain.enums import FindingKind, MatchQuality, RecordKind
from praxis.domain.ids import EstimateId, OutcomeId
from praxis.domain.links import LinkType
from praxis.domain.records import Estimate, Finding, Link, Outcome
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

from tests.agents.test_fusion import (
    NOW,
    RUNS_LONG,
    a_disguised_estimate,
    write_history,
)
from tests.store.conftest import ACTOR, WRITTEN_AT, World, build_world

AT = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


@pytest.fixture
def world(store: Repository) -> World:
    """The Phase 1 graph. Its estimate already carries a `MISS` outcome."""
    return build_world(store)


def findings_of(store: Repository, kind: FindingKind) -> list[Finding]:
    """Every standing finding of one kind."""
    return [finding for finding in store.list_all(Finding) if finding.kind is kind]


class TestBothDirections:
    """One pass, two questions, two kinds of finding."""

    def test_a_missed_estimate_raises_a_collateral_finding(
        self, store: Repository, world: World
    ) -> None:
        """The backward direction, over the fixture that was built for it."""
        run = fuse_store(store, at=AT)

        assert len(run.damaging) == 1
        collateral = findings_of(store, FindingKind.COLLATERAL_IMPACT)
        assert len(collateral) == 1
        assert collateral[0].subject_id == world.estimate.id
        assert collateral[0].subject_kind is RecordKind.ESTIMATE

    def test_a_calibrated_flip_raises_a_stale_decision_finding(
        self, store: Repository, world: World
    ) -> None:
        """The forward direction, on a history that runs 1.8x long."""
        write_history(store, world, RUNS_LONG)
        assumption, _ = a_disguised_estimate(
            store, world, predicate="migration_weeks <= 6", quantity="4"
        )

        run = fuse_store(store, at=NOW)

        assert len(run.flips) == 1
        stale = findings_of(store, FindingKind.STALE_DECISION)
        assert len(stale) == 1
        assert stale[0].subject_id == assumption.id
        assert stale[0].created_by == STALE_ACTOR

    def test_a_projection_is_never_filed_as_a_measured_breach(
        self, store: Repository, world: World
    ) -> None:
        """The distinction ADR 0028 turns on, asserted rather than described.

        `praxis.monitor.breach` writes `ASSUMPTION_BREACH` from facts a run was
        given. A calibration flip is a number nobody has observed yet, and a
        triage queue that could not tell the two apart would rank the projection
        above the measurement.
        """
        write_history(store, world, RUNS_LONG)
        a_disguised_estimate(store, world, predicate="migration_weeks <= 6", quantity="4")
        before = len(findings_of(store, FindingKind.ASSUMPTION_BREACH))

        fuse_store(store, at=NOW)

        assert len(findings_of(store, FindingKind.ASSUMPTION_BREACH)) == before

    def test_the_prosecution_leads_with_the_fact_that_nothing_was_measured(
        self, store: Repository, world: World
    ) -> None:
        """A reader who takes a projection for an observation over-reacts to it."""
        write_history(store, world, RUNS_LONG)
        a_disguised_estimate(store, world, predicate="migration_weeks <= 6", quantity="4")

        fuse_store(store, at=NOW)

        (stale,) = findings_of(store, FindingKind.STALE_DECISION)
        assert stale.prosecution.startswith("Nothing has been measured yet")
        assert "n=5" in stale.prosecution
        assert world.decision.id in stale.prosecution


class TestWritingOnlyOnAChange:
    """The rule `praxis.monitor.run` established, in an append-only store."""

    def test_a_second_pass_over_an_unchanged_store_writes_nothing(
        self, store: Repository, world: World
    ) -> None:
        """Not "writes the same thing again". Nothing."""
        first = fuse_store(store, at=AT)
        versions_before = store.versions(first.raised[0].id)

        second = fuse_store(store, at=AT)

        assert second.raised == ()
        assert second.revised == ()
        assert second.unchanged == first.written
        assert store.versions(first.raised[0].id) == versions_before

    def test_a_finding_whose_numbers_moved_is_revised_rather_than_duplicated(
        self, store: Repository, world: World
    ) -> None:
        """One continuing allegation, versioned -- not a trail of new rows."""
        first = fuse_store(store, at=AT)
        standing = first.raised[0]
        outcome = next(
            record for record in store.list_all(Outcome) if record.estimate_id == world.estimate.id
        )
        store.revise(
            outcome.model_copy(update={"active_quantity": Decimal(20)}),
            actor=ACTOR,
            reason="the actual was corrected",
            at=WRITTEN_AT,
        )

        second = fuse_store(store, at=AT)

        assert second.raised == ()
        assert len(second.revised) == 1
        assert second.revised[0].id == standing.id
        assert second.revised[0].version == 2

    def test_the_pass_costs_no_model_calls(self, store: Repository, world: World) -> None:
        """Structural: neither agent imports a provider. Reported, not omitted."""
        assert fuse_store(store, at=AT).calls == 0


class TestWhatIsWrittenAndWhatIsNot:
    """A survey row and an allegation are different things."""

    def test_a_miss_nothing_rests_on_is_surveyed_but_raises_no_finding(
        self, store: Repository, world: World
    ) -> None:
        """Both halves matter: it must appear, and it must not be triaged."""
        lonely = store.add(
            Estimate(
                id=EstimateId(store.next_id(RecordKind.ESTIMATE)),
                subject="a job nobody decided anything about",
                owner="Nadeesha",
                work_class="migration",
                active_quantity=Decimal(4),
                blocked_quantity=Decimal(0),
                unit=world.estimate.unit,
                confidence=0.5,
                estimated_at=WRITTEN_AT,
                span_id=world.span.id,
                created_at=WRITTEN_AT,
                created_by="EstimateExtractor",
            ),
            actor=ACTOR,
            reason="the test",
            at=WRITTEN_AT,
        )
        store.add(
            Outcome(
                id=OutcomeId(store.next_id(RecordKind.OUTCOME)),
                estimate_id=lonely.id,
                active_quantity=Decimal(12),
                blocked_quantity=Decimal(0),
                unit=world.estimate.unit,
                match_quality=MatchQuality.MISS,
                resolved_at=WRITTEN_AT,
                notes="the test",
                created_at=WRITTEN_AT,
                created_by="OutcomeMatcher",
            ),
            actor=ACTOR,
            reason="the test",
            at=WRITTEN_AT,
        )

        run = fuse_store(store, at=AT)

        surveyed = {damage.estimate.id for damage in run.damages}
        prosecuted = {finding.subject_id for finding in run.raised}
        assert lonely.id in surveyed
        assert lonely.id not in prosecuted

    def test_a_collateral_finding_records_why_it_exists(
        self, store: Repository, world: World
    ) -> None:
        """`LinkType.COLLATERAL_OF` has existed since Phase 1. This writes it."""
        fuse_store(store, at=AT)

        (collateral,) = findings_of(store, FindingKind.COLLATERAL_IMPACT)
        edges = [
            link
            for link in store.list_all(Link)
            if link.link_type is LinkType.COLLATERAL_OF and link.source_id == collateral.id
        ]

        assert [edge.target_id for edge in edges] == [world.estimate.id]

    def test_a_forward_finding_writes_no_collateral_edge(
        self, store: Repository, world: World
    ) -> None:
        """It is already filed against its subject, so the edge would assert nothing.

        The control for the test above: an edge written for both kinds would
        pass that assertion just as well and mean nothing.
        """
        write_history(store, world, RUNS_LONG)
        assumption, _ = a_disguised_estimate(
            store, world, predicate="migration_weeks <= 6", quantity="4"
        )

        fuse_store(store, at=NOW)

        edges = [
            link
            for link in store.list_all(Link)
            if link.link_type is LinkType.COLLATERAL_OF and link.target_id == assumption.id
        ]
        assert edges == []

    def test_the_refusals_are_kept_rather_than_filtered(
        self, store: Repository, world: World
    ) -> None:
        """Which groups are one outcome short is a fact somebody can act on.

        The fixture's own assumption carries an `estimated_as` edge to a group
        with no history, so it is priced and refused -- and it has to survive
        into the result, or the run cannot report why it found nothing.
        """
        run = fuse_store(store, at=AT)

        assert run.priced
        assert len(run.flips) < len(run.priced)


class TestTheAuditTrail:
    """Who wrote it and what produced it are different questions."""

    def test_the_actor_is_the_pass_and_created_by_is_the_agent(
        self, store: Repository, world: World
    ) -> None:
        run = fuse_store(store, at=AT)
        finding = run.raised[0]

        events = store.audit_for(finding.id)

        assert [event.actor for event in events] == [FUSION_ACTOR]
        assert finding.created_by != FUSION_ACTOR


class TestTheBuilderRefuses:
    """A finding raised from a correction that changed no verdict is noise."""

    def test_a_stale_finding_needs_a_flip(self, store: Repository, world: World) -> None:
        write_history(store, world, RUNS_LONG)
        assumption, _ = a_disguised_estimate(
            store, world, predicate="migration_weeks <= 100", quantity="4"
        )
        (unchanged,) = FusionBridge(store).price(assumption, now=NOW)

        with pytest.raises(ValueError, match="needs a flipped verdict"):
            stale_finding(unchanged, (), finding_id="F-9999", at=NOW)

    def test_a_prosecution_names_no_decision_when_none_rests_on_it(
        self, store: Repository, world: World
    ) -> None:
        """The sentence still has to read, and it says so plainly."""
        write_history(store, world, RUNS_LONG)
        assumption, _ = a_disguised_estimate(
            store, world, predicate="migration_weeks <= 6", quantity="4"
        )
        (flip,) = FusionBridge(store).price(assumption, now=NOW)

        assert "no recorded decision" in stale_prosecution(flip, ())
