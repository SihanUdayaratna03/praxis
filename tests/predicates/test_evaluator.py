"""The evaluator and the world it reads, held to the rules a breach rests on.

The property under nearly every test here is one sentence: **the facts can only
ever make a verdict more decided, never differently decided.** Measuring
something must not turn a holding assumption into a breached one or the reverse;
it may only move an undecided predicate off `UNKNOWN`. If that ever stops being
true, `AssumptionMonitor` can raise a breach against a decision because somebody
supplied a fact, and the whole monitor becomes something nobody should act on.

The Kleene tables are asserted exhaustively rather than sampled. There are nine
cases per connective and they are the entire semantics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, localcontext

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from praxis.predicates.ast import Evaluation, Truth, identifiers_in, unknown
from praxis.predicates.evaluator import evaluate, unbound_in
from praxis.predicates.parser import parse
from praxis.predicates.world import WorldState, normalise_event

from tests.predicates.test_grammar import formulas, names

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

HOLDS = "1 == 1"
VIOLATED = "1 == 2"
UNDECIDED = "unmeasured == 1"


def world(**facts: Decimal | bool | str) -> WorldState:
    """A world at a fixed time, holding only what a test names."""
    return WorldState(now=NOW, facts=facts)


def truth_of(source: str, state: WorldState) -> Truth:
    """The truth value of a predicate, for a table-driven assertion."""
    return evaluate(parse(source), state).truth


class TestTheWorldState:
    def test_a_naive_clock_is_refused(self):
        # Invariant 5. An expiry is a comparison against wall-clock time, and a
        # naive one fires at a different moment on two machines.
        with pytest.raises(ValueError, match="timezone-aware"):
            WorldState(now=datetime(2026, 8, 25, 12, 0))  # noqa: DTZ001 -- the point of the test

    def test_the_facts_cannot_be_changed_under_an_evaluation(self):
        facts: dict[str, Decimal] = {"a": Decimal(1)}
        state = WorldState(now=NOW, facts=facts)
        facts["a"] = Decimal(99)
        assert state.value_of("a") == Decimal(1)

    def test_an_unmeasured_identifier_is_none_rather_than_zero(self):
        assert world().value_of("anything") is None

    def test_an_event_matches_across_case_and_whitespace(self):
        state = WorldState(now=NOW, events=frozenset({"The  Work   Ships"}))
        assert state.observed("the work ships")

    def test_an_event_that_differs_by_more_than_spelling_does_not_match(self):
        # Deliberately not fuzzy. Recognising "the search rollout completed" as
        # "the work ships" is a judgement, and it is made in AssumptionMonitor
        # on a path that can only age an assumption, never breach one.
        state = WorldState(now=NOW, events=frozenset({"the search rollout completed"}))
        assert not state.observed("the work ships")

    def test_normalising_an_event_collapses_only_spelling(self):
        assert normalise_event("  The\tWork \n Ships ") == "the work ships"

    def test_adding_facts_returns_a_new_world(self):
        original = world(a=Decimal(1))
        extended = original.with_facts({"b": Decimal(2)})
        assert original.value_of("b") is None
        assert extended.value_of("a") == Decimal(1)

    def test_a_later_fact_wins(self):
        # The layering praxis.monitor.facts relies on: a facts file supplied by
        # a person overrides what the store derived.
        assert world(a=Decimal(1)).with_facts({"a": Decimal(2)}).value_of("a") == Decimal(2)

    def test_adding_events_returns_a_new_world(self):
        original = world()
        assert original.with_events("it shipped").observed("it shipped")
        assert not original.observed("it shipped")


class TestComparingKnownValues:
    def test_a_measured_quantity_can_hold(self):
        assert truth_of("index_size_gb <= 50", world(index_size_gb=Decimal(40))) is Truth.TRUE

    def test_a_measured_quantity_can_be_violated(self):
        assert truth_of("index_size_gb <= 50", world(index_size_gb=Decimal(60))) is Truth.FALSE

    def test_a_boolean_compares_by_equality(self):
        assert truth_of("valid == true", world(valid=True)) is Truth.TRUE
        assert truth_of("valid == false", world(valid=True)) is Truth.FALSE

    def test_a_string_compares_by_equality(self):
        assert truth_of('stage == "closed"', world(stage="closed")) is Truth.TRUE

    def test_arithmetic_is_evaluated_before_the_comparison(self):
        state = world(parsed=Decimal(64), total=Decimal(65))
        assert truth_of("parsed / total >= 0.9", state) is Truth.TRUE

    def test_a_quantity_is_decimal_and_the_arithmetic_stays_exact(self):
        # Invariant 4 reaches the evaluator. 0.1 + 0.2 <= 0.3 is false in binary
        # floating point and true here, and a predicate over money or a rate is
        # exactly where that difference gets read off a table by a judge.
        assert truth_of("a + b <= 0.3", world(a=Decimal("0.1"), b=Decimal("0.2"))) is Truth.TRUE


class TestWhatTheFactsDoNotSettle:
    def test_an_unmeasured_identifier_is_unknown_and_not_false(self):
        # The rule the whole phase rests on. Answering "I was not told" with
        # "your decision is broken" is the failure mode that would make the
        # monitor untrustworthy.
        evaluation = evaluate(parse("index_size_gb <= 50"), world())
        assert evaluation.truth is Truth.UNKNOWN
        assert "index_size_gb" in evaluation.reason

    def test_a_division_by_zero_is_unknown_and_not_false(self):
        # A ratio over a denominator nothing has counted yet, which is what
        # ADR 0001's own first assumption looks like before Phase 5 exists.
        state = world(parsed=Decimal(64), total=Decimal(0))
        evaluation = evaluate(parse("parsed / total >= 0.9"), state)
        assert evaluation.truth is Truth.UNKNOWN
        assert "cannot be computed" in evaluation.reason

    def test_two_kinds_that_cannot_be_compared_are_unknown(self):
        evaluation = evaluate(parse("valid == 1"), world(valid=True))
        assert evaluation.truth is Truth.UNKNOWN
        assert "cannot be compared" in evaluation.reason

    def test_ordering_two_booleans_is_unknown(self):
        evaluation = evaluate(parse("valid < true"), world(valid=False))
        assert evaluation.truth is Truth.UNKNOWN
        assert "has no order" in evaluation.reason

    def test_ordering_two_strings_is_unknown(self):
        assert truth_of('stage < "closed"', world(stage="open")) is Truth.UNKNOWN

    def test_arithmetic_on_a_string_is_unknown(self):
        evaluation = evaluate(parse("stage + 1 >= 2"), world(stage="open"))
        assert evaluation.truth is Truth.UNKNOWN
        assert "needs two numbers" in evaluation.reason

    def test_an_unknown_verdict_always_carries_a_reason(self):
        assert evaluate(parse("a >= 1"), world()).reason != ""

    def test_the_unmeasured_names_are_reported_rather_than_left_to_be_guessed(self):
        unbound = unbound_in(parse("a + b >= c"), world(c=Decimal(1)))
        assert unbound == {"a", "b"}


class TestKleeneSemantics:
    @pytest.mark.parametrize(
        ("left", "right", "expected"),
        [
            (HOLDS, HOLDS, Truth.TRUE),
            (HOLDS, VIOLATED, Truth.FALSE),
            (HOLDS, UNDECIDED, Truth.UNKNOWN),
            (VIOLATED, HOLDS, Truth.FALSE),
            (VIOLATED, VIOLATED, Truth.FALSE),
            (VIOLATED, UNDECIDED, Truth.FALSE),
            (UNDECIDED, HOLDS, Truth.UNKNOWN),
            (UNDECIDED, VIOLATED, Truth.FALSE),
            (UNDECIDED, UNDECIDED, Truth.UNKNOWN),
        ],
    )
    def test_and_is_false_whenever_either_side_is(self, left, right, expected):
        # Strictly more informative than propagating the unknown: no
        # measurement could rescue a conjunction with a false half.
        assert truth_of(f"({left}) and ({right})", world()) is expected

    @pytest.mark.parametrize(
        ("left", "right", "expected"),
        [
            (HOLDS, HOLDS, Truth.TRUE),
            (HOLDS, VIOLATED, Truth.TRUE),
            (HOLDS, UNDECIDED, Truth.TRUE),
            (VIOLATED, HOLDS, Truth.TRUE),
            (VIOLATED, VIOLATED, Truth.FALSE),
            (VIOLATED, UNDECIDED, Truth.UNKNOWN),
            (UNDECIDED, HOLDS, Truth.TRUE),
            (UNDECIDED, VIOLATED, Truth.UNKNOWN),
            (UNDECIDED, UNDECIDED, Truth.UNKNOWN),
        ],
    )
    def test_or_is_true_whenever_either_side_is(self, left, right, expected):
        assert truth_of(f"({left}) or ({right})", world()) is expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [(HOLDS, Truth.FALSE), (VIOLATED, Truth.TRUE), (UNDECIDED, Truth.UNKNOWN)],
    )
    def test_not_leaves_an_undecided_verdict_undecided(self, source, expected):
        assert truth_of(f"not ({source})", world()) is expected

    def test_a_negated_unknown_keeps_the_reason_it_could_not_be_decided(self):
        assert "unmeasured" in evaluate(parse(f"not ({UNDECIDED})"), world()).reason


class TestTheMonotonicityProperty:
    """Facts may decide a predicate. They may never change a decided one."""

    @settings(suppress_health_check=[HealthCheck.too_slow])
    @given(
        formula=formulas(depth=2),
        extra=st.dictionaries(names(), st.integers(-100, 100).map(Decimal), max_size=4),
    )
    def test_measuring_something_never_flips_a_verdict(self, formula, extra):
        # The safety property the monitor rests on. If this fails, supplying a
        # fact can raise a breach against a decision that was holding, and no
        # number the monitor reports would be worth acting on.
        empty = WorldState(now=NOW)
        before = evaluate(formula, empty)
        after = evaluate(formula, empty.with_facts(extra))
        if before.decided:
            assert after.truth is before.truth

    @given(formula=formulas(depth=2))
    def test_binding_every_name_a_predicate_mentions_leaves_nothing_unbound(self, formula):
        empty = WorldState(now=NOW)
        missing = unbound_in(formula, empty)
        assert missing == identifiers_in(formula)
        filled = empty.with_facts(dict.fromkeys(missing, Decimal(1)))
        assert unbound_in(formula, filled) == frozenset()


class TestDeterminism:
    def test_the_verdict_does_not_depend_on_the_callers_decimal_context(self):
        # decimal's context is per-thread and anyone can change it. Two thirds
        # rounds to 0.667 at three digits and to 0.666... at twenty-eight, and
        # the threshold below sits between them -- so a verdict that inherited
        # the caller's context would be TRUE here and FALSE one frame up.
        state = world(parsed=Decimal(2), total=Decimal(3))
        outside = truth_of("parsed / total >= 0.6667", state)
        with localcontext() as context:
            context.prec = 3
            inside = truth_of("parsed / total >= 0.6667", state)
        assert inside is outside is Truth.FALSE

    @given(formulas(depth=2))
    def test_evaluating_twice_gives_the_same_answer(self, formula):
        empty = WorldState(now=NOW)
        assert evaluate(formula, empty) == evaluate(formula, empty)


class TestTheEvaluationType:
    def test_an_undecided_verdict_must_say_why(self):
        # "Unknown" with no explanation is a verdict nobody can act on, and the
        # reason is the field a person actually reads.
        with pytest.raises(ValueError, match="without a reason"):
            Evaluation(Truth.UNKNOWN)

    def test_a_decided_verdict_carries_no_reason(self):
        # A false verdict with an explanation attached would read as an excuse,
        # and there is nowhere for one to have come from.
        with pytest.raises(ValueError, match="carries no reason"):
            Evaluation(Truth.FALSE, "because")

    def test_only_false_counts_as_a_violation(self):
        assert Evaluation(Truth.FALSE).violated
        assert not unknown("nothing measured it").violated
        assert not Evaluation(Truth.TRUE).violated

    def test_only_true_counts_as_holding(self):
        assert Evaluation(Truth.TRUE).holds
        assert not unknown("nothing measured it").holds
