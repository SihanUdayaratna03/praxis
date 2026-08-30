"""The pass over a store: what it writes, what it refuses to write, and when.

Written at the store level on purpose. Every component this pass composes is
already covered in isolation, and Phase 6's own lesson was that four defects
were found by tests above the unit level while every agent was correct alone.
The interactions here are the ones worth holding: writing only on a change,
never raising a finding about a group with nothing to allege, and the anchor
convention staying stable as a group grows.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from praxis.agents.bias import BiasDirection, CalibrationFactor, summarise
from praxis.agents.calibration import (
    CALIBRATION_ACTOR,
    CalibrationRun,
    bias_finding,
    calibrate_store,
    prosecution_for,
    severity_for,
)
from praxis.agents.classifier import UNCLASSIFIED
from praxis.domain.enums import AuditAction, FindingKind, RecordKind, Severity, Verdict
from praxis.domain.records import AuditEvent, Estimate, Finding
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

from tests.agents.test_bias import GROUP, OWNER, WORK_CLASS, a_row, write_history
from tests.store.conftest import World, build_world

UNDER = [("6", "10.8")] * 6
"""Six estimates and six actuals at 1.8x. Enough to speak, and clearly biased."""

CALIBRATED = [("6", "6")] * 6
"""Six estimates answered exactly. Enough to speak, and nothing to allege."""


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


def findings_in(store: Repository) -> list[Finding]:
    """Every calibration finding the store currently holds."""
    return [
        finding
        for finding in store.list_all(Finding)
        if finding.kind is FindingKind.CALIBRATION_BIAS
    ]


class TestWhatItWrites:
    """A finding per measured, directional group. Nothing else."""

    def test_a_measured_bias_raises_a_finding(self, store: Repository, world: World) -> None:
        write_history(store, world, UNDER)

        run = calibrate_store(store)

        assert len(run.raised) == 1
        assert run.raised[0].kind is FindingKind.CALIBRATION_BIAS

    def test_the_finding_names_the_group_before_anything_else(
        self, store: Repository, world: World
    ) -> None:
        """So the anchor estimate is never read as the biased row."""
        write_history(store, world, UNDER)

        prosecution = calibrate_store(store).raised[0].prosecution

        assert prosecution.startswith(f"{OWNER} estimating {WORK_CLASS} work")
        assert "allegation about the group" in prosecution

    def test_the_finding_carries_every_number_the_verdict_rests_on(
        self, store: Repository, world: World
    ) -> None:
        """Which is also what makes the write-on-change comparison exact."""
        write_history(store, world, UNDER)

        prosecution = calibrate_store(store).raised[0].prosecution

        assert "1.8000x under" in prosecution
        assert "n=6" in prosecution
        assert "confidence=" in prosecution
        assert "6 resolved of 6 estimates" in prosecution

    def test_it_is_filed_against_the_groups_earliest_estimate(
        self, store: Repository, world: World
    ) -> None:
        """The anchor convention, checked rather than described."""
        write_history(store, world, UNDER)
        mine = sorted(
            estimate.id
            for estimate in store.list_all(Estimate)
            if estimate.owner == OWNER and estimate.work_class == WORK_CLASS
        )

        finding = calibrate_store(store).raised[0]

        assert finding.subject_id == mine[0]
        assert finding.subject_kind is RecordKind.ESTIMATE

    def test_the_anchor_does_not_move_as_the_group_grows(
        self, store: Repository, world: World
    ) -> None:
        """Latest would move on every write and turn one allegation into a trail."""
        write_history(store, world, UNDER)
        first = calibrate_store(store).raised[0]

        write_history(store, world, [("6", "12")])
        second = calibrate_store(store).revised[0]

        assert second.id == first.id
        assert second.subject_id == first.subject_id

    def test_it_carries_no_evidence_spans_and_the_record_says_why(
        self, store: Repository, world: World
    ) -> None:
        """Computed from the store rather than quoted, so there is no passage."""
        write_history(store, world, UNDER)

        assert calibrate_store(store).raised[0].evidence_span_ids == ()

    def test_it_arrives_undecided(self, store: Repository, world: World) -> None:
        """`ChallengerAgent` turns a prosecution into a verdict, and is a later phase."""
        write_history(store, world, UNDER)

        finding = calibrate_store(store).raised[0]

        assert finding.verdict is Verdict.UNDECIDED
        assert finding.challenge is None

    def test_the_audit_row_names_the_pass_and_the_record_names_the_agent(
        self, store: Repository, world: World
    ) -> None:
        """Two questions, two answers, neither inferred from the other."""
        write_history(store, world, UNDER)

        finding = calibrate_store(store).raised[0]
        rows = [
            event for event in store.audit_for(finding.id) if event.action is AuditAction.CREATED
        ]

        assert finding.created_by == "BiasDetective"
        assert [event.actor for event in rows] == [CALIBRATION_ACTOR]


class TestWhatItRefusesToWrite:
    """Most groups, most of the time."""

    def test_a_group_below_the_threshold_raises_nothing(
        self, store: Repository, world: World
    ) -> None:
        write_history(store, world, [("6", "10.8")] * 4)

        run = calibrate_store(store)

        assert run.raised == ()
        assert findings_in(store) == []

    def test_a_calibrated_estimator_raises_nothing(self, store: Repository, world: World) -> None:
        """ "Nothing is wrong" in the same queue as a breach is how a queue stops being read."""
        write_history(store, world, CALIBRATED)

        run = calibrate_store(store)

        assert len(run.measured) == 1
        assert run.measured[0].direction is BiasDirection.NONE
        assert run.raised == ()

    def test_an_unclassified_group_raises_nothing(self, store: Repository, world: World) -> None:
        write_history(store, world, UNDER * 2, work_class=UNCLASSIFIED)

        assert calibrate_store(store).raised == ()

    def test_an_empty_store_writes_nothing_and_does_not_fail(self, store: Repository) -> None:
        run = calibrate_store(store)

        assert run == CalibrationRun()
        assert run.factors == ()

    def test_building_a_finding_for_a_group_with_nothing_to_allege_is_refused(
        self, store: Repository, world: World
    ) -> None:
        """The one output this pass must not produce, refused at the constructor too."""
        write_history(store, world, [("6", "10.8")] * 4)
        silent = summarise(GROUP, [a_row("6", "10.8", index=i) for i in range(4)])
        anchor = store.list_all(Estimate)[0]

        with pytest.raises(ValueError, match="nothing to allege"):
            bias_finding(silent, anchor, finding_id="F-9999", at=anchor.created_at)


class TestWritingOnlyOnAChange:
    """The rule `praxis.monitor.run` established, applied to a second pass."""

    def test_a_second_pass_over_an_unchanged_store_writes_nothing(
        self, store: Repository, world: World
    ) -> None:
        write_history(store, world, UNDER)
        calibrate_store(store)
        before = store.stats().audit_events

        run = calibrate_store(store)

        assert run.raised == ()
        assert run.revised == ()
        assert run.unchanged == 1
        assert store.stats().audit_events == before

    def test_a_new_outcome_that_moves_the_factor_writes_a_new_version(
        self, store: Repository, world: World
    ) -> None:
        write_history(store, world, UNDER)
        first = calibrate_store(store).raised[0]

        write_history(store, world, [("6", "36")])
        revised = calibrate_store(store).revised[0]

        assert revised.id == first.id
        assert revised.version == first.version + 1
        assert revised.prosecution != first.prosecution

    def test_the_old_version_stays_readable(self, store: Repository, world: World) -> None:
        """Invariant 7. "It said 1.8x in March and 2.4x in June" is a query."""
        write_history(store, world, UNDER)
        first = calibrate_store(store).raised[0]
        write_history(store, world, [("6", "36")])
        calibrate_store(store)

        assert store.get(Finding, first.id, version=1) == first

    def test_the_severity_moves_with_the_factor(self, store: Repository, world: World) -> None:
        write_history(store, world, UNDER)
        first = calibrate_store(store).raised[0]

        write_history(store, world, [("6", "60")] * 3)
        revised = calibrate_store(store).revised[0]

        assert first.severity is Severity.MEDIUM
        assert revised.severity is Severity.HIGH

    def test_a_third_pass_after_a_revision_is_quiet_again(
        self, store: Repository, world: World
    ) -> None:
        """Re-runnability is not a one-shot property."""
        write_history(store, world, UNDER)
        calibrate_store(store)
        write_history(store, world, [("6", "36")])
        calibrate_store(store)

        run = calibrate_store(store)

        assert run.raised == run.revised == ()
        assert run.unchanged == 1


class TestSeverity:
    """How hard a bias pushes for attention, symmetric between the directions."""

    @staticmethod
    def verdict(estimated: str, actual: str) -> CalibrationFactor:
        return summarise(GROUP, [a_row(estimated, actual, index=i) for i in range(6)])

    def test_a_modest_bias_is_medium(self) -> None:
        assert severity_for(self.verdict("6", "9")) is Severity.MEDIUM

    def test_double_is_high(self) -> None:
        assert severity_for(self.verdict("6", "12")) is Severity.HIGH

    def test_quadruple_is_critical(self) -> None:
        assert severity_for(self.verdict("6", "24")) is Severity.CRITICAL

    def test_over_estimation_is_graded_the_same_as_under(self) -> None:
        """Half as long as predicted is as wrong as twice as long.

        Reading the bands asymmetrically is how an over-estimator quietly stops
        being reported, because their factor never rises above one.
        """
        assert severity_for(self.verdict("12", "6")) is severity_for(self.verdict("6", "12"))
        assert severity_for(self.verdict("24", "6")) is severity_for(self.verdict("6", "24"))


class TestItIsAStorePassAndCostsNothing:
    """The two facts that make this different from every earlier pass."""

    def test_it_makes_no_model_calls(self, store: Repository, world: World) -> None:
        """Reported as zero rather than omitted: a missing cost row reads as unmeasured."""
        write_history(store, world, UNDER)

        assert calibrate_store(store).calls == 0

    def test_it_runs_over_a_store_with_no_documents_at_all(self, store: Repository) -> None:
        """There is nothing per-document to iterate, which is the design.

        Records written straight into the store, with no ingestion behind them,
        still calibrate -- because the input is accumulated history rather than
        a corpus.
        """
        assert calibrate_store(store).factors == ()

    def test_it_reports_refusals_beside_the_answers(self, store: Repository, world: World) -> None:
        """Which classes are one outcome short is the row a person acts on."""
        write_history(store, world, UNDER)
        write_history(store, world, [("6", "9")] * 2, work_class="refactor")

        run = calibrate_store(store)

        assert len(run.factors) > len(run.measured)

    def test_it_carries_a_backtest_for_every_group(self, store: Repository, world: World) -> None:
        write_history(store, world, UNDER * 2)

        run = calibrate_store(store)

        assert len(run.backtests) == len(run.factors)
        assert run.overall.graded

    def test_the_overall_backtest_is_ungraded_when_nothing_reached_the_threshold(
        self, store: Repository, world: World
    ) -> None:
        """The ordinary state, and the one a bare score would misreport."""
        write_history(store, world, [("6", "10.8")] * 3)

        assert not calibrate_store(store).overall.graded


class TestTheProsecutionText:
    """It is compared for equality on every run, so it is worth pinning."""

    def test_it_is_stable_for_an_unchanged_verdict(self) -> None:
        verdict = summarise(GROUP, [a_row("6", "10.8", index=i) for i in range(6)])

        assert prosecution_for(verdict) == prosecution_for(verdict)

    def test_it_moves_when_any_number_in_it_moves(self) -> None:
        """Write-on-change rests entirely on this being true."""
        six = summarise(GROUP, [a_row("6", "10.8", index=i) for i in range(6)])
        seven = summarise(GROUP, [a_row("6", "10.8", index=i) for i in range(7)])

        assert prosecution_for(six) != prosecution_for(seven)

    def test_a_moved_band_alone_is_enough_to_change_it(self) -> None:
        """Same n, same centre, different scatter. The text has to notice."""
        tight = summarise(GROUP, [a_row("6", "10.8", index=i) for i in range(6)])
        loose = summarise(
            GROUP,
            [a_row("6", "7.2" if i % 2 else "16.2", index=i) for i in range(6)],
        )

        assert tight.n == loose.n
        assert prosecution_for(tight) != prosecution_for(loose)


def test_every_written_finding_carries_exactly_one_creation_event(
    store: Repository, world: World
) -> None:
    """Invariant 7 at the point this pass touches it.

    The store writes the record and its audit row in one transaction, so this
    cannot fail without the write failing -- which is why it is worth asserting
    once here rather than in every test above: it pins that this pass goes
    through `Repository` rather than around it.
    """
    write_history(store, world, UNDER)

    finding = calibrate_store(store).raised[0]
    events = store.audit_for(finding.id)

    assert len(events) == 1
    assert all(isinstance(event, AuditEvent) for event in events)
    assert events[0].action is AuditAction.CREATED


def test_the_neutral_band_is_what_decides_there_is_nothing_to_allege() -> None:
    """Ties the pass's silence to ADR 0024's constant rather than to a second rule."""
    calibrated = summarise(GROUP, [a_row("6", "6.1", index=i) for i in range(6)])

    assert calibrated.speaks
    assert calibrated.direction is BiasDirection.NONE
    assert calibrated.factor is not None
    assert abs(calibrated.factor - Decimal(1)) < Decimal("0.05")
