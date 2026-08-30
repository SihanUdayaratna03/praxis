"""The fusion mechanism backwards: an estimate missed, so what rested on it?

The store fixture is the graph this question was designed for, and it has been
in the repository since Phase 1:

    D-0001 --assumes--> A-0001 --estimated_as--> EST-0001
    D-0002 --justified_by--> EST-0001

Two routes to the same estimate, one direct and one two hops long. Phase 1 built
`Repository.impacted_by` to walk it and called that walk "the fusion query" in
its own docstring, so the class worth reading first here is
`TestTheWalkWasAlreadyBuilt`, which asserts that both decisions come back and
that this module did not write a second traversal to get them.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from praxis.agents.collateral import (
    BADLY_MISSED,
    COLLATERAL_NAME,
    CollateralAgent,
    collateral_finding,
    prosecution_for,
)
from praxis.config.models import NON_LLM_AGENTS, routed_agents
from praxis.domain.enums import (
    DecisionScope,
    FindingKind,
    Impact,
    MatchQuality,
    RecordKind,
    Severity,
    Verdict,
)
from praxis.domain.ids import EstimateId, OutcomeId
from praxis.domain.records import Decision, Estimate, Outcome
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

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
    """The Phase 1 graph. Its outcome is already a `MISS`: 6.5 estimated, 9.0 actual."""
    return build_world(store)


def outcome_of(store: Repository, world: World) -> Outcome:
    """The fixture's outcome, read back from the store rather than rebuilt."""
    return next(
        outcome for outcome in store.list_all(Outcome) if outcome.estimate_id == world.estimate.id
    )


class TestTheWalkWasAlreadyBuilt:
    """Phase 1's `impacted_by` is the traversal, and this module just asks it."""

    def test_both_routes_to_the_estimate_come_back(self, store: Repository, world: World) -> None:
        """One decision assumes its way there, the other justifies directly.

        If this module had walked `estimated_as` by hand it would most likely
        have found only the two-hop route and missed `justified_by` entirely,
        which is exactly the special-case-per-hop that `DEPENDENCY_LINK_TYPES`
        was arranged to prevent.
        """
        damage = CollateralAgent(store).damage_from(outcome_of(store, world))

        assert damage is not None
        assert damage.damaging
        found = {decision.id for decision in damage.decisions}
        assert found == {world.decision.id, world.other_decision.id}

    def test_the_assumption_on_the_path_is_reported_too(
        self, store: Repository, world: World
    ) -> None:
        """A person asking "how did this decision reach that estimate" gets an answer."""
        damage = CollateralAgent(store).damage_from(outcome_of(store, world))

        assert damage is not None
        assert [assumption.id for assumption in damage.assumptions] == [world.assumption.id]

    def test_the_overrun_is_the_ratio_the_calibration_side_already_computes(
        self, store: Repository, world: World
    ) -> None:
        """6.5 estimated against 9.0 actual. `ratio_of`, not a second formula."""
        damage = CollateralAgent(store).damage_from(outcome_of(store, world))

        assert damage is not None
        assert damage.overrun is not None
        assert damage.overrun > Decimal(1)

    def test_an_outcome_recording_only_blocked_time_has_no_overrun(
        self, store: Repository, world: World
    ) -> None:
        """A real shape, not a defensive branch: the record model permits it.

        `Outcome` requires *a* quantity for anything but `unresolved`, and
        blocked time alone satisfies that -- work that was pure waiting. There is
        no active ratio to report there and `None` is reported rather than a
        fabricated one, the same rule `ratio_of` applies to a zero. The finding
        still stands: the decisions resting on it are unchanged.
        """
        outcome = outcome_of(store, world)
        waiting = outcome.model_copy(
            update={"active_quantity": None, "blocked_quantity": Decimal(9)}
        )

        damage = CollateralAgent(store).damage_from(waiting)

        assert damage is not None
        assert damage.overrun is None
        assert damage.damaging
        assert "no ratio exists" in prosecution_for(damage)


class TestWhichMissesCount:
    """The threshold is a stored `MatchQuality` and no second one is invented."""

    def test_only_a_miss_is_surveyed(self) -> None:
        """`BADLY_MISSED` is the whole rule, and it is one member."""
        assert {MatchQuality.MISS} == BADLY_MISSED

    @pytest.mark.parametrize(
        "quality",
        [MatchQuality.EXACT, MatchQuality.CLOSE, MatchQuality.PARTIAL, MatchQuality.UNRESOLVED],
    )
    def test_anything_short_of_a_miss_raises_nothing(
        self, store: Repository, world: World, quality: MatchQuality
    ) -> None:
        """A close estimate has not been shown wrong, so nothing rests on it wrongly."""
        outcome = outcome_of(store, world)
        graded = outcome.model_copy(
            update={
                "match_quality": quality,
                "active_quantity": None if quality is MatchQuality.UNRESOLVED else Decimal(7),
                "blocked_quantity": None if quality is MatchQuality.UNRESOLVED else Decimal(0),
                "resolved_at": None if quality is MatchQuality.UNRESOLVED else WRITTEN_AT,
            }
        )

        assert CollateralAgent(store).damage_from(graded) is None

    def test_an_unresolved_estimate_is_not_collateral_evidence(
        self, store: Repository, world: World
    ) -> None:
        """Silence is not a miss.

        Raising findings from estimates nothing ever answered would fill the
        queue with allegations no evidence supports -- and `UNRESOLVED` exists
        so those estimates stay *visible*, not so they can be prosecuted.
        """
        assert MatchQuality.UNRESOLVED not in BADLY_MISSED

    def test_a_withdrawn_estimate_raises_nothing(self, store: Repository, world: World) -> None:
        """The `get` asymmetry Phase 8 found, applied here as well.

        `Repository.get` returns a retracted record rather than `None`, so a
        check for absence alone would prosecute an estimate that has already
        been taken back.
        """
        outcome = outcome_of(store, world)
        store.retract(Estimate, world.estimate.id, actor=ACTOR, reason="withdrawn", at=WRITTEN_AT)

        assert CollateralAgent(store).damage_from(outcome) is None


class TestSeverityIsNotReinvented:
    """`severity_for` is imported, so there is one triage queue and not two."""

    def test_severity_comes_from_the_decisions_resting_on_it(
        self, store: Repository, world: World
    ) -> None:
        damage = CollateralAgent(store).damage_from(outcome_of(store, world))

        assert damage is not None
        assert damage.severity in set(Severity)

    def test_an_organisation_wide_high_impact_decision_makes_it_critical(
        self, store: Repository, world: World
    ) -> None:
        """The promotion rule `praxis.monitor.breach` already owns, reached from here."""
        store.revise(
            world.decision.model_copy(
                update={"impact": Impact.HIGH, "scope": DecisionScope.ORGANISATION}
            ),
            actor=ACTOR,
            reason="raised for the test",
            at=WRITTEN_AT,
        )

        damage = CollateralAgent(store).damage_from(outcome_of(store, world))

        assert damage is not None
        assert damage.severity is Severity.CRITICAL

    def test_a_miss_nothing_rests_on_is_recorded_and_is_low(
        self, store: Repository, world: World
    ) -> None:
        """Worth recording, and not worth waking anybody for.

        An estimate with no decisions on it still missed. Dropping it would make
        "how often is a miss harmless" unanswerable.
        """
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
        missed = store.add(
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

        damage = CollateralAgent(store).damage_from(missed)

        assert damage is not None
        assert not damage.damaging
        assert damage.severity is Severity.LOW
        assert "nothing recorded rests on it" in damage.describe()


class TestTheFinding:
    """One per estimate, filed against the estimate, naming the decisions."""

    def test_it_is_filed_against_the_estimate_that_missed(
        self, store: Repository, world: World
    ) -> None:
        """Not one per damaged decision.

        `praxis.monitor.breach` made this choice for a breached assumption and
        the argument transfers: one finding per affected decision makes the
        count of findings a count of citations.
        """
        damage = CollateralAgent(store).damage_from(outcome_of(store, world))
        assert damage is not None

        finding = collateral_finding(damage, finding_id="F-0001", at=AT)

        assert finding.kind is FindingKind.COLLATERAL_IMPACT
        assert finding.subject_kind is RecordKind.ESTIMATE
        assert finding.subject_id == world.estimate.id
        assert finding.created_by == COLLATERAL_NAME

    def test_the_prosecution_names_every_decision_a_person_must_reread(
        self, store: Repository, world: World
    ) -> None:
        """A finding that makes somebody look those up is one nobody follows up."""
        damage = CollateralAgent(store).damage_from(outcome_of(store, world))
        assert damage is not None

        text = prosecution_for(damage)

        assert world.decision.id in text
        assert world.other_decision.id in text
        assert "miss" in text

    def test_it_arrives_undecided_with_no_challenge(self, store: Repository, world: World) -> None:
        """Writing it already decided would claim a review that never happened."""
        damage = CollateralAgent(store).damage_from(outcome_of(store, world))
        assert damage is not None

        finding = collateral_finding(damage, finding_id="F-0001", at=AT)

        assert finding.verdict is Verdict.UNDECIDED
        assert finding.challenge is None

    def test_it_carries_no_evidence_spans(self, store: Repository, world: World) -> None:
        """Computed from two records and the graph, not quoted from a document."""
        damage = CollateralAgent(store).damage_from(outcome_of(store, world))
        assert damage is not None

        assert collateral_finding(damage, finding_id="F-0001", at=AT).evidence_span_ids == ()

    def test_a_finding_cannot_be_built_from_an_outcome_that_did_not_miss(
        self, store: Repository, world: World
    ) -> None:
        """Refused a second time, at the builder, not only at the survey.

        An allegation with no evidence under it is the one thing this component
        must not be able to emit, so the check is not left to the caller alone.
        """
        damage = CollateralAgent(store).damage_from(outcome_of(store, world))
        assert damage is not None
        close = damage.outcome.model_copy(update={"match_quality": MatchQuality.CLOSE})

        with pytest.raises(ValueError, match="needs a missed outcome"):
            collateral_finding(
                type(damage)(
                    estimate=damage.estimate,
                    outcome=close,
                    decisions=damage.decisions,
                    assumptions=damage.assumptions,
                    overrun=damage.overrun,
                ),
                finding_id="F-0002",
                at=AT,
            )


class TestSurveyingTheWholeStore:
    """What `survey` returns, and what it deliberately keeps in."""

    def test_every_miss_is_returned_including_the_harmless_ones(
        self, store: Repository, world: World
    ) -> None:
        """A survey showing only damaging misses cannot answer how often one is harmless."""
        surveyed = CollateralAgent(store).survey()

        assert len(surveyed) == 1
        assert surveyed[0].estimate.id == world.estimate.id

    def test_a_second_survey_over_an_unchanged_store_answers_identically(
        self, store: Repository, world: World
    ) -> None:
        """Determinism, which is what lets the store pass write only on a change."""
        agent = CollateralAgent(store)

        first = agent.survey()
        second = agent.survey()

        assert [damage.describe() for damage in first] == [damage.describe() for damage in second]

    def test_a_retracted_decision_is_not_named_as_damaged(
        self, store: Repository, world: World
    ) -> None:
        """The edge survives the retraction, so the filter has to be on the record.

        Naming a withdrawn decision would send somebody to re-read a decision
        that has already been taken back.
        """
        store.retract(
            Decision, world.other_decision.id, actor=ACTOR, reason="withdrawn", at=WRITTEN_AT
        )

        damage = CollateralAgent(store).damage_from(outcome_of(store, world))

        assert damage is not None
        assert {decision.id for decision in damage.decisions} == {world.decision.id}


class TestTheArithmeticIsArithmetic:
    """Invariant 3, checked in the code rather than in the registry."""

    def test_the_agent_is_named_as_deterministic(self) -> None:
        assert COLLATERAL_NAME in NON_LLM_AGENTS
        assert COLLATERAL_NAME not in routed_agents()
        assert CollateralAgent.name == COLLATERAL_NAME
