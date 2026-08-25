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
from praxis.domain.enums import AssumptionStatus as Status
from praxis.domain.enums import RecordKind
from praxis.domain.ids import DocumentId
from praxis.eval.matching import Pairing
from praxis.eval.metrics import (
    EMPTY,
    PERFECT,
    RATE_PLACES,
    CitationIntegrity,
    FormalizationScore,
    Score,
    citation_integrity,
    cost_per_document,
    fusion_recall,
    monitoring_score,
    pair_score,
    score,
)

EXAMPLES = 60
"""Enough to reach the awkward end of the input space, few enough to stay in
the pre-commit loop."""

counts = st.integers(min_value=0, max_value=10_000)

ONE = PERFECT.quantize(RATE_PLACES)
ZERO = EMPTY.quantize(RATE_PLACES)


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


class TestPairScore:
    """Precision and recall over things that are already identified.

    `Score` pairs by byte overlap because an extraction points at a place.
    A `contradicts` edge is a pair of record ids and a monitoring verdict is
    about one assumption, so comparing those by overlap would invent a
    difficulty they do not have.
    """

    def test_everything_expected_and_nothing_else_is_perfect(self):
        score = pair_score("contradiction", [("a", "b")], [("a", "b")])
        assert (score.precision, score.recall, score.f1) == (ONE, ONE, ONE)

    def test_something_found_that_was_not_expected_costs_precision(self):
        score = pair_score("contradiction", [("a", "b"), ("c", "d")], [("a", "b")])
        assert score.false_positives == 1
        assert score.precision == Decimal("0.5000")
        assert score.recall == ONE

    def test_something_expected_that_was_not_found_costs_recall(self):
        score = pair_score("contradiction", [("a", "b")], [("a", "b"), ("c", "d")])
        assert score.false_negatives == 1
        assert score.recall == Decimal("0.5000")

    def test_one_thing_found_twice_is_one_thing(self):
        # Correct for everything this grades: a contradiction found twice is a
        # contradiction, and the store's content-addressed link ids mean it is
        # also one row.
        score = pair_score("contradiction", [("a", "b"), ("a", "b")], [("a", "b")])
        assert score.true_positives == 1
        assert score.false_positives == 0

    def test_finding_nothing_when_nothing_was_expected_recalls_everything(self):
        # The convention `Score` already states: there was nothing to find and
        # nothing was missed, and the corpus decides the denominator.
        assert pair_score("contradiction", [], []).recall == ONE

    def test_finding_nothing_when_something_was_expected_recalls_nothing(self):
        assert pair_score("contradiction", [], [("a", "b")]).recall == ZERO

    def test_precision_over_nothing_extracted_is_zero_not_one(self):
        # The same conservative reading Score takes: a pipeline that produces
        # nothing must not tie with a perfect one.
        assert pair_score("contradiction", [], [("a", "b")]).precision == ZERO

    def test_a_row_knows_what_it_is_called(self):
        assert pair_score("contradiction", [], []).label == "contradiction"

    def test_a_kind_score_knows_too_so_one_function_renders_both(self):
        assert score(Pairing(), ItemKind.DECISION).label == "decision"


class TestFormalization:
    def test_every_predicate_parsing_is_a_full_rate(self):
        assert FormalizationScore(total=4, predicates_parsed=4, checkable=4).parse_rate == ONE

    def test_a_predicate_that_parses_is_not_yet_checkable(self):
        # Both halves have to parse: the monitor needs the predicate to reach a
        # verdict and the condition to know whether the verdict is stale.
        found = FormalizationScore(total=4, predicates_parsed=4, checkable=2)
        assert found.parse_rate == ONE
        assert found.checkable_rate == Decimal("0.5000")

    def test_nothing_stored_reads_as_nothing_unread(self):
        assert FormalizationScore().parse_rate == ONE


class TestMonitoringScore:
    """The class the phase is graded on."""

    def test_an_aged_assumption_reported_as_breached_is_counted_by_name(self):
        # The failure the whole phase is arranged to prevent, and the reason
        # this is a named property rather than a cell a reader has to find.
        found = monitoring_score({"a": Status.BREACHED}, {"a": Status.UNVERIFIED})
        assert found.aged_misreported_as_breached == 1

    def test_an_expired_assumption_reported_as_breached_counts_too(self):
        found = monitoring_score({"a": Status.BREACHED}, {"a": Status.EXPIRED})
        assert found.aged_misreported_as_breached == 1

    def test_a_holding_assumption_reported_as_breached_does_not(self):
        # A wrong verdict, and a wrong verdict about a *measured* quantity --
        # a different failure from confusing age with violation, so it is not
        # counted as one.
        found = monitoring_score({"a": Status.BREACHED}, {"a": Status.HOLDING})
        assert found.aged_misreported_as_breached == 0
        assert found.accuracy == ZERO

    def test_a_correct_breach_is_not_a_misreport(self):
        found = monitoring_score({"a": Status.BREACHED}, {"a": Status.BREACHED})
        assert found.aged_misreported_as_breached == 0

    def test_agreement_is_counted_across_the_diagonal(self):
        found = monitoring_score(
            {"a": Status.BREACHED, "b": Status.HOLDING, "c": Status.UNVERIFIED},
            {"a": Status.BREACHED, "b": Status.HOLDING, "c": Status.UNVERIFIED},
        )
        assert found.total == 3
        assert found.accuracy == ONE

    def test_an_assumption_the_run_never_reached_counts_as_unverified(self):
        # Which is what an assumption nothing evaluated really is, rather than
        # a gap in the table.
        found = monitoring_score({}, {"a": Status.UNVERIFIED})
        assert found.accuracy == ONE
        assert found.total == 1

    def test_an_expectation_the_run_missed_is_not_silently_dropped(self):
        found = monitoring_score({}, {"a": Status.BREACHED})
        assert found.total == 1
        assert found.accuracy == ZERO

    def test_a_verdict_the_corpus_says_nothing_about_is_not_counted(self):
        # The corpus decides the denominator, as everywhere else here.
        assert monitoring_score({"z": Status.BREACHED}, {}).total == 0

    def test_breach_precision_and_recall_are_reported_apart(self):
        found = monitoring_score(
            {"a": Status.BREACHED, "b": Status.BREACHED, "c": Status.HOLDING},
            {"a": Status.BREACHED, "b": Status.UNVERIFIED, "c": Status.BREACHED},
        )
        assert found.breaches.true_positives == 1
        assert found.breaches.false_positives == 1
        assert found.breaches.false_negatives == 1

    def test_a_run_with_no_expectations_is_perfect_and_says_so(self):
        assert monitoring_score({}, {}).accuracy == ONE


class TestCostPerDocument:
    def test_a_total_is_divided_by_the_documents_it_covered(self):
        assert cost_per_document({"DecisionScout": Decimal("0.12")}, 12) == {
            "DecisionScout": Decimal("0.010000")
        }

    def test_the_places_reported_survive_a_fraction_of_a_cent(self):
        # ADR 0006's fifth assumption is cost_per_document_usd <= 0.05, and
        # rounding to cents would report most of this pipeline as free.
        found = cost_per_document({"DecisionScout": Decimal("0.000036")}, 12)
        assert found["DecisionScout"] == Decimal("0.000003")

    def test_dividing_by_no_documents_reports_nothing(self):
        assert cost_per_document({"DecisionScout": Decimal("0.12")}, 0) == {}

    def test_an_agent_that_spent_nothing_is_still_reported(self):
        # Offline every cost is zero, and an agent missing from the table would
        # read as an agent that did not run.
        assert cost_per_document({"DecisionScout": Decimal("0")}, 4) == {
            "DecisionScout": Decimal("0.000000")
        }

    def test_the_result_is_decimal_throughout(self):
        found = cost_per_document({"DecisionScout": Decimal("0.1")}, 3)
        assert all(isinstance(value, Decimal) for value in found.values())


class TestAPairScoreThatFoundNothingRight:
    def test_f1_is_zero_when_both_halves_are(self):
        # Not an error and not undefined: a run that found one wrong thing and
        # missed the right one scores zero on both, and the harmonic mean of
        # two zeros is the number a table should print.
        score = pair_score("contradiction", [("a", "b")], [("c", "d")])
        assert (score.precision, score.recall) == (ZERO, ZERO)
        assert score.f1 == ZERO
