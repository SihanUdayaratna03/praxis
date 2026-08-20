"""The scoring arithmetic, stated over generated counts rather than examples.

Invariant 3: statistics are deterministic code, never a model, and property-
tested rather than trusted. That is why `praxis.eval.metrics` knows nothing
about matching -- it takes counts, so `hypothesis` can hand it any counts at
all rather than only the ones a plausible-looking run would produce.

The examples below the properties are the two conventions, which are choices
and not consequences, and a property test cannot tell that a chosen convention
was chosen wrongly.

`max_examples` is bounded on purpose: these run in the pre-commit loop, and a
thirty-second hook is a hook that gets disabled.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from praxis.agents.errors import Refusal
from praxis.agents.results import Refused, Stage
from praxis.corpus.groundtruth import ItemKind
from praxis.domain.enums import RecordKind
from praxis.domain.ids import DocumentId
from praxis.eval.metrics import (
    EMPTY,
    PERFECT,
    RATE_PLACES,
    CitationIntegrity,
    Score,
    citation_integrity,
    fusion_recall,
)

EXAMPLES = 60
"""Enough to reach the awkward end of the input space, few enough to stay in
the pre-commit loop."""

counts = st.integers(min_value=0, max_value=10_000)


def a_score(**kwargs: int) -> Score:
    """A score over stated counts, kind fixed because nothing branches on it."""
    return Score(kind=ItemKind.DECISION, **kwargs)


def a_refusal(refusal: Refusal, stage: Stage = Stage.STRUCTURE) -> Refused:
    """One entry in a run's refusal list."""
    return Refused(
        stage=stage,
        refusal=refusal,
        doc_id=DocumentId("DOC-0001"),
        detail="because",
        lost=RecordKind.DECISION,
    )


class TestRatesAreRates:
    @settings(max_examples=EXAMPLES)
    @given(counts, counts, counts)
    def test_every_rate_is_between_zero_and_one(self, tp, fp, fn):
        score = a_score(true_positives=tp, false_positives=fp, false_negatives=fn)
        for rate in (score.precision, score.recall, score.f1, score.field_accuracy):
            assert EMPTY <= rate <= PERFECT

    @settings(max_examples=EXAMPLES)
    @given(counts, counts, counts)
    def test_every_rate_is_reported_to_one_precision(self, tp, fp, fn):
        # A table where one column says 0.0000 and the next says 0 is a table
        # whose reader wonders what the difference is.
        score = a_score(true_positives=tp, false_positives=fp, false_negatives=fn)
        for rate in (score.precision, score.recall, score.f1):
            assert rate.as_tuple().exponent == RATE_PLACES.as_tuple().exponent

    @settings(max_examples=EXAMPLES)
    @given(counts, counts, counts)
    def test_f1_never_exceeds_either_half(self, tp, fp, fn):
        # The property that makes a harmonic mean the right summary: it cannot
        # be talked up by one good number beside one bad one.
        score = a_score(true_positives=tp, false_positives=fp, false_negatives=fn)
        assert score.f1 <= max(score.precision, score.recall)

    @settings(max_examples=EXAMPLES)
    @given(counts, counts)
    def test_f1_is_symmetric_in_its_two_halves(self, fp, fn):
        one = a_score(true_positives=10, false_positives=fp, false_negatives=fn)
        other = a_score(true_positives=10, false_positives=fn, false_negatives=fp)
        assert one.f1 == other.f1

    @settings(max_examples=EXAMPLES)
    @given(counts, counts)
    def test_a_run_with_no_mistakes_scores_one(self, tp, extra):
        score = a_score(true_positives=tp + 1, fields_expected=extra, fields_correct=extra)
        assert score.precision == PERFECT
        assert score.recall == PERFECT
        assert score.f1 == PERFECT
        assert score.field_accuracy == PERFECT

    @settings(max_examples=EXAMPLES)
    @given(counts, counts)
    def test_more_false_positives_never_raise_precision(self, tp, fp):
        assert (
            a_score(true_positives=tp, false_positives=fp + 1).precision
            <= a_score(true_positives=tp, false_positives=fp).precision
        )

    @settings(max_examples=EXAMPLES)
    @given(counts, counts)
    def test_more_misses_never_raise_recall(self, tp, fn):
        assert (
            a_score(true_positives=tp, false_negatives=fn + 1).recall
            <= a_score(true_positives=tp, false_negatives=fn).recall
        )

    @settings(max_examples=EXAMPLES)
    @given(counts, counts)
    def test_field_accuracy_cannot_exceed_what_was_expected(self, expected, correct):
        # Nonsense in, bounded out: the caller cannot make this exceed one by
        # reporting more correct fields than were asked for.
        score = a_score(fields_expected=expected, fields_correct=min(correct, expected))
        assert score.field_accuracy <= PERFECT

    @settings(max_examples=EXAMPLES)
    @given(counts, counts)
    def test_fusion_recall_is_a_rate(self, found, expected):
        assert EMPTY <= fusion_recall(min(found, expected), expected) <= PERFECT


class TestTheTwoConventions:
    def test_precision_over_nothing_extracted_is_zero_not_one(self):
        # The vacuous truth is exactly what a broken extractor would score, and
        # a table where it ties with a perfect one is unreadable.
        assert a_score(false_negatives=5).precision == EMPTY

    def test_recall_over_nothing_expected_is_one(self):
        # Cannot flatter a failure: the corpus owns this denominator.
        assert a_score().recall == PERFECT

    def test_f1_is_zero_when_either_half_is(self):
        assert a_score(false_negatives=5).f1 == EMPTY
        assert a_score(false_positives=5).f1 == EMPTY

    def test_field_accuracy_over_nothing_graded_is_one(self):
        # And `praxis.eval.report` prints a dash for it, because 1.0000 beside
        # a recall of 0.0000 invites exactly one misreading.
        assert a_score().field_accuracy == PERFECT

    def test_a_corpus_labelling_no_fusion_edges_is_not_a_failure(self):
        assert fusion_recall(0, 0) == PERFECT


class TestCitationIntegrity:
    def test_nothing_refused_is_perfect_integrity(self):
        assert citation_integrity([], offered=7).integrity == PERFECT

    def test_the_refusals_are_counted_by_defect_and_by_stage(self):
        refused = [
            a_refusal(Refusal.FABRICATED_QUOTE),
            a_refusal(Refusal.FABRICATED_QUOTE, Stage.EXTRACT),
            a_refusal(Refusal.UNOFFERED_SPAN, Stage.SCAN),
        ]
        summary = citation_integrity(refused, offered=1)
        assert summary.by_refusal[Refusal.FABRICATED_QUOTE] == 2
        assert summary.by_stage[Stage.SCAN] == 1
        assert summary.refused == 3

    def test_the_two_quotation_failures_are_reported_apart(self):
        # ADR 0015's distinction, surviving all the way to the table. One is a
        # model that read the material; the other is a model that invented.
        refused = [
            a_refusal(Refusal.FABRICATED_QUOTE),
            a_refusal(Refusal.MIS_ATTRIBUTED_QUOTE),
        ]
        summary = citation_integrity(refused, offered=2)
        assert summary.fabrication_rate == Decimal("0.2500")
        assert summary.mis_attribution_rate == Decimal("0.2500")

    def test_a_defect_that_never_happened_is_a_rate_of_zero(self):
        summary = citation_integrity([a_refusal(Refusal.EMPTY_ANSWER)], offered=1)
        assert summary.fabrication_rate == EMPTY

    def test_a_run_that_did_nothing_at_all_has_nothing_against_it(self):
        assert CitationIntegrity().integrity == PERFECT
        assert CitationIntegrity().fabrication_rate == EMPTY


@pytest.mark.parametrize("kind", list(ItemKind))
def test_a_score_carries_the_kind_it_is_about(kind):
    """Nothing branches on it, and a table with no kind column is unreadable."""
    assert Score(kind=kind).kind is kind
