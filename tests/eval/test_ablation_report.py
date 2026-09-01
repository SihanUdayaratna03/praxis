"""Rendering the ladder, over rows built by hand.

Built by hand on purpose: `test_ablation.py` runs the real ladder and takes
minutes, and nothing here is about what the pipeline produces. What is asserted
is that a number the ladder measured and a number it never reached print
differently, which is the failure mode a table has.
"""

from __future__ import annotations

import json
from decimal import Decimal

from praxis.eval.ablation import AblationRow, AblationTable, Rung
from praxis.eval.ablation_report import (
    HEADINGS,
    NOT_MEASURED,
    as_json,
    as_markdown,
    deltas,
)
from praxis.eval.metrics import MaeImprovement, PairScore
from praxis.eval.stages import Stages

FLOOR = Rung("floor", "paragraph floor, extraction only", Stages.floor())
TOP = Rung("+ governance", "challenger, curator, abstention gate", Stages())


def a_row(rung: Rung = FLOOR, **fields: object) -> AblationRow:
    """One row, with everything not under test left at its default."""
    defaults: dict[str, object] = {
        "extraction": PairScore(label="overall", true_positives=3, false_negatives=1),
    }
    return AblationRow(rung=rung, **{**defaults, **fields})


class TestTheMarkdownTable:
    def test_every_heading_the_brief_named_is_a_column(self) -> None:
        for heading in ("Precision", "Recall", "Citations", "Cost/doc"):
            assert heading in HEADINGS

    def test_a_row_lands_for_every_rung(self) -> None:
        found = as_markdown(AblationTable(rows=(a_row(FLOOR), a_row(TOP)), documents=8))

        assert "| floor |" in found
        assert "| + governance |" in found

    def test_the_row_has_a_cell_for_every_heading(self) -> None:
        """A short row shifts every column after it and the table lies quietly."""
        found = as_markdown(AblationTable(rows=(a_row(),), documents=8))
        body = next(line for line in found.splitlines() if line.startswith("| floor |"))

        assert body.count("|") == len(HEADINGS) + 1

    def test_an_unmeasured_mae_prints_as_a_dash_rather_than_a_zero(self) -> None:
        """The distinction the whole column exists for."""
        found = as_markdown(AblationTable(rows=(a_row(),), documents=8))

        assert NOT_MEASURED in found

    def test_a_measured_mae_prints_the_improvement(self) -> None:
        row = a_row(mae=MaeImprovement(scored=4, before=Decimal("0.40"), after=Decimal("0.20")))
        found = as_markdown(AblationTable(rows=(row,), documents=8))

        assert "0.2000" in found

    def test_the_documents_per_rung_are_stated(self) -> None:
        """A rate with no denominator beside it is not comparable to anything."""
        assert "**8**" in as_markdown(AblationTable(rows=(a_row(),), documents=8))

    def test_the_trivial_abstention_is_called_trivial(self) -> None:
        found = as_markdown(AblationTable(rows=(a_row(),), documents=8))

        assert "never_challenged" in found

    def test_the_provenance_is_rendered_above_the_table(self) -> None:
        found = as_markdown(
            AblationTable(rows=(a_row(),), documents=8), provenance={"provider": "mock"}
        )

        assert found.index("provider") < found.index("| Rung |")


class TestTheJson:
    def test_every_rate_is_a_string_rather_than_a_float(self) -> None:
        """Invariant 4. A JSON float puts back what the Decimal keeps out."""
        payload = json.loads(as_json(AblationTable(rows=(a_row(),), documents=8)))
        rung = payload["rungs"][0]

        assert isinstance(rung["precision"], str)
        assert isinstance(rung["recall"], str)
        assert isinstance(rung["cost_per_document"], str)

    def test_an_unmeasured_mae_says_so_rather_than_reporting_a_zero(self) -> None:
        payload = json.loads(as_json(AblationTable(rows=(a_row(),), documents=8)))

        assert payload["rungs"][0]["calibration_mae"]["measured"] is False

    def test_the_rungs_keep_ladder_order(self) -> None:
        """Sorted keys, unsorted rungs -- the order is the ladder's meaning."""
        payload = json.loads(as_json(AblationTable(rows=(a_row(FLOOR), a_row(TOP)), documents=8)))

        assert [rung["rung"] for rung in payload["rungs"]] == ["floor", "+ governance"]

    def test_the_provenance_is_carried(self) -> None:
        payload = json.loads(
            as_json(AblationTable(rows=(a_row(),), documents=8), provenance={"seed": "1"})
        )

        assert payload["provenance"] == {"seed": "1"}


class TestTheDeltas:
    def test_a_delta_lands_for_every_rung_but_the_first(self) -> None:
        table = AblationTable(rows=(a_row(FLOOR), a_row(TOP)), documents=8)

        assert [name for name, _, _ in deltas(table)] == ["+ governance"]

    def test_a_rung_that_moved_nothing_reports_zero(self) -> None:
        table = AblationTable(rows=(a_row(FLOOR), a_row(TOP)), documents=8)
        _, precision, recall = deltas(table)[0]

        assert (precision, recall) == (Decimal(0), Decimal(0))

    def test_a_rung_that_lost_recall_reports_a_negative(self) -> None:
        """Signed, because a component that made things worse is the finding."""
        worse = a_row(TOP, extraction=PairScore(label="overall", false_negatives=4))
        table = AblationTable(rows=(a_row(FLOOR), worse), documents=8)
        _, _, recall = deltas(table)[0]

        assert recall < 0

    def test_one_rung_has_no_deltas_at_all(self) -> None:
        assert deltas(AblationTable(rows=(a_row(),), documents=8)) == ()
