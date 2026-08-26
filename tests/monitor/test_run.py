"""A monitoring pass over a real store: what it writes, and what it refuses to write twice.

Every assertion about what was written reads it back out of SQLite. Checking the
objects the run returned would only prove the run agrees with itself, which is
the argument `tests/agents/test_extraction.py` makes about the extraction
pipeline and is if anything stronger here: half of what this code does is decide
*not* to write.

`test_a_second_pass_over_an_unchanged_world_writes_nothing` is the
re-runnability claim, and it is asserted against version counts and audit rows
rather than against a flag the run reports.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

import pydantic
import pytest
from praxis.domain.enums import (
    AssumptionStatus,
    DecisionScope,
    FindingKind,
    Impact,
    RecordKind,
    Severity,
    Unit,
    Verdict,
)
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Estimate, Finding, Link
from praxis.monitor.breach import breach_finding, severity_for
from praxis.monitor.facts import FACTS_FILENAME, FactsFile, load_facts, measured_in, subject_of
from praxis.monitor.run import MONITOR_ACTOR, decisions_resting_on, monitor_store
from praxis.predicates.ast import FALSE, TRUE, unknown
from praxis.predicates.world import WorldState
from praxis.store.repository import Repository

from tests.agents.conftest import Answering
from tests.monitor.conftest import ACTOR, AT, make_assumption, make_decision, measure, rest_on

UNDER = {"index_size_gb": Decimal(40)}
OVER = {"index_size_gb": Decimal(60)}


def world(facts=None, events=(), now=AT) -> WorldState:
    """A world holding only what a test names."""
    return WorldState(now=now, facts=facts or {}, events=frozenset(events))


def stored(store: Repository, assumption_id: str = "A-0001") -> Assumption:
    """The assumption as SQLite currently holds it."""
    return store.require(Assumption, assumption_id)


@pytest.fixture
def assumption(store: Repository, span) -> Assumption:
    """One formalized assumption, written."""
    return store.add(make_assumption(span), actor=ACTOR, reason="fixture")


@pytest.fixture
def resting(store: Repository, span, assumption: Assumption):
    """A decision that rests on the assumption, so a breach can reach something."""
    decision = store.add(make_decision(span), actor=ACTOR, reason="fixture")
    rest_on(store, decision, assumption)
    return decision


class TestWhatAPassWrites:
    def test_a_breach_writes_a_new_version_and_a_finding(self, store, assumption):
        run = monitor_store(store, world=world(OVER), at=AT)
        assert run.revised == 1
        assert len(run.findings) == 1
        assert stored(store).status is AssumptionStatus.BREACHED
        assert stored(store).version == 2

    def test_the_breach_finding_is_read_back_out_of_the_store(self, store, assumption):
        monitor_store(store, world=world(OVER), at=AT)
        findings = store.list_all(Finding)
        assert len(findings) == 1
        assert findings[0].kind is FindingKind.ASSUMPTION_BREACH
        assert findings[0].subject_id == "A-0001"

    def test_a_holding_assumption_is_written_too(self, store, assumption):
        # Holding is news the first time: the status moves off unverified, and
        # "checked and fine" is what a person needs to see to trust the rest.
        monitor_store(store, world=world(UNDER), at=AT)
        assert stored(store).status is AssumptionStatus.HOLDING
        assert store.list_all(Finding) == ()

    def test_a_status_change_carries_the_time_it_was_reached(self, store, assumption):
        # The record refuses a verdict with no evaluation time, because a
        # breached assumption whose monitor never ran and one that was just
        # checked would otherwise be indistinguishable.
        monitor_store(store, world=world(UNDER), at=AT)
        assert stored(store).last_evaluated_at == AT

    def test_the_audit_row_says_why_the_status_moved(self, store, assumption):
        monitor_store(store, world=world(OVER), at=AT)
        events = store.audit_for("A-0001")
        assert events[-1].actor == MONITOR_ACTOR
        assert "evaluated false" in events[-1].reason

    def test_the_run_id_reaches_every_row_it_writes(self, store, assumption):
        monitor_store(store, world=world(OVER), at=AT, run_id="RUN-abc")
        assert all(event.run_id == "RUN-abc" for event in store.audit_for("A-0001")[1:])

    def test_a_finding_is_undecided_until_something_challenges_it(self, store, assumption):
        # Nothing high-severity reaches a human without surviving the
        # challenger, and that agent is not built yet. A finding written already
        # decided would be claiming a review that never happened.
        monitor_store(store, world=world(OVER), at=AT)
        assert store.list_all(Finding)[0].verdict is Verdict.UNDECIDED

    def test_an_unchecked_assumption_is_reported_and_not_written(self, store, assumption):
        run = monitor_store(store, world=world(), at=AT)
        assert len(run.unchecked) == 1
        assert run.revised == 0
        assert stored(store).version == 1


class TestReRunning:
    def test_a_second_pass_over_an_unchanged_world_writes_nothing(self, store, assumption):
        # The re-runnability claim, asserted against version counts rather than
        # against something the run reports about itself.
        monitor_store(store, world=world(OVER), at=AT)
        before = stored(store).version, len(store.audit_for("A-0001"))

        second = monitor_store(store, world=world(OVER), at=AT + timedelta(days=1))
        assert second.revised == 0
        assert (stored(store).version, len(store.audit_for("A-0001"))) == before

    def test_a_second_pass_raises_no_duplicate_finding(self, store, assumption):
        monitor_store(store, world=world(OVER), at=AT)
        monitor_store(store, world=world(OVER), at=AT + timedelta(days=1))
        assert len(store.list_all(Finding)) == 1

    def test_a_world_that_changed_is_written_again(self, store, assumption):
        monitor_store(store, world=world(UNDER), at=AT)
        run = monitor_store(store, world=world(OVER), at=AT + timedelta(days=1))
        assert run.revised == 1
        assert stored(store).status is AssumptionStatus.BREACHED
        assert stored(store).version == 3

    def test_a_repaired_assumption_can_go_back_to_holding(self, store, assumption):
        # And the breach that was raised stays in the store, because the store
        # is append-only and a breach that happened did happen.
        monitor_store(store, world=world(OVER), at=AT)
        monitor_store(store, world=world(UNDER), at=AT + timedelta(days=1))
        assert stored(store).status is AssumptionStatus.HOLDING
        assert len(store.list_all(Finding)) == 1

    def test_a_second_breach_after_a_repair_is_a_second_finding(self, store, assumption):
        # Two separate breaches are two separate facts, and counting them once
        # would hide a decision that keeps going wrong.
        monitor_store(store, world=world(OVER), at=AT)
        monitor_store(store, world=world(UNDER), at=AT + timedelta(days=1))
        monitor_store(store, world=world(OVER), at=AT + timedelta(days=2))
        assert len(store.list_all(Finding)) == 2

    def test_a_pass_with_no_provider_still_breaches(self, store, assumption):
        assert monitor_store(store, world=world(OVER), at=AT).findings

    def test_a_pass_with_no_provider_makes_no_calls(self, store, assumption):
        assert monitor_store(store, world=world(OVER), at=AT).calls == 0


class TestWhatABreachReaches:
    def test_the_decisions_resting_on_an_assumption_are_found_by_walking(
        self, store, assumption, resting
    ):
        # A reverse walk rather than a stored list: the edges are the record of
        # what rests on what, and a second copy would be a second thing to keep
        # in step.
        assert [decision.id for decision in decisions_resting_on(store, "A-0001")] == ["D-0001"]

    def test_the_prosecution_names_them(self, store, assumption, resting):
        monitor_store(store, world=world(OVER), at=AT)
        assert "D-0001" in store.list_all(Finding)[0].prosecution

    def test_a_breach_nothing_rests_on_is_still_recorded(self, store, assumption):
        monitor_store(store, world=world(OVER), at=AT)
        assert store.list_all(Finding)[0].severity is Severity.LOW

    def test_one_finding_per_assumption_not_one_per_decision(self, store, span, assumption):
        # Otherwise a count of breaches would be a count of citations.
        for index in (1, 2, 3):
            decision = store.add(
                make_decision(span, decision_id=f"D-{index:04d}"), actor=ACTOR, reason="fixture"
            )
            rest_on(store, decision, assumption)
        monitor_store(store, world=world(OVER), at=AT)
        assert len(store.list_all(Finding)) == 1

    def test_the_finding_cites_the_span_the_assumption_was_read_from(self, store, assumption, span):
        monitor_store(store, world=world(OVER), at=AT)
        assert store.list_all(Finding)[0].evidence_span_ids == (span.id,)


class TestSeverity:
    def test_nothing_resting_on_it_is_low(self):
        assert severity_for(()) is Severity.LOW

    @pytest.mark.parametrize(
        ("impact", "expected"),
        [
            (Impact.LOW, Severity.LOW),
            (Impact.MEDIUM, Severity.MEDIUM),
            (Impact.HIGH, Severity.HIGH),
        ],
    )
    def test_the_worst_impact_becomes_the_severity(self, span, impact, expected):
        assert severity_for((make_decision(span, impact=impact),)) is expected

    def test_the_worst_of_several_wins(self, span):
        decisions = (
            make_decision(span, decision_id="D-0001", impact=Impact.LOW),
            make_decision(span, decision_id="D-0002", impact=Impact.HIGH),
        )
        assert severity_for(decisions) is Severity.HIGH

    def test_a_high_impact_decision_binding_everyone_is_critical(self, span):
        decision = make_decision(span, impact=Impact.HIGH, scope=DecisionScope.ORGANISATION)
        assert severity_for((decision,)) is Severity.CRITICAL

    def test_a_low_impact_decision_binding_everyone_is_not(self, span):
        decision = make_decision(span, impact=Impact.LOW, scope=DecisionScope.ORGANISATION)
        assert severity_for((decision,)) is Severity.LOW


class TestABreachNeedsAViolation:
    def test_an_undecided_predicate_cannot_raise_a_breach(self, span):
        # Refused here as well as avoided by the caller. This is the one failure
        # the whole phase is arranged to prevent, so it is checked twice.
        with pytest.raises(ValueError, match="needs a violated predicate"):
            breach_finding(
                make_assumption(span),
                unknown("nothing measured it"),
                (),
                finding_id="F-0001",
                at=AT,
            )

    def test_a_holding_predicate_cannot_either(self, span):
        with pytest.raises(ValueError, match="needs a violated predicate"):
            breach_finding(make_assumption(span), TRUE, (), finding_id="F-0001", at=AT)

    def test_a_violated_one_can(self, span):
        finding = breach_finding(make_assumption(span), FALSE, (), finding_id="F-0001", at=AT)
        assert finding.kind is FindingKind.ASSUMPTION_BREACH
        assert finding.confidence == 0.8


class TestTheFactsAStoreCanAssert:
    def test_an_empty_store_binds_nothing(self, store):
        assert measured_in(store) == {}

    def test_a_store_with_no_outcome_binds_nothing(self, store, assumption):
        # The Phase 5 corpus case: the mechanism is built and there is nothing
        # for it to reach through until Phase 6 writes an Outcome.
        assert measured_in(store) == {}

    def test_a_measured_outcome_binds_the_predicate_s_subject(self, store, span):
        # The product's central claim in miniature: a missed estimate reaching
        # forward to breach the assumption a decision rests on.
        record = store.add(
            make_assumption(span, predicate="search_index_weeks <= 4"),
            actor=ACTOR,
            reason="fixture",
        )
        measure(store, span, record, estimated=Decimal(4), actual=Decimal(7))
        assert measured_in(store) == {"search_index_weeks": Decimal(7)}

    def test_a_missed_estimate_breaches_the_assumption_it_hid_in(self, store, span, resting):
        record = store.add(
            make_assumption(span, assumption_id="A-0002", predicate="search_index_weeks <= 4"),
            actor=ACTOR,
            reason="fixture",
        )
        measure(store, span, record, estimated=Decimal(4), actual=Decimal(7))
        run = monitor_store(store, at=AT)
        breached = {verdict.assumption.id for verdict in run.breached}
        assert "A-0002" in breached

    def test_a_predicate_naming_two_quantities_binds_neither(self, store, span):
        # Nothing says which of them the estimate is about, and binding the
        # wrong one would produce a breach with a number attached -- the most
        # convincing kind of wrong answer this system could give.
        record = store.add(
            make_assumption(span, predicate="a <= 4 and b >= 1"), actor=ACTOR, reason="fixture"
        )
        measure(store, span, record, estimated=Decimal(4), actual=Decimal(7))
        assert measured_in(store) == {}

    def test_an_unparseable_predicate_binds_nothing(self, store, span):
        record = store.add(
            make_assumption(span, predicate="prose about weeks"), actor=ACTOR, reason="fixture"
        )
        measure(store, span, record, estimated=Decimal(4), actual=Decimal(7))
        assert measured_in(store) == {}

    def test_an_outcome_in_a_different_unit_is_left_unbound(self, store, span):
        # Comparing across units is how a four-week overrun reads as four hours.
        record = store.add(
            make_assumption(span, predicate="search_index_weeks <= 4"),
            actor=ACTOR,
            reason="fixture",
        )
        measure(
            store,
            span,
            record,
            estimated=Decimal(4),
            actual=Decimal(7),
            unit=Unit.WEEKS,
            outcome_unit=Unit.HOURS,
        )
        assert measured_in(store) == {}

    def test_the_subject_is_read_off_the_parsed_predicate(self, span):
        assert subject_of(make_assumption(span)) == "index_size_gb"
        assert subject_of(make_assumption(span, predicate="a <= 1 and b >= 2")) is None
        assert subject_of(make_assumption(span, predicate="not an expression")) is None


class TestTheFactsFile:
    def test_a_json_number_is_read_as_decimal(self):
        # Invariant 4 has no exception for a file: a rate read through binary
        # floating point would move a verdict that sits on a threshold.
        supplied = FactsFile.model_validate({"facts": {"share": 0.8}})
        assert supplied.facts["share"] == Decimal("0.8")
        assert isinstance(supplied.facts["share"], Decimal)

    def test_a_numeric_string_is_read_as_decimal_too(self):
        assert FactsFile.model_validate({"facts": {"n": "3.00"}}).facts["n"] == Decimal("3.00")

    def test_a_boolean_stays_a_boolean(self):
        # True is an int in Python, and a boolean silently becoming Decimal(1)
        # would make `valid == true` compare a number with a boolean forever.
        assert FactsFile.model_validate({"facts": {"valid": True}}).facts["valid"] is True

    def test_a_word_stays_a_word(self):
        assert FactsFile.model_validate({"facts": {"stage": "closed"}}).facts["stage"] == "closed"

    def test_a_file_is_read_from_a_path(self, tmp_path):
        path = tmp_path / FACTS_FILENAME
        path.write_text(json.dumps({"facts": {"a": 1}, "events": ["it shipped"]}), encoding="utf-8")
        assert load_facts(path).events == ("it shipped",)

    def test_a_directory_is_read_through_its_facts_file(self, tmp_path):
        (tmp_path / FACTS_FILENAME).write_text(json.dumps({"facts": {"a": 1}}), encoding="utf-8")
        assert load_facts(tmp_path).facts["a"] == Decimal(1)

    def test_supplied_facts_win_over_what_the_store_derived(self, store, span):
        record = store.add(
            make_assumption(span, predicate="search_index_weeks <= 4"),
            actor=ACTOR,
            reason="fixture",
        )
        measure(store, span, record, estimated=Decimal(4), actual=Decimal(7))
        run = monitor_store(
            store,
            supplied=FactsFile.model_validate({"facts": {"search_index_weeks": 2}}),
            at=AT,
        )
        assert not run.breached

    def test_an_as_of_date_is_what_the_expiry_is_read_against(self, store, span):
        store.add(
            make_assumption(span, expiry='after("2026-08-20")'), actor=ACTOR, reason="fixture"
        )
        run = monitor_store(
            store,
            supplied=FactsFile.model_validate({"as_of": "2026-08-01T00:00:00+00:00"}),
            at=AT,
        )
        assert not run.aged


class TestWhatARunReports:
    def test_every_assumption_is_reported_whether_or_not_it_changed(self, store, span):
        for index in (1, 2):
            store.add(
                make_assumption(span, assumption_id=f"A-{index:04d}"), actor=ACTOR, reason="x"
            )
        assert len(monitor_store(store, world=world(), at=AT).verdicts) == 2

    def test_the_counts_add_up_to_the_assumptions_read(self, store, span, assumption):
        run = monitor_store(store, world=world(OVER), at=AT)
        assert sum(run.counts().values()) == len(run.verdicts)

    def test_breached_and_aged_are_reported_apart(self, store, span):
        store.add(make_assumption(span, assumption_id="A-0001"), actor=ACTOR, reason="x")
        store.add(
            make_assumption(
                span,
                assumption_id="A-0002",
                predicate="nobody_measured_this <= 1",
                expiry='after("2020-01-01")',
            ),
            actor=ACTOR,
            reason="x",
        )
        run = monitor_store(store, world=world(OVER), at=AT)
        assert [verdict.assumption.id for verdict in run.breached] == ["A-0001"]
        assert [verdict.assumption.id for verdict in run.aged] == ["A-0002"]

    def test_a_run_over_an_empty_store_is_a_run(self, store):
        run = monitor_store(store, at=AT)
        assert run.verdicts == ()
        assert run.revised == 0

    def test_a_provider_is_used_only_for_event_matching(self, store, span):
        store.add(make_assumption(span), actor=ACTOR, reason="x")
        provider = Answering([json.dumps({"matches": []})])
        run = monitor_store(
            store, provider=provider, world=world(OVER, events=["something"]), at=AT
        )
        assert run.calls == 1
        assert len(run.findings) == 1

    def test_the_finding_id_follows_the_store_s_own_counter(self, store, span, assumption):
        store.add(
            breach_finding(
                make_assumption(span),
                FALSE,
                (),
                finding_id=store.next_id(RecordKind.FINDING),
                at=AT,
            ),
            actor=ACTOR,
            reason="an earlier finding",
        )
        monitor_store(store, world=world(OVER), at=AT)
        assert sorted(finding.id for finding in store.list_all(Finding)) == ["F-0001", "F-0002"]


class TestTheEdgesOfEachSource:
    def test_a_facts_field_that_is_not_a_mapping_is_left_to_pydantic(self):
        # The validator normalises numbers and refuses nothing; saying what a
        # facts file may contain is the model's job, not the coercion's.
        with pytest.raises(pydantic.ValidationError):
            FactsFile.model_validate({"facts": ["not", "a", "mapping"]})

    def test_a_value_of_a_kind_no_predicate_compares_is_passed_through(self):
        # Left alone rather than coerced, so the model refuses it by name
        # instead of this quietly turning it into something.
        with pytest.raises(pydantic.ValidationError):
            FactsFile.model_validate({"facts": {"a": {"nested": 1}}})

    def test_an_assumption_whose_expiry_does_not_parse_awaits_no_event(self, store, span):
        # It reaches the matcher's listing through parse_expiry, and one that
        # will not parse is skipped rather than shown as an awaited event.
        store.add(
            make_assumption(span, expiry="whenever we remember"), actor=ACTOR, reason="fixture"
        )
        provider = Answering([json.dumps({"matches": []})])
        run = monitor_store(store, provider=provider, world=world(events=["x"]), at=AT)
        assert run.calls == 0

    def test_holding_is_reported_as_its_own_group(self, store, assumption):
        run = monitor_store(store, world=world(UNDER), at=AT)
        assert [verdict.assumption.id for verdict in run.holding] == ["A-0001"]

    def test_an_estimate_no_outcome_resolved_binds_nothing(self, store, span):
        # The edge is there and the measurement is not, which is the ordinary
        # state of an estimate that has not finished yet.
        measured = store.add(
            make_assumption(span, predicate="search_index_weeks <= 4"),
            actor=ACTOR,
            reason="fixture",
        )
        measure(store, span, measured, estimated=Decimal(4), actual=Decimal(7))
        pending = store.add(
            make_assumption(span, assumption_id="A-0002", predicate="billing_events_weeks <= 3"),
            actor=ACTOR,
            reason="fixture",
        )
        estimate = store.add(
            Estimate(
                id="EST-0002",
                subject=pending.statement,
                owner="Ishara",
                work_class="data-engineering",
                active_quantity=Decimal(3),
                unit=Unit.WEEKS,
                confidence=0.5,
                estimated_at=AT,
                span_id=span.id,
                created_by=ACTOR,
                created_at=AT,
            ),
            actor=ACTOR,
            reason="fixture",
        )
        store.add(
            Link.between(
                LinkType.ESTIMATED_AS,
                pending.id,
                estimate.id,
                rationale="a quantified forward-looking claim about effort",
                confidence=0.7,
                created_by=ACTOR,
                created_at=AT,
                span_id=span.id,
            ),
            actor=ACTOR,
            reason="fixture",
        )
        assert measured_in(store) == {"search_index_weeks": Decimal(7)}
