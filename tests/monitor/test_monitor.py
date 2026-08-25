"""What the monitor concludes, and the one thing it must never conclude.

The file exists for `TestAViolationIsNotTheSameThingAsAge`. Everything else
supports it. A monitor that cannot tell a genuinely violated predicate from one
that has merely gone stale is a monitor whose findings nobody can act on, and
the whole of Phase 5's grading rests on that distinction being reliable.

The strongest test here is `test_no_model_answer_can_produce_a_breach`. It is a
property rather than an example: whatever the event matcher says, and however
wrong it is, the set of breached assumptions is the same as it would have been
with no model at all. That is true by construction -- an event match can only
add an observation, an observation can only fire an expiry, and an expiry can
only produce `EXPIRED` -- and this is the test that says the construction has
not been undone.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from praxis.domain.enums import AssumptionStatus
from praxis.domain.records import Assumption
from praxis.monitor.monitor import MAX_MATCHED_EVENTS, AssumptionMonitor, Verdict
from praxis.predicates.ast import Truth
from praxis.predicates.world import WorldState

from tests.agents.conftest import Answering, Refusing
from tests.monitor.conftest import AT, make_assumption

UNDER = {"index_size_gb": Decimal(40)}
OVER = {"index_size_gb": Decimal(60)}


def world(facts=None, events=(), now=AT) -> WorldState:
    """A world holding only what a test names."""
    return WorldState(now=now, facts=facts or {}, events=frozenset(events))


def check(assumption: Assumption, state: WorldState) -> Verdict:
    """One assumption's verdict, with no provider anywhere near it."""
    return AssumptionMonitor().check(assumption, state)


def matches(*pairs: tuple[int, int], confidence: float = 0.9) -> str:
    """An event-matching answer as the structured layer would receive it."""
    return json.dumps(
        {
            "matches": [
                {"awaited_ordinal": awaited, "observed_ordinal": observed, "confidence": confidence}
                for awaited, observed in pairs
            ]
        }
    )


class TestTheFourVerdicts:
    def test_a_true_predicate_holds(self, span):
        verdict = check(make_assumption(span), world(UNDER))
        assert verdict.status is AssumptionStatus.HOLDING
        assert verdict.evaluation.truth is Truth.TRUE

    def test_a_false_predicate_is_a_breach(self, span):
        verdict = check(make_assumption(span), world(OVER))
        assert verdict.status is AssumptionStatus.BREACHED
        assert verdict.breached

    def test_an_unmeasured_predicate_whose_expiry_fired_is_expired(self, span):
        verdict = check(make_assumption(span, expiry='after("2026-01-01")'), world(now=AT))
        assert verdict.status is AssumptionStatus.EXPIRED
        assert verdict.aged

    def test_an_unmeasured_predicate_whose_expiry_has_not_fired_is_unchanged(self, span):
        # Reported rather than written. This row is the whole of re-runnability:
        # evaluation is free, so the monitor always evaluates and writes only
        # when the verdict is news.
        assumption = make_assumption(span, expiry='after("2027-01-01")')
        verdict = check(assumption, world())
        assert verdict.status is assumption.status
        assert not verdict.changed

    def test_a_fresh_true_verdict_beats_a_fired_expiry(self, span):
        # The expiry says the last verdict is stale; a fresh evaluation is not
        # stale. Holding, and the clock resets.
        verdict = check(make_assumption(span, expiry='after("2026-01-01")'), world(UNDER))
        assert verdict.status is AssumptionStatus.HOLDING

    def test_a_breach_beats_a_fired_expiry_too(self, span):
        # Knowing it is false is strictly more than knowing it is old, and only
        # one of the two raises a finding.
        verdict = check(make_assumption(span, expiry='after("2026-01-01")'), world(OVER))
        assert verdict.status is AssumptionStatus.BREACHED


class TestAViolationIsNotTheSameThingAsAge:
    """The distinction this phase is graded on."""

    def test_an_unmeasured_assumption_is_never_breached(self, span):
        # Answering "I was not told" with "your decision is broken" is the one
        # failure that would make every number this monitor reports worthless.
        verdict = check(make_assumption(span), world())
        assert not verdict.breached
        assert verdict.evaluation.truth is Truth.UNKNOWN

    def test_an_expired_assumption_is_never_breached(self, span):
        verdict = check(make_assumption(span, expiry='after("2020-01-01")'), world())
        assert verdict.aged
        assert not verdict.breached

    def test_a_breached_assumption_is_never_merely_aged(self, span):
        verdict = check(make_assumption(span, expiry='after("2020-01-01")'), world(OVER))
        assert verdict.breached
        assert not verdict.aged

    def test_the_reason_says_which_of_the_two_it_was(self, span):
        # A person reading the audit trail should not have to infer it from the
        # status column.
        breached = check(make_assumption(span), world(OVER))
        aged = check(make_assumption(span, expiry='after("2020-01-01")'), world())
        assert "evaluated false" in breached.reason
        assert "has fired" in aged.reason

    def test_an_unchecked_assumption_names_what_nobody_measured(self, span):
        verdict = check(make_assumption(span), world())
        assert "index_size_gb" in verdict.reason

    def test_a_division_by_zero_is_unchecked_rather_than_breached(self, span):
        # A ratio over a denominator nothing has counted. Three-valued
        # evaluation reaching the monitor intact.
        assumption = make_assumption(span, predicate="parsed / total >= 0.9")
        verdict = check(assumption, world({"parsed": Decimal(9), "total": Decimal(0)}))
        assert not verdict.breached
        assert verdict.evaluation.truth is Truth.UNKNOWN


class TestAnAssumptionThatWasNeverCheckable:
    def test_an_unparseable_predicate_is_not_evaluated(self, span):
        # AssumptionFormalizer's uncheckable output arriving here and being
        # handled rather than guessed at.
        verdict = check(make_assumption(span, predicate="the team stays motivated"), world(OVER))
        assert verdict.status is AssumptionStatus.UNVERIFIED
        assert not verdict.breached
        assert "does not parse" in verdict.reason

    def test_an_unparseable_expiry_stops_the_check_too(self, span):
        verdict = check(make_assumption(span, expiry="whenever we remember"), world(OVER))
        assert not verdict.breached
        assert "expiry condition does not parse" in verdict.reason

    def test_nothing_uncheckable_is_ever_written(self, span):
        assumption = make_assumption(span, predicate="prose, not an expression")
        assert not check(assumption, world(OVER)).changed


class TestWhenAnExpiryFires:
    def test_a_date_that_has_passed_fires(self, span):
        assumption = make_assumption(span, expiry='after("2026-08-24")')
        assert check(assumption, world(now=AT)).expiry.truth is Truth.TRUE

    def test_a_date_still_ahead_does_not(self, span):
        assumption = make_assumption(span, expiry='after("2026-08-26")')
        assert check(assumption, world(now=AT)).expiry.truth is Truth.FALSE

    def test_an_observed_event_fires_without_any_model(self, span):
        state = world(events=["The Index Is  Re-sharded"])
        assert check(make_assumption(span), state).expiry.truth is Truth.TRUE

    def test_a_when_condition_over_an_unmeasured_quantity_is_undecided(self, span):
        assumption = make_assumption(span, expiry="when(indexed_documents >= 10000000)")
        assert check(assumption, world(UNDER)).expiry.truth is Truth.UNKNOWN


class TestMatchingAnObservationToAnAwaitedEvent:
    def test_a_recognised_match_becomes_an_ordinary_observation(self, span):
        provider = Answering([matches((0, 0))])
        monitor = AssumptionMonitor(provider)
        state, calls = monitor.enrich(
            world(events=["the search index rollout completed"]), [make_assumption(span)]
        )
        assert calls == 1
        assert state.observed("the index is re-sharded")

    def test_the_awaited_wording_is_what_is_added(self, span):
        # WorldState.observed compares an on_event condition against what the
        # world holds, and the condition is spelled the awaited way.
        provider = Answering([matches((0, 0))])
        state, _ = AssumptionMonitor(provider).enrich(
            world(events=["something else entirely"]), [make_assumption(span)]
        )
        assert "the index is re-sharded" in state.events

    def test_a_low_confidence_match_is_ignored(self, span):
        provider = Answering([matches((0, 0), confidence=0.1)])
        state, _ = AssumptionMonitor(provider).enrich(
            world(events=["vaguely related"]), [make_assumption(span)]
        )
        assert not state.observed("the index is re-sharded")

    def test_an_ordinal_that_was_never_offered_is_ignored(self, span):
        # The same rule ADR 0015 makes about a span offering: an agent may only
        # cite what it was shown.
        provider = Answering([matches((7, 0))])
        state, _ = AssumptionMonitor(provider).enrich(
            world(events=["something"]), [make_assumption(span)]
        )
        assert not state.observed("the index is re-sharded")

    def test_nothing_is_asked_when_no_event_is_awaited(self, span):
        provider = Answering([matches((0, 0))])
        assumption = make_assumption(span, expiry='after("2026-08-31")')
        _, calls = AssumptionMonitor(provider).enrich(world(events=["x"]), [assumption])
        assert calls == 0

    def test_nothing_is_asked_when_nothing_has_been_observed(self, span):
        provider = Answering([matches((0, 0))])
        _, calls = AssumptionMonitor(provider).enrich(world(), [make_assumption(span)])
        assert calls == 0

    def test_an_event_already_observed_exactly_is_not_asked_about(self, span):
        # Deterministic matching has answered it, so a model is not paid to
        # answer it again.
        provider = Answering([matches((0, 0))])
        _, calls = AssumptionMonitor(provider).enrich(
            world(events=["the index is re-sharded"]), [make_assumption(span)]
        )
        assert calls == 0

    def test_one_call_serves_a_whole_run(self, span):
        # The question is about the two lists, not about any single record.
        provider = Answering([matches((0, 0), (1, 0))])
        assumptions = [
            make_assumption(span, assumption_id="A-0001"),
            make_assumption(
                span, assumption_id="A-0002", expiry='on_event("the vendor raises its price")'
            ),
        ]
        _, calls = AssumptionMonitor(provider).enrich(world(events=["something"]), assumptions)
        assert calls == 1

    def test_a_refused_matching_call_loses_nothing_but_freshness(self, span):
        # A bad answer about this batch is not a broken run: some assumptions
        # are re-checked later than they could have been, and nothing becomes
        # wrong.
        state, calls = AssumptionMonitor(Refusing([])).enrich(
            world(events=["the search index rollout completed"]), [make_assumption(span)]
        )
        assert calls == 1
        assert not state.observed("the index is re-sharded")

    def test_the_listing_is_capped(self, span):
        provider = Answering([matches()])
        assumptions = [
            make_assumption(
                span, assumption_id=f"A-{index:04d}", expiry=f'on_event("event {index}")'
            )
            for index in range(1, MAX_MATCHED_EVENTS + 6)
        ]
        AssumptionMonitor(provider).enrich(world(events=["something"]), assumptions)
        listing = provider.requests[0].messages[0].content
        assert f"[{MAX_MATCHED_EVENTS}]" not in listing.split("Observed:")[0]

    def test_the_call_names_the_prompt_behind_it(self, span):
        provider = Answering([matches()])
        AssumptionMonitor(provider).enrich(world(events=["x"]), [make_assumption(span)])
        assert provider.requests[0].prompt_id == "match_event@v1"
        assert provider.requests[0].agent == "AssumptionMonitor"


class TestNoModelAnswerCanBreach:
    """The safety property, asserted as a property rather than as an example."""

    @pytest.mark.parametrize(
        "answer",
        [
            matches(),
            matches((0, 0)),
            matches((0, 0), confidence=1.0),
            matches((0, 0), (0, 0)),
            '{"matches": [{"awaited_ordinal": null, "observed_ordinal": 0, "confidence": 1.0}]}',
        ],
    )
    def test_no_model_answer_can_produce_a_breach(self, span, answer):
        # Whatever the matcher says and however wrong it is, the breached set is
        # the one a run with no model at all would have produced. True by
        # construction: a match adds an observation, an observation fires an
        # expiry, and an expiry produces EXPIRED.
        assumptions = [make_assumption(span)]
        offline = AssumptionMonitor()
        before = [offline.check(a, world()).breached for a in assumptions]

        enriched, _ = AssumptionMonitor(Answering([answer])).enrich(
            world(events=["anything at all"]), assumptions
        )
        after = [offline.check(a, enriched).breached for a in assumptions]
        assert after == before == [False]

    def test_a_matched_event_can_only_age_an_assumption(self, span):
        provider = Answering([matches((0, 0))])
        enriched, _ = AssumptionMonitor(provider).enrich(
            world(events=["the rollout finished"]), [make_assumption(span)]
        )
        verdict = AssumptionMonitor().check(make_assumption(span), enriched)
        assert verdict.aged
        assert not verdict.breached

    def test_the_monitor_works_with_no_provider_at_all(self, span):
        # Not a convenience: without one every predicate is still evaluated and
        # every breach still raised. Degrade quality, never correctness.
        assert AssumptionMonitor().check(make_assumption(span), world(OVER)).breached


class TestConstruction:
    def test_a_threshold_outside_zero_to_one_is_refused(self):
        with pytest.raises(ValueError, match="between 0 and 1"):
            AssumptionMonitor(None, min_match_confidence=1.5)

    def test_the_clock_the_world_carries_is_what_an_expiry_is_read_against(self, span):
        assumption = make_assumption(span, expiry='after("2026-08-25T18:00:00+00:00")')
        early = check(assumption, world(now=AT))
        late = check(assumption, world(now=AT + timedelta(hours=12)))
        assert early.expiry.truth is Truth.FALSE
        assert late.expiry.truth is Truth.TRUE

    def test_a_naive_clock_never_reaches_the_monitor(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            WorldState(now=datetime(2026, 8, 25))  # noqa: DTZ001 -- the point of the test

    def test_utc_is_not_assumed_by_the_monitor_itself(self, span):
        # The world carries the clock; the monitor never reads one. Two runs
        # against the same world agree whatever machine they run on.
        state = world(now=datetime(2026, 8, 25, 12, 0, tzinfo=UTC))
        assert check(make_assumption(span), state).expiry.truth is Truth.FALSE

    def test_an_observed_ordinal_that_was_never_offered_is_ignored(self, span):
        # Both halves of a claimed match have to address something that was
        # really shown -- the same rule ADR 0015 makes about a span offering.
        provider = Answering([matches((0, 7))])
        state, _ = AssumptionMonitor(provider).enrich(
            world(events=["something"]), [make_assumption(span)]
        )
        assert not state.observed("the index is re-sharded")
