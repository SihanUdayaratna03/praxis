"""The lexer, the parser and the renderer, held to each other.

The property that matters most is the round trip: anything the parser builds,
the renderer writes back as text the parser reads to the same tree. It is the
cheapest evidence there is that a grammar means one thing, and it is what lets
`AssumptionFormalizer` store a *rendered* predicate without worrying that
normalising the spelling changed the claim.

Every literal predicate in the parser tests below is one this repository really
contains -- pulled from `praxis/corpus/topics.py` and from the assumption tables
in `docs/adr/`. A grammar tested only against predicates invented for the test
is a grammar tested against itself.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.predicates.ast import (
    Arithmetic,
    ArithOp,
    CompareOp,
    Comparison,
    Conjunction,
    Constant,
    Disjunction,
    Formula,
    Identifier,
    Negation,
    Term,
    identifiers_in,
    render,
)
from praxis.predicates.errors import PredicateSyntaxError
from praxis.predicates.lexer import TokenKind, tokenize
from praxis.predicates.parser import parse, parses

RESERVED = frozenset({"and", "or", "not", "between", "true", "false"})


def names() -> st.SearchStrategy[str]:
    """Identifiers in the style every predicate in this project is written in."""
    return st.from_regex(r"\A[a-z][a-z0-9_]{0,10}\Z").filter(lambda name: name not in RESERVED)


def constants() -> st.SearchStrategy[Constant]:
    """Literals of all three kinds, kept inside what plain notation can write.

    Exponent notation is excluded deliberately rather than accidentally: the
    grammar has no rule for `1E+3`, so generating one would be testing a
    predicate no author of one has ever written.
    """
    return st.builds(
        Constant,
        st.one_of(
            st.integers(min_value=-(10**6), max_value=10**6).map(Decimal),
            st.decimals(min_value=-1000, max_value=1000, places=2),
            st.booleans(),
            st.text(max_size=8),
        ),
    )


def terms(depth: int) -> st.SearchStrategy[Term]:
    """Arithmetic trees, bounded so shrinking stays fast."""
    leaves = st.one_of(constants(), names().map(Identifier))
    if depth <= 0:
        return leaves
    return st.one_of(
        leaves,
        st.builds(Arithmetic, st.sampled_from(ArithOp), terms(depth - 1), terms(depth - 1)),
    )


def formulas(depth: int) -> st.SearchStrategy[Formula]:
    """Whole predicates, bounded the same way."""
    comparisons = st.builds(Comparison, st.sampled_from(CompareOp), terms(1), terms(1))
    if depth <= 0:
        return comparisons
    return st.one_of(
        comparisons,
        st.builds(Negation, formulas(depth - 1)),
        st.builds(Conjunction, formulas(depth - 1), formulas(depth - 1)),
        st.builds(Disjunction, formulas(depth - 1), formulas(depth - 1)),
    )


class TestTheLexer:
    def test_a_digit_separator_is_read_as_part_of_the_number(self):
        # Two of this project's own ADRs write a threshold this way, so a
        # grammar that refused it would fail ADR 0001's first assumption by
        # being too strict rather than by being wrong.
        token = tokenize("edge_count < 1_000_000")[2]
        assert token.kind is TokenKind.NUMBER
        assert token.literal == Decimal(1000000)

    def test_a_number_is_a_decimal_and_never_a_float(self):
        token = tokenize("rate <= 0.1")[2]
        assert isinstance(token.literal, Decimal)
        assert token.literal == Decimal("0.1")

    def test_keywords_are_matched_whatever_their_case(self):
        kinds = [token.kind for token in tokenize("a == 1 AND b == 2")]
        assert TokenKind.AND in kinds

    def test_an_identifier_keeps_its_case(self):
        # Reading is permissive, writing is not. Folding these together would
        # merge two quantities, and therefore two assumptions.
        first, second = tokenize("Index_Gb")[0], tokenize("index_gb")[0]
        assert first.literal != second.literal

    def test_true_and_false_are_literals_rather_than_names(self):
        token = tokenize("all_routed_models_valid == true")[2]
        assert token.kind is TokenKind.BOOLEAN
        assert token.literal is True

    def test_a_quoted_string_comes_back_unescaped(self):
        token = tokenize(r'note == "a \"quoted\" word"')[2]
        assert token.literal == 'a "quoted" word'

    def test_a_single_equals_is_reported_by_name(self):
        # The single most likely thing to come back from a model asked for an
        # expression, so it gets the one line an author will read.
        with pytest.raises(PredicateSyntaxError, match="equality is written '=='"):
            tokenize("index_size_gb = 50")

    def test_an_unterminated_string_says_so(self):
        with pytest.raises(PredicateSyntaxError, match="never closed"):
            tokenize('note == "unfinished')

    def test_an_unknown_character_is_named_and_placed(self):
        with pytest.raises(PredicateSyntaxError) as raised:
            tokenize("a == 1 ` b")
        assert raised.value.position == 7

    def test_the_list_always_ends_with_an_end_token(self):
        # So the parser never checks for the end of a list separately from
        # checking what comes next.
        assert tokenize("")[-1].kind is TokenKind.END
        assert tokenize("a == 1")[-1].kind is TokenKind.END


class TestWhatTheParserAccepts:
    @pytest.mark.parametrize(
        "source",
        [
            "index_size_gb <= 50",
            "analyses_needing_raw_events_over_90_days == 0",
            "self_hosted_job_share >= 0.8",
            "all_routed_models_valid == true",
            "adr_predicates_parsed / adr_predicates_total >= 0.9",
            "segmenter_f1 - paragraph_floor_f1 >= 0.05",
            "sonnet_5_input_usd_per_mtok <= 3.00",
            "edge_count < 1_000_000",
            "commits_per_phase between 8 and 20",
            "data_dir_under_sync_root == false",
        ],
    )
    def test_every_predicate_this_repository_contains_parses(self, source):
        assert parses(source)

    def test_and_binds_tighter_than_or(self):
        parsed = parse("a == 1 or b == 2 and c == 3")
        assert isinstance(parsed, Disjunction)
        assert isinstance(parsed.right, Conjunction)

    def test_not_binds_tighter_than_and(self):
        parsed = parse("not a == 1 and b == 2")
        assert isinstance(parsed, Conjunction)
        assert isinstance(parsed.left, Negation)

    def test_multiplication_binds_tighter_than_addition(self):
        parsed = parse("a + b * c == 0")
        assert isinstance(parsed, Comparison)
        assert isinstance(parsed.left, Arithmetic)
        assert parsed.left.op is ArithOp.ADD

    def test_between_desugars_to_the_conjunction_it_means(self):
        # Sugar rather than a node, so `praxis.predicates.intervals` has one
        # shape to understand instead of two.
        assert parse("commits_per_phase between 8 and 20") == parse(
            "commits_per_phase >= 8 and commits_per_phase <= 20"
        )

    def test_a_sign_on_a_literal_is_folded_into_it(self):
        parsed = parse("x >= -1")
        assert isinstance(parsed, Comparison)
        assert parsed.right == Constant(Decimal(-1))

    def test_a_leading_plus_is_read_and_changes_nothing(self):
        assert parse("x >= +1") == parse("x >= 1")

    def test_a_sign_on_a_name_becomes_a_subtraction_from_zero(self):
        # A type error for the evaluator to report, not a shape for the parser
        # to refuse.
        parsed = parse("-x >= 0")
        assert isinstance(parsed, Comparison)
        assert parsed.left == Arithmetic(ArithOp.SUBTRACT, Constant(Decimal(0)), Identifier("x"))


class TestTheAmbiguousBracket:
    """`(` opens an arithmetic group or a formula, and only lookahead decides."""

    def test_a_bracket_followed_by_a_comparison_was_arithmetic(self):
        assert parse("(a + 1) <= 5") == parse("a + 1 <= 5")

    def test_a_bracket_followed_by_nothing_that_continues_it_was_a_formula(self):
        parsed = parse("(a <= 1) and b == 2")
        assert isinstance(parsed, Conjunction)

    def test_a_formula_may_be_bracketed_under_a_negation(self):
        # The case that makes the rewind worth having: forbidding brackets
        # around a formula would reject this outright.
        parsed = parse("not (a == 1 or b == 2)")
        assert isinstance(parsed, Negation)
        assert isinstance(parsed.operand, Disjunction)

    def test_a_bracketed_name_still_reads_as_a_quantity(self):
        assert parse("(a) <= 5") == parse("a <= 5")

    def test_a_formula_used_as_a_quantity_is_refused_rather_than_reinterpreted(self):
        # `(a == b)` parses as a formula and then a comparison follows it, so
        # the lookahead rewinds and tries it as arithmetic -- where it fails.
        # Refusing is the point: quietly picking one of the two readings is how
        # a predicate ends up meaning something nobody wrote.
        assert not parses("(a == b) == true")


class TestWhatTheParserRefuses:
    def test_a_quantity_on_its_own_is_not_a_predicate(self):
        # `index_size_gb` is a quantity, not a claim. Accepting it would let a
        # predicate truncated by a bad model answer parse as though it said
        # something.
        with pytest.raises(PredicateSyntaxError, match="has to compare something"):
            parse("index_size_gb")

    def test_an_empty_predicate_says_there_is_nothing_here(self):
        with pytest.raises(PredicateSyntaxError, match="no predicate here"):
            parse("   ")

    def test_between_without_its_second_bound_is_refused(self):
        with pytest.raises(PredicateSyntaxError, match="two bounds"):
            parse("commits between 8")

    def test_the_one_adr_predicate_written_in_prose_does_not_parse(self):
        # ADR 0015 assumption 3 says a ratio should be `outside [0.5, 2.0]`,
        # which is English rather than an expression. It is left failing on
        # purpose: ADR 0001's first assumption exists to surface exactly this,
        # and widening the grammar for a single instance would be answering the
        # question by editing it. Reported as a finding in docs/reports/phase-5.md.
        assert not parses("mis_attribution_rate / fabrication_rate outside [0.5, 2.0]")

    def test_the_adr_template_placeholder_does_not_parse(self):
        assert not parses("<expression>")

    def test_a_trailing_operator_is_refused_rather_than_half_parsed(self):
        # A predicate that half-parses is one whose meaning nobody knows.
        with pytest.raises(PredicateSyntaxError):
            parse("a <= 5 and")

    def test_the_error_points_at_the_character_to_look_at(self):
        with pytest.raises(PredicateSyntaxError) as raised:
            parse("a <= 5 and ?")
        assert raised.value.position == 11
        assert "?" in str(raised.value)


class TestRendering:
    def test_a_rendered_predicate_is_normalised(self):
        # Why the formalizer stores the rendering rather than the model's
        # spelling: two agents saying the same thing produce one string, and
        # therefore one blocking bucket.
        assert render(parse("x<=50")) == render(parse("x  <=  50")) == "x <= 50"

    def test_brackets_are_kept_only_where_dropping_them_would_change_the_meaning(self):
        assert render(parse("not (a == 1 or b == 2)")) == "not (a == 1 or b == 2)"
        assert render(parse("(a == 1) and b == 2")) == "a == 1 and b == 2"

    def test_a_right_hand_subtraction_keeps_its_brackets(self):
        # `a - (b - c)` is not `a - b - c`, and the renderer brackets the right
        # side one level earlier than the left for exactly this.
        assert render(parse("a - (b - c) == 0")) == "a - (b - c) == 0"

    def test_a_boolean_renders_as_the_word_the_lexer_reads(self):
        assert render(Constant(value=True)) == "true"

    def test_a_string_renders_with_its_escapes_put_back(self):
        assert render(Constant(value='say "hi"')) == r'"say \"hi\""'


class TestTheRoundTrip:
    @given(formulas(depth=2))
    def test_anything_rendered_parses_back_to_the_same_tree(self, formula):
        assert parse(render(formula)) == formula

    @given(formulas(depth=2))
    def test_rendering_is_idempotent(self, formula):
        once = render(formula)
        assert render(parse(once)) == once


class TestIdentifiersIn:
    def test_every_name_a_predicate_mentions_is_reported(self):
        assert identifiers_in(parse("a / b >= c and not d == 1")) == {"a", "b", "c", "d"}

    def test_a_predicate_over_literals_alone_mentions_nothing(self):
        assert identifiers_in(parse("1 <= 2")) == frozenset()

    @given(formulas(depth=2))
    def test_the_names_are_exactly_those_the_rendering_could_contain(self, formula):
        # The blocking key ContradictionDetector buckets on, so a name it misses
        # is a pair that is never proposed and therefore never found.
        rendered = render(formula)
        assert all(name in rendered for name in identifiers_in(formula))
