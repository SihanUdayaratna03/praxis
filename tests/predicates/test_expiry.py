"""Expiry conditions and the interval arithmetic, tested together.

Two modules in one file because both are the same kind of thing: deterministic
code that decides something a model would otherwise be asked, and both are held
to the same standard -- **never answer when the answer is not certain.** An
expiry that cannot be decided does not expire anything, and two constraints
arithmetic cannot separate are not thereby compatible.

Every expiry condition asserted here is one this repository really writes. The
`when`, `after` and `on_event` forms are not a design; they are what
`docs/adr/` and `praxis/corpus/topics.py` already contain, and a fourth form
would be one nothing writes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.predicates.ast import Truth
from praxis.predicates.errors import ExpiryError, PredicateSyntaxError
from praxis.predicates.expiry import (
    AfterInstant,
    OnEvent,
    WhenPredicate,
    expires,
    identifiers_in_expiry,
    parse_expiry,
    parses_expiry,
    render_expiry,
)
from praxis.predicates.intervals import (
    ExactValue,
    ExcludedValue,
    NumericRange,
    conflict,
    constraints_of,
)
from praxis.predicates.parser import parse
from praxis.predicates.world import WorldState

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def world(**facts: Decimal | bool | str) -> WorldState:
    """A world at a fixed time, holding only what a test names."""
    return WorldState(now=NOW, facts=facts)


def fired(source: str, state: WorldState) -> Truth:
    """Whether an expiry condition has fired, as a truth value."""
    return expires(parse_expiry(source), state).truth


class TestReadingAnExpiryCondition:
    @pytest.mark.parametrize(
        "source",
        [
            'on_event("Phase 5 predicate DSL is implemented")',
            'on_event("Anthropic deprecates a routed model")',
            "when(phases_completed >= 6)",
            "when(edge_count > 500_000)",
            'after("2026-08-31")',
            'on_event("the work ships")',
        ],
    )
    def test_every_expiry_condition_this_repository_contains_parses(self, source):
        assert parses_expiry(source)

    def test_a_when_carries_the_predicate_it_names(self):
        assert parse_expiry("when(phases_completed >= 6)") == WhenPredicate(
            predicate=parse("phases_completed >= 6")
        )

    def test_an_on_event_carries_the_description_unescaped(self):
        assert parse_expiry(r'on_event("the \"real\" launch")') == OnEvent(
            description='the "real" launch'
        )

    def test_a_bare_date_is_read_at_utc_midnight(self):
        # Reading it in the local zone would expire an assumption at a different
        # moment in Colombo and in CI. Invariant 5 exists for exactly this.
        assert parse_expiry('after("2026-08-31")') == AfterInstant(
            instant=datetime(2026, 8, 31, tzinfo=UTC)
        )

    def test_an_instant_that_names_its_own_zone_keeps_it(self):
        offset = timezone(timedelta(hours=5, minutes=30))
        assert parse_expiry('after("2026-08-31T09:00:00+05:30")') == AfterInstant(
            instant=datetime(2026, 8, 31, 9, 0, tzinfo=offset)
        )


class TestWhatIsNotAnExpiryCondition:
    def test_the_adr_template_placeholder_is_refused(self):
        assert not parses_expiry("<when to re-check>")

    def test_a_form_this_project_does_not_write_is_refused(self):
        with pytest.raises(ExpiryError, match=r"when\(\.\.\.\), after"):
            parse_expiry('before("2026-08-31")')

    def test_trailing_text_is_a_refusal_rather_than_ignored(self):
        assert not parses_expiry('on_event("shipped") and also later')

    def test_a_malformed_inner_predicate_raises_a_syntax_error_not_an_expiry_error(self):
        # The two send a reader to different places: "that is not an expiry
        # condition" and "that expression is malformed".
        with pytest.raises(PredicateSyntaxError):
            parse_expiry("when(a ==)")

    def test_an_unquoted_event_is_refused(self):
        with pytest.raises(ExpiryError, match="one quoted string"):
            parse_expiry("on_event(the work ships)")

    def test_a_date_that_is_not_iso_8601_is_refused(self):
        with pytest.raises(ExpiryError, match="ISO-8601"):
            parse_expiry('after("next Tuesday")')


class TestWhetherAnExpiryHasFired:
    def test_a_when_fires_once_its_predicate_becomes_true(self):
        state = world(phases_completed=Decimal(6))
        assert fired("when(phases_completed >= 6)", state) is Truth.TRUE

    def test_a_when_has_not_fired_while_its_predicate_is_false(self):
        state = world(phases_completed=Decimal(4))
        assert fired("when(phases_completed >= 6)", state) is Truth.FALSE

    def test_a_when_over_an_unmeasured_quantity_is_undecided(self):
        # Not "has not fired". The distinction is the whole reason firing is
        # three-valued: an undecided condition expires nothing, and the monitor
        # must not read silence as an answer.
        assert fired("when(corpus_documents > 500)", world()) is Truth.UNKNOWN

    def test_an_after_fires_once_the_clock_passes_it(self):
        assert fired('after("2026-01-01")', world()) is Truth.TRUE
        assert fired('after("2026-12-31")', world()) is Truth.FALSE

    def test_an_after_fires_exactly_at_the_instant_it_names(self):
        state = WorldState(now=datetime(2026, 8, 31, tzinfo=UTC))
        assert fired('after("2026-08-31")', state) is Truth.TRUE

    def test_an_on_event_fires_when_the_event_was_observed(self):
        state = WorldState(now=NOW, events=frozenset({"The Work  Ships"}))
        assert fired('on_event("the work ships")', state) is Truth.TRUE

    def test_an_unobserved_event_has_not_fired_rather_than_being_undecided(self):
        # The observations are a closed list of what has been recorded, so "not
        # among them" is an answer. Reading it as undecided would leave every
        # on_event condition permanently unresolvable.
        assert fired('on_event("the work ships")', world()) is Truth.FALSE


class TestRenderingAnExpiry:
    @pytest.mark.parametrize(
        "source",
        [
            "when(phases_completed >= 6)",
            'on_event("the work ships")',
            'after("2026-08-31T00:00:00+00:00")',
        ],
    )
    def test_a_rendered_condition_reads_back_to_the_same_condition(self, source):
        assert parse_expiry(render_expiry(parse_expiry(source))) == parse_expiry(source)

    def test_rendering_normalises_a_bare_date_into_an_instant(self):
        assert render_expiry(parse_expiry('after("2026-08-31")')) == (
            'after("2026-08-31T00:00:00+00:00")'
        )

    def test_rendering_normalises_the_predicate_inside_a_when(self):
        assert render_expiry(parse_expiry("when( a<=1 )")) == "when(a <= 1)"


class TestWhatAnExpiryDependsOn:
    def test_a_when_reports_the_quantities_it_measures(self):
        assert identifiers_in_expiry(parse_expiry("when(a + b > 5)")) == {"a", "b"}

    def test_the_two_forms_that_measure_nothing_report_nothing(self):
        assert identifiers_in_expiry(parse_expiry('after("2026-08-31")')) == frozenset()
        assert identifiers_in_expiry(parse_expiry('on_event("shipped")')) == frozenset()


class TestReadingAPredicateAsConstraints:
    def test_a_simple_comparison_becomes_one_constraint(self):
        assert constraints_of(parse("index_size_gb <= 50")) == (
            NumericRange(
                "index_size_gb", low=None, low_closed=False, high=Decimal(50), high_closed=True
            ),
        )

    def test_a_comparison_written_backwards_is_normalised(self):
        assert constraints_of(parse("50 >= index_size_gb")) == constraints_of(
            parse("index_size_gb <= 50")
        )

    def test_a_conjunction_yields_every_constraint_in_it(self):
        assert len(constraints_of(parse("a <= 1 and b >= 2"))) == 2

    def test_an_equality_against_a_number_is_a_closed_point(self):
        assert constraints_of(parse("count == 3")) == (
            NumericRange(
                "count", low=Decimal(3), low_closed=True, high=Decimal(3), high_closed=True
            ),
        )

    def test_an_equality_against_a_boolean_is_not_a_range(self):
        # A boolean has no order, so a point on the number line is the wrong
        # shape for it -- and `valid == true` against `valid == false` still has
        # to be detectable.
        assert constraints_of(parse("valid == true")) == (ExactValue("valid", True),)

    def test_an_inequality_is_a_hole_rather_than_a_region(self):
        assert constraints_of(parse('stage != "open"')) == (ExcludedValue("stage", "open"),)

    @pytest.mark.parametrize(
        "source",
        [
            "a <= 1 or b >= 2",
            "not a <= 1",
            "a / b <= 1",
            "a <= b",
            "a + 1 <= 2",
            "a <= 1 and (b <= 2 or c <= 3)",
        ],
    )
    def test_anything_that_is_not_a_conjunction_of_simple_comparisons_yields_nothing(self, source):
        # Empty means "arithmetic has nothing to say", never "no constraint".
        # Reading a disjunction as its readable half would silently weaken the
        # claim, and a weakened claim is what produces a false contradiction.
        assert constraints_of(parse(source)) == ()


class TestConstraintsThatCannotBothHold:
    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("index_size_gb <= 50", "index_size_gb > 50"),
            ("index_size_gb <= 50", "index_size_gb >= 60"),
            ("index_size_gb >= 60", "index_size_gb <= 50"),
            ("index_size_gb < 50", "index_size_gb >= 50"),
            ("count == 3", "count == 4"),
            ("valid == true", "valid == false"),
            ('stage == "open"', 'stage != "open"'),
        ],
    )
    def test_a_provable_conflict_is_found_without_a_model(self, first, second):
        assert _first_conflict(first, second) is not None

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("index_size_gb <= 50", "index_size_gb <= 40"),
            ("index_size_gb <= 50", "index_size_gb >= 50"),
            ("index_size_gb <= 50", "other_thing > 50"),
            ("count == 3", "count != 4"),
            ('stage == "open"', 'stage != "closed"'),
            ("a >= 1", "a >= 2"),
            ("a >= 60", "a > 50"),
            ("a > 5", "a >= 5"),
            ("a < 5", "a <= 5"),
            ("valid == true", "valid == true"),
        ],
    )
    def test_constraints_that_can_both_hold_are_not_reported(self, first, second):
        assert _first_conflict(first, second) is None

    def test_a_shared_endpoint_is_only_a_conflict_when_one_side_excludes_it(self):
        assert _first_conflict("a <= 50", "a >= 50") is None
        assert _first_conflict("a < 50", "a >= 50") is not None

    def test_two_constraints_about_different_names_never_conflict(self):
        assert _first_conflict("a <= 1", "b >= 2") is None

    def test_the_rationale_spells_a_value_the_way_the_predicate_did(self):
        # A person reading the edge should see `true`, not Python's `True`.
        assert _first_conflict("valid == true", "valid == false") == (
            "valid cannot be both true and false"
        )

    def test_a_point_reads_as_a_point_in_the_rationale(self):
        rationale = _first_conflict("count == 3", "count == 4")
        assert rationale is not None
        assert "exactly 3" in rationale
        assert "exactly 4" in rationale

    def test_the_rationale_names_both_requirements(self):
        rationale = _first_conflict("index_size_gb <= 50", "index_size_gb > 50")
        assert rationale is not None
        assert "at most 50" in rationale
        assert "over 50" in rationale

    @given(
        low=st.integers(-50, 50).map(Decimal),
        high=st.integers(-50, 50).map(Decimal),
    )
    def test_two_ranges_conflict_exactly_when_no_number_satisfies_both(self, low, high):
        # The property behind every arithmetic contradiction this phase writes:
        # a conflict is reported if and only if the two permitted sets are
        # disjoint, and a false contradiction is the expensive way to be wrong.
        found = _first_conflict(f"a >= {low}", f"a <= {high}")
        assert (found is not None) == (low > high)


def _first_conflict(first: str, second: str) -> str | None:
    """The first incompatibility between two predicates, or `None`."""
    return next(
        (
            found
            for left in constraints_of(parse(first))
            for right in constraints_of(parse(second))
            if (found := conflict(left, right)) is not None
        ),
        None,
    )
