"""Pairing extracted claims with an answer key, against keys built to be awkward.

The interesting cases are all about ambiguity: two records over one passage,
one record spanning two items, a record that lands on a distractor, a record in
the right place in the wrong document. Each is a decision the pairing has to
make the same way twice, because a metric that depends on iteration order is a
metric two runs can disagree about.

The answer keys here are built by hand rather than generated, because each one
exists to pose one specific ambiguity. `tests/eval/test_harness.py` runs the
real generated corpus, which is what stops these fixtures drifting from it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from praxis.corpus.groundtruth import (
    Comparison,
    CorpusGroundTruth,
    DocumentGroundTruth,
    ExpectedField,
    ExpectedLink,
    GroundTruthItem,
    ItemKind,
)
from praxis.domain.enums import SourceKind
from praxis.domain.links import LinkType
from praxis.eval.matching import SET_SEPARATOR, Claim, field_matches, fusion_pairs, pair

FIRST = "a" * 64
SECOND = "b" * 64
AT = "2026-08-18T09:00:00Z"


def an_item(item_id: str, start: int, end: int, **kwargs) -> GroundTruthItem:
    """One answer-key entry over a byte range, with a quotation that fits it."""
    kwargs.setdefault("kind", ItemKind.DECISION)
    return GroundTruthItem(
        item_id=item_id, start_byte=start, end_byte=end, quote="x" * (end - start), **kwargs
    )


def a_key(*items: GroundTruthItem, content_hash: str = FIRST) -> CorpusGroundTruth:
    """A corpus of one document holding those items."""
    return CorpusGroundTruth(
        generator_version=1,
        seed=1,
        generated_at=AT,
        documents=(
            DocumentGroundTruth(
                path="documents/one.md",
                source_kind=SourceKind.MARKDOWN,
                content_sha256=content_hash,
                byte_length=1000,
                items=items,
            ),
        ),
    )


def a_claim(record_id: str, start: int, end: int, **kwargs) -> Claim:
    """One extracted record, reduced to what grading needs."""
    kwargs.setdefault("kind", ItemKind.DECISION)
    kwargs.setdefault("content_hash", FIRST)
    kwargs.setdefault("fields", {})
    return Claim(record_id=record_id, start_byte=start, end_byte=end, **kwargs)


class TestWhatCountsAsAMatch:
    def test_an_overlapping_span_matches(self):
        # Not equality: the key's ranges are written as the document is
        # assembled and the spans are cut by the block grid, so demanding
        # identical coordinates would grade the segmenter.
        result = pair([a_claim("D-0001", 10, 40)], a_key(an_item("i1", 20, 30)))
        assert [match.item.item_id for match in result.matched] == ["i1"]
        assert result.matched[0].overlap == 10

    def test_a_span_that_only_touches_does_not_match(self):
        # End bytes are exclusive, so [0, 20) and [20, 30) share nothing.
        result = pair([a_claim("D-0001", 0, 20)], a_key(an_item("i1", 20, 30)))
        assert result.matched == ()
        assert [claim.record_id for claim in result.spurious] == ["D-0001"]

    def test_the_right_place_in_the_wrong_document_does_not_match(self):
        result = pair(
            [a_claim("D-0001", 20, 30, content_hash=SECOND)], a_key(an_item("i1", 20, 30))
        )
        assert result.matched == ()
        assert len(result.missed) == 1

    def test_a_record_of_another_kind_does_not_answer_the_item(self):
        result = pair(
            [a_claim("A-0001", 20, 30, kind=ItemKind.ASSUMPTION)],
            a_key(an_item("i1", 20, 30)),
        )
        assert result.matched == ()

    def test_the_greatest_overlap_wins(self):
        result = pair(
            [a_claim("D-0001", 0, 22), a_claim("D-0002", 18, 40)],
            a_key(an_item("i1", 20, 40)),
        )
        assert result.matched[0].claim.record_id == "D-0002"

    def test_one_item_is_answered_once_and_the_rest_are_spurious(self):
        # The corpus says there is one decision there. Two records citing it are
        # one hit and one false positive, which is the honest reading.
        result = pair(
            [a_claim("D-0001", 20, 30), a_claim("D-0002", 21, 29)],
            a_key(an_item("i1", 20, 30)),
        )
        assert len(result.matched) == 1
        assert len(result.spurious) == 1

    def test_one_record_answers_only_one_item(self):
        result = pair(
            [a_claim("D-0001", 0, 100)], a_key(an_item("i1", 10, 20), an_item("i2", 30, 40))
        )
        assert len(result.matched) == 1
        assert len(result.missed) == 1

    def test_the_pairing_does_not_depend_on_the_order_it_was_given(self):
        # The property the whole ablation table rests on, at this level.
        claims = [a_claim("D-0002", 21, 29), a_claim("D-0001", 20, 30)]
        key = a_key(an_item("i1", 20, 30), an_item("i2", 100, 110))
        forwards = pair(claims, key)
        backwards = pair(list(reversed(claims)), key)
        assert [m.claim.record_id for m in forwards.matched] == [
            m.claim.record_id for m in backwards.matched
        ]

    def test_a_generator_of_claims_is_read_once_and_still_graded_fully(self):
        # `spurious` needs a second pass over the claims; a generator would be
        # empty by then and every false positive would vanish.
        claims = (a_claim(f"D-000{index}", 20, 30) for index in (1, 2))
        result = pair(claims, a_key(an_item("i1", 20, 30)))
        assert len(result.matched) == 1
        assert len(result.spurious) == 1


class TestDistractors:
    def test_a_record_on_a_distractor_is_a_false_positive_of_its_own_kind(self):
        # ADR 0012's labelled negatives paying for themselves: without them
        # only recall is measurable.
        result = pair([a_claim("D-0001", 20, 30)], a_key(an_item("i1", 20, 30, is_distractor=True)))
        assert result.matched == ()
        assert len(result.distracted) == 1
        assert result.false_positives == 1

    def test_a_distractor_nothing_landed_on_is_not_a_miss(self):
        # It was never an answer. Counting it would make resisting a trap look
        # like failing to find something.
        result = pair([], a_key(an_item("i1", 20, 30, is_distractor=True)))
        assert result.missed == ()

    def test_a_real_item_beats_a_distractor_for_the_same_record(self):
        result = pair(
            [a_claim("D-0001", 0, 100)],
            a_key(an_item("i1", 10, 90, is_distractor=True), an_item("i2", 20, 30)),
        )
        # Greatest overlap decides, and here the distractor overlaps more --
        # which is the honest answer: the record really is mostly on the trap.
        assert len(result.distracted) == 1
        assert len(result.matched) == 0


class TestGradingFields:
    def test_an_expected_field_is_compared_the_way_the_key_says(self):
        item = an_item(
            "i1",
            20,
            30,
            fields=(
                ExpectedField(name="chosen", value="OpenSearch", comparison=Comparison.CONTAINS),
            ),
        )
        result = pair(
            [a_claim("D-0001", 20, 30, fields={"chosen": "OpenSearch on managed nodes"})],
            a_key(item),
        )
        assert result.matched[0].exact
        assert result.matched[0].fields_correct == 1

    def test_a_field_the_record_never_filled_is_wrong_rather_than_absent(self):
        item = an_item("i1", 20, 30, fields=(ExpectedField(name="chosen", value="OpenSearch"),))
        result = pair([a_claim("D-0001", 20, 30)], a_key(item))
        assert not result.matched[0].exact
        assert result.matched[0].fields[0].actual == ""

    def test_a_match_with_nothing_expected_is_vacuously_exact(self):
        result = pair([a_claim("D-0001", 20, 30)], a_key(an_item("i1", 20, 30)))
        assert result.matched[0].exact


class TestTheComparisons:
    @pytest.mark.parametrize(("expected", "actual", "ok"), [("a", " a ", True), ("a", "b", False)])
    def test_exact_ignores_only_surrounding_whitespace(self, expected, actual, ok):
        assert field_matches(ExpectedField(name="f", value=expected), actual) is ok

    @pytest.mark.parametrize(
        ("expected", "actual", "ok"),
        [("OpenSearch", "we chose opensearch", True), ("OpenSearch", "we chose Solr", False)],
    )
    def test_contains_is_case_insensitive(self, expected, actual, ok):
        # For prose an extractor captures the substance and not the wording, so
        # a capital letter is not a wrong answer.
        field = ExpectedField(name="f", value=expected, comparison=Comparison.CONTAINS)
        assert field_matches(field, actual) is ok

    def test_a_set_ignores_the_order_it_was_listed_in(self):
        field = ExpectedField(name="f", value="a|b", comparison=Comparison.SET)
        assert field_matches(field, f"b{SET_SEPARATOR}a")
        assert not field_matches(field, "a")

    def test_a_set_ignores_empty_entries(self):
        field = ExpectedField(name="f", value="a|b", comparison=Comparison.SET)
        assert field_matches(field, "a| |b|")

    def test_numbers_compare_within_the_stated_tolerance(self):
        field = ExpectedField(
            name="f", value="4", comparison=Comparison.NUMERIC, tolerance=Decimal("0.5")
        )
        assert field_matches(field, "4.25")
        assert not field_matches(field, "5")

    def test_a_number_that_is_not_one_is_wrong_rather_than_an_error(self):
        # An extractor writing prose into a quantity is a bad answer, not a
        # crash in the grader.
        field = ExpectedField(name="f", value="4", comparison=Comparison.NUMERIC)
        assert not field_matches(field, "about four weeks")
        assert not field_matches(field, "")

    def test_quantities_are_compared_as_decimal(self):
        # Invariant 4 reaches the grader: 0.1 + 0.2 is a number a float would
        # get wrong and this comparison must not.
        field = ExpectedField(name="f", value="0.3", comparison=Comparison.NUMERIC)
        assert field_matches(field, str(Decimal("0.1") + Decimal("0.2")))


class TestTheFusionEdges:
    def test_the_labelled_fusion_edges_are_read_off_the_key(self):
        item = an_item(
            "a1",
            20,
            30,
            kind=ItemKind.ASSUMPTION,
            links=(ExpectedLink(link_type=LinkType.ESTIMATED_AS, target_item_id="e1"),),
        )
        assert fusion_pairs(a_key(item)) == (("a1", "e1"),)

    def test_other_edges_are_not_counted_as_fusion(self):
        item = an_item(
            "d1",
            20,
            30,
            links=(ExpectedLink(link_type=LinkType.ASSUMES, target_item_id="a1"),),
        )
        assert fusion_pairs(a_key(item)) == ()


def test_grading_one_kind_ignores_the_others_entirely():
    """Precision and recall are per kind, so the denominators must be too."""
    key = a_key(an_item("i1", 20, 30), an_item("a1", 40, 50, kind=ItemKind.ASSUMPTION))
    result = pair([a_claim("D-0001", 20, 30)], key, kind=ItemKind.DECISION)
    assert len(result.matched) == 1
    assert result.missed == ()
