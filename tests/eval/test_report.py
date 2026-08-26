"""Rendering a graded run, held to saying exactly what the metrics say.

`praxis.eval.report` does no arithmetic, and that constraint is the whole
reason it is a separate module -- so these tests assert the claim the
constraint buys: every number in the table is a number `praxis.eval.metrics`
already computed, found in the output as its own string. A test that
recomputed a rate to check the rate would be testing the same expression
twice.

Three things are worth their own tests rather than an eyeball:

**A dash is not a zero.** `field_accuracy` is 1 by convention when no record
was matched, and printing `1.0000` beside a recall of `0.0000` invites exactly
one misreading. The cell says `--` instead, and that is formatting rather than
arithmetic -- the metric itself is untouched.

**The JSON is byte-stable.** Phase 10's ablation table diffs these files, so
two results holding the same numbers have to produce the same bytes: sorted
keys, and rates as strings rather than floats.

**The offline caveat is in the artefact.** A metrics table outlives the
conversation in which everyone knew the mock produced it.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from praxis.agents.errors import Refusal
from praxis.agents.results import ExtractionRun, Stage
from praxis.corpus.groundtruth import GroundTruthItem, ItemKind
from praxis.domain.enums import AssumptionStatus
from praxis.eval.harness import EvalResult, KindResult
from praxis.eval.matching import Pairing
from praxis.eval.memory import MemoryResult
from praxis.eval.metrics import (
    CitationIntegrity,
    FormalizationScore,
    MonitoringScore,
    PairScore,
    Score,
    exact_matches,
    score,
)
from praxis.eval.report import (
    AGED_NOTE,
    NOTHING_TO_GRADE,
    OFFLINE_CAVEAT,
    PAIR_HEADINGS,
    SCORE_HEADINGS,
    UNNAMED_NOTE,
    as_json,
    as_markdown,
)

PROVENANCE = {
    "provider": "mock",
    "corpus_seed": "20260818",
    "decision_scout": "v1",
}


def a_kind(kind: ItemKind = ItemKind.DECISION, **kwargs) -> KindResult:
    """One kind's result, built from counts rather than from a pairing.

    The pairing is empty on purpose: nothing in `report` reads it, and a
    fixture carrying one would suggest otherwise.
    """
    kwargs.setdefault("true_positives", 3)
    kwargs.setdefault("false_positives", 1)
    kwargs.setdefault("false_negatives", 2)
    kwargs.setdefault("fields_expected", 9)
    kwargs.setdefault("fields_correct", 7)
    exact = kwargs.pop("exact", 2)
    return KindResult(pairing=Pairing(), score=Score(kind=kind, **kwargs), exact=exact)


def a_result(*kinds: KindResult, **kwargs) -> EvalResult:
    """A graded run, with everything the renderer reads and nothing it does not."""
    kwargs.setdefault("citations", CitationIntegrity(offered=8, refused=2))
    kwargs.setdefault("fusion_found", 3)
    kwargs.setdefault("fusion_expected", 5)
    kwargs.setdefault("documents", 4)
    return EvalResult(run=ExtractionRun(), kinds=kinds or (a_kind(),), **kwargs)


def a_missed_item() -> GroundTruthItem:
    """One answer-key entry, for a pairing the renderer must not read."""
    return GroundTruthItem(
        item_id="GT-0001", kind=ItemKind.DECISION, start_byte=0, end_byte=4, quote="xxxx"
    )


def cells_of(row: str) -> list[str]:
    """One markdown row split back into its cells."""
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def row_for(markdown: str, kind: ItemKind) -> list[str]:
    """The table row one kind was rendered as."""
    return next(
        cells_of(line) for line in markdown.splitlines() if cells_of(line)[:1] == [kind.value]
    )


# -- the table says what the metrics say ---------------------------------------


def test_every_score_the_metrics_computed_appears_in_the_row():
    kind = a_kind()
    row = row_for(as_markdown(a_result(kind)), ItemKind.DECISION)

    assert row == [
        ItemKind.DECISION.value,
        "3",
        "2",
        "1",
        "0",
        str(kind.score.precision),
        str(kind.score.recall),
        str(kind.score.f1),
        str(kind.score.field_accuracy),
        "2",
    ]


def test_the_headings_are_the_ones_the_module_names():
    markdown = as_markdown(a_result())

    assert cells_of(next(line for line in markdown.splitlines() if line.startswith("| Kind"))) == [
        *SCORE_HEADINGS
    ]


def test_a_distracted_false_positive_is_shown_apart_from_a_spurious_one():
    kind = a_kind(false_positives=4, distracted=3)

    row = row_for(as_markdown(a_result(kind)), ItemKind.DECISION)

    assert row[SCORE_HEADINGS.index("Spurious")] == "1"
    assert row[SCORE_HEADINGS.index("Distracted")] == "3"


def test_one_row_per_graded_kind_in_the_order_it_was_given():
    result = a_result(
        a_kind(ItemKind.DECISION), a_kind(ItemKind.ASSUMPTION), a_kind(ItemKind.ESTIMATE)
    )

    markdown = as_markdown(result)
    order = [line for line in markdown.splitlines() if line.startswith("| ")]

    assert [cells_of(line)[0] for line in order[2:5]] == [
        ItemKind.DECISION.value,
        ItemKind.ASSUMPTION.value,
        ItemKind.ESTIMATE.value,
    ]


def test_nothing_matched_prints_a_dash_rather_than_a_flattering_one():
    kind = a_kind(true_positives=0, false_negatives=5, fields_expected=0, fields_correct=0, exact=0)

    row = row_for(as_markdown(a_result(kind)), ItemKind.DECISION)

    assert row[SCORE_HEADINGS.index("Fields")] == NOTHING_TO_GRADE
    assert kind.score.field_accuracy == 1, "the metric is still 1 -- only the cell changed"


def test_the_fusion_line_carries_both_counts_and_the_recall():
    result = a_result(fusion_found=3, fusion_expected=5)

    markdown = as_markdown(result)

    assert "**3** of 5" in markdown
    assert str(result.fusion_recall) in markdown


def test_the_run_s_own_counts_are_reported_beside_the_scores():
    markdown = as_markdown(a_result(documents=4))

    assert "Documents graded: 4" in markdown


# -- provenance and the caveat -------------------------------------------------


def test_provenance_is_ascii_so_a_redirected_table_is_not_mojibake():
    """`praxis eval` prints this, and a redirect on Windows is not UTF-8.

    The line that says which run produced a table is the worst line to lose to
    a codepage, because it is the one that makes the table comparable at all.
    """
    markdown = as_markdown(a_result(), provenance=PROVENANCE)

    markdown.encode("ascii")


def test_provenance_is_rendered_in_a_stable_order():
    first = as_markdown(a_result(), provenance=PROVENANCE)
    second = as_markdown(a_result(), provenance=dict(reversed(list(PROVENANCE.items()))))

    assert first == second
    assert "**corpus_seed**: 20260818" in first


def test_a_table_with_no_provenance_still_renders():
    markdown = as_markdown(a_result())

    assert "**provider**" not in markdown
    assert markdown.startswith("## Extraction quality")


def test_the_offline_caveat_is_printed_in_the_artefact_when_asked_for():
    assert OFFLINE_CAVEAT in as_markdown(a_result(), offline=True)
    assert OFFLINE_CAVEAT not in as_markdown(a_result(), offline=False)


def test_the_markdown_ends_in_a_newline_so_a_report_can_concatenate_it():
    assert as_markdown(a_result(), provenance=PROVENANCE, offline=True).endswith("\n")


# -- citation integrity --------------------------------------------------------


def test_the_gate_s_counts_and_rates_are_both_reported():
    citations = CitationIntegrity(
        offered=8,
        refused=2,
        by_refusal={Refusal.FABRICATED_QUOTE: 2},
        by_stage={Stage.STRUCTURE: 2},
    )

    markdown = as_markdown(a_result(citations=citations))

    assert "Claims stored: **8**, refused: **2**" in markdown
    assert str(citations.integrity) in markdown
    assert str(citations.fabrication_rate) in markdown


def test_refusals_are_listed_worst_first_and_ties_broken_by_name():
    citations = CitationIntegrity(
        offered=1,
        refused=6,
        by_refusal={
            Refusal.MIS_ATTRIBUTED_QUOTE: 2,
            Refusal.FABRICATED_QUOTE: 4,
            Refusal.UNOFFERED_SPAN: 2,
        },
    )

    markdown = as_markdown(a_result(citations=citations))
    listed = [
        cells_of(line)[0]
        for line in markdown.splitlines()
        if line.startswith("| ") and cells_of(line)[0] in {refusal.value for refusal in Refusal}
    ]

    assert listed[0] == Refusal.FABRICATED_QUOTE.value
    assert listed[1:] == sorted(listed[1:])


def test_a_run_that_lost_nothing_prints_no_refusal_table():
    markdown = as_markdown(a_result(citations=CitationIntegrity(offered=8, refused=0)))

    assert "| Refusal | Count |" not in markdown


# -- the json Phase 10 reads ---------------------------------------------------


def test_the_json_holds_every_rate_as_a_string_never_a_float():
    kind = a_kind()
    payload = json.loads(as_json(a_result(kind)))

    decision = payload["kinds"][ItemKind.DECISION.value]
    assert decision["precision"] == str(kind.score.precision)
    assert decision["recall"] == str(kind.score.recall)
    assert decision["f1"] == str(kind.score.f1)
    assert decision["field_accuracy"] == str(kind.score.field_accuracy)


def test_the_json_reports_the_same_counts_the_table_does():
    kind = a_kind()
    payload = json.loads(as_json(a_result(kind)))
    row = row_for(as_markdown(a_result(kind)), ItemKind.DECISION)

    decision = payload["kinds"][ItemKind.DECISION.value]
    assert str(decision["true_positives"]) == row[SCORE_HEADINGS.index("Found")]
    assert str(decision["false_negatives"]) == row[SCORE_HEADINGS.index("Missed")]
    assert str(decision["exact"]) == row[SCORE_HEADINGS.index("Exact")]


def test_two_results_holding_the_same_numbers_produce_the_same_bytes():
    first = as_json(a_result(a_kind(ItemKind.DECISION), a_kind(ItemKind.ASSUMPTION)))
    second = as_json(a_result(a_kind(ItemKind.DECISION), a_kind(ItemKind.ASSUMPTION)))

    assert first == second
    assert json.loads(first)


def test_the_json_keys_are_sorted_so_a_diff_is_a_diff_of_the_content():
    payload = as_json(a_result())
    keys = [line.split('"')[1] for line in payload.splitlines() if line.startswith('  "')]

    assert keys == sorted(keys)


def test_the_json_carries_provenance_only_when_it_was_given():
    assert "provenance" not in json.loads(as_json(a_result()))
    assert json.loads(as_json(a_result(), provenance=PROVENANCE))["provenance"] == PROVENANCE


def test_the_json_names_each_refusal_and_stage_by_its_own_value():
    citations = CitationIntegrity(
        offered=1, refused=1, by_refusal={Refusal.FABRICATED_QUOTE: 1}, by_stage={Stage.EXTRACT: 1}
    )

    payload = json.loads(as_json(a_result(citations=citations)))

    assert payload["citations"]["by_refusal"] == {Refusal.FABRICATED_QUOTE.value: 1}
    assert payload["citations"]["by_stage"] == {Stage.EXTRACT.value: 1}


def test_the_json_ends_in_a_newline():
    assert as_json(a_result()).endswith("\n")


# -- the renderer does no arithmetic -------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        a_kind(true_positives=0, false_positives=0, false_negatives=0, fields_expected=0),
        a_kind(true_positives=7, false_positives=0, false_negatives=0),
        a_kind(true_positives=0, false_positives=9, false_negatives=9),
    ],
)
def test_the_table_and_the_json_never_disagree_about_a_rate(kind):
    row = row_for(as_markdown(a_result(kind)), ItemKind.DECISION)
    decision = json.loads(as_json(a_result(kind)))["kinds"][ItemKind.DECISION.value]

    assert row[SCORE_HEADINGS.index("Precision")] == decision["precision"]
    assert row[SCORE_HEADINGS.index("Recall")] == decision["recall"]
    assert row[SCORE_HEADINGS.index("F1")] == decision["f1"]


def test_a_score_built_by_the_metrics_renders_the_metrics_own_numbers():
    """The one test whose input comes through `score` rather than by hand.

    The fixtures above set counts directly, which is what keeps them readable;
    this one goes through the real counting so that a `Score` field the
    renderer stopped reading would show up as a column that never changes.
    """
    pairing = Pairing(matched=(), missed=(), spurious=(), distracted=())
    computed = KindResult(
        pairing=pairing,
        score=score(pairing, ItemKind.ESTIMATE),
        exact=exact_matches(pairing.matched),
    )

    row = row_for(as_markdown(a_result(computed)), ItemKind.ESTIMATE)

    assert row[SCORE_HEADINGS.index("Found")] == "0"
    assert row[SCORE_HEADINGS.index("Recall")] == str(computed.score.recall)
    assert row[SCORE_HEADINGS.index("Fields")] == NOTHING_TO_GRADE


def test_a_row_renders_from_the_counts_alone_and_never_from_the_pairing():
    """`report` reads the score and the exact count, never the pairing itself.

    Asserted by rendering one result twice with the same counts and two
    different pairings behind them. If a cell ever came from a `Match` rather
    than from a `Score`, these two would differ.
    """
    counts = {
        "true_positives": 1,
        "false_positives": 0,
        "false_negatives": 0,
        "fields_expected": 0,
    }
    empty = KindResult(pairing=Pairing(), score=Score(kind=ItemKind.DECISION, **counts), exact=1)
    populated = KindResult(
        pairing=Pairing(missed=(a_missed_item(),)),
        score=Score(kind=ItemKind.DECISION, **counts),
        exact=1,
    )

    assert as_markdown(a_result(empty)) == as_markdown(a_result(populated))


# -- the memory half -----------------------------------------------------------


def a_memory(**kwargs) -> MemoryResult:
    """A memory grading, built from counts for the reason `a_kind` gives."""
    kwargs.setdefault(
        "formalization", FormalizationScore(total=10, predicates_parsed=8, checkable=6)
    )
    kwargs.setdefault(
        "contradictions", PairScore(label="contradiction", true_positives=2, false_negatives=1)
    )
    kwargs.setdefault("expected", 3)
    kwargs.setdefault("proposed", 3)
    kwargs.setdefault("identified", 7)
    return MemoryResult(**kwargs)


def test_the_formalization_rates_are_the_ones_metrics_computed():
    memory = a_memory()

    rendered = as_markdown(a_result(memory=memory))

    assert str(memory.formalization.parse_rate) in rendered
    assert str(memory.formalization.checkable_rate) in rendered


def test_every_contradiction_stage_gets_its_own_row():
    # A low recall means three different things depending on which stage lost
    # the pair, so the table has to be able to say which.
    rendered = as_markdown(a_result(memory=a_memory()))

    assert all(heading in rendered for heading in PAIR_HEADINGS)
    assert "| contradiction |" in rendered
    assert "| arithmetic |" in rendered
    assert "| model |" in rendered


def test_the_blocking_recall_is_printed_as_the_ceiling_it_is():
    memory = a_memory()

    rendered = as_markdown(a_result(memory=memory))

    assert str(memory.proposed_recall) in rendered


def test_the_aged_count_carries_its_explanation():
    # A zero with no note reads as a column nobody filled in.
    assert AGED_NOTE in as_markdown(a_result(memory=a_memory()))


def test_the_unnamed_count_says_it_is_not_a_false_positive():
    assert UNNAMED_NOTE in as_markdown(a_result(memory=a_memory(unnamed=4)))


def test_a_run_with_no_cost_totalled_prints_no_cost_table():
    assert "Cost per document" not in as_markdown(a_result(memory=a_memory()))


def test_the_cost_table_names_each_agent_and_what_it_spent():
    cost = {"DecisionScout": Decimal("0.001200"), "AssumptionFormalizer": Decimal("0.000300")}

    rendered = as_markdown(a_result(memory=a_memory(), cost=cost))

    assert "## Cost per document" in rendered
    assert "| DecisionScout | 0.001200 |" in rendered
    assert "| AssumptionFormalizer | 0.000300 |" in rendered


def test_the_memory_numbers_reach_the_json():
    payload = json.loads(as_json(a_result(memory=a_memory())))

    assert payload["memory"]["formalization"]["checkable"] == 6
    assert payload["memory"]["contradictions"]["all"]["true_positives"] == 2
    assert payload["memory"]["contradictions"]["proposed_recall"] == "1.0000"


def test_the_confusion_matrix_survives_json_with_no_tuple_keys():
    memory = a_memory(
        monitoring=MonitoringScore(
            matrix={(AssumptionStatus.BREACHED, AssumptionStatus.HOLDING): 2}
        )
    )

    payload = json.loads(as_json(a_result(memory=memory)))

    assert payload["memory"]["monitoring"]["matrix"] == {"breached>holding": 2}


def test_a_cost_is_a_string_in_the_json_and_never_a_float():
    # Invariant 4 reaches the artefact: a float here would put back exactly the
    # representation the arithmetic excludes.
    payload = json.loads(as_json(a_result(memory=a_memory(), cost={"S": Decimal("0.000001")})))

    assert payload["cost_per_document"] == {"S": "0.000001"}
