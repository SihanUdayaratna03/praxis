"""Rendering the ablation ladder. No arithmetic, so a format change moves nothing.

The same split `praxis.eval.report` makes, and a separate module only because
that one is already long. See ADR 0035 for what the rungs mean.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from itertools import pairwise
from typing import Any, Final

from praxis.eval.ablation import AblationRow, AblationTable
from praxis.eval.report import PROVENANCE_SEPARATOR, table_row

HEADINGS: Final = (
    "Rung",
    "Adds",
    "Precision",
    "Recall",
    "Citations",
    "Breach recall",
    "MAE improvement",
    "Abstention precision",
    "Fusion recall",
    "Cost/doc",
    "Calls",
)

NOT_MEASURED: Final = "--"
"""What a column prints when nothing reached it. Distinct from a zero."""

LADDER_NOTE: Final = (
    "Cumulative: each rung is the one above it plus one component, run over the "
    "same corpus in a store of its own (ADR 0035). A difference between two "
    "adjacent rows is that component and nothing else."
)

TRIVIAL_ABSTENTION_NOTE: Final = (
    "Abstention precision is trivially **1.0000** below the governance rung: "
    "nothing has argued the findings, so every one fails `never_challenged` and "
    "none is emitted. Read it with the emitted count, not alone."
)


def as_markdown(table: AblationTable, *, provenance: Mapping[str, str] | None = None) -> str:
    """Render the ladder as the table a phase report carries."""
    lines = ["## Ablation", ""]
    if provenance:
        lines += [
            PROVENANCE_SEPARATOR.join(
                f"**{key}**: {value}" for key, value in sorted(provenance.items())
            ),
            "",
        ]
    lines += [
        f"> {LADDER_NOTE}",
        "",
        table_row(HEADINGS),
        table_row(["---"] * len(HEADINGS)),
        *(_row(row) for row in table.rows),
        "",
        f"Documents per rung: **{table.documents}**.",
        "",
        f"> {TRIVIAL_ABSTENTION_NOTE}",
        "",
    ]
    return "\n".join(lines)


def _row(row: AblationRow) -> str:
    """One rung, in the column order the brief named."""
    return table_row(
        (
            row.rung.name,
            row.rung.adds,
            str(row.extraction.precision),
            str(row.extraction.recall),
            str(row.citation_integrity),
            str(row.breach_detection.recall),
            _mae(row),
            f"{row.abstention_precision} ({row.emitted} emitted)",
            str(row.fusion_recall),
            str(row.cost_per_document),
            str(row.calls),
        )
    )


def _mae(row: AblationRow) -> str:
    """The calibration improvement, or why there is none.

    Offline no group reaches `MINIMUM_SAMPLE`, so this is usually `--`. Printed
    as a zero it would read as a calibrator that changed nothing.
    """
    if not row.mae.measured:
        return NOT_MEASURED
    return f"{row.mae.absolute} ({row.mae.relative} of {row.mae.before})"


def as_json(table: AblationTable, *, provenance: Mapping[str, str] | None = None) -> str:
    """The same numbers as data. Rates are strings, for invariant 4's reason."""
    payload: dict[str, Any] = {
        "documents": table.documents,
        "rungs": [_data(row) for row in table.rows],
    }
    if provenance:
        payload["provenance"] = dict(provenance)
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _data(row: AblationRow) -> dict[str, Any]:
    """One rung as data."""
    return {
        "rung": row.rung.name,
        "adds": row.rung.adds,
        "precision": str(row.extraction.precision),
        "recall": str(row.extraction.recall),
        "f1": str(row.extraction.f1),
        "true_positives": row.extraction.true_positives,
        "false_positives": row.extraction.false_positives,
        "false_negatives": row.extraction.false_negatives,
        "citation_integrity": str(row.citation_integrity),
        "breach_detection": {
            "precision": str(row.breach_detection.precision),
            "recall": str(row.breach_detection.recall),
        },
        "calibration_mae": {
            "measured": row.mae.measured,
            "scored": row.mae.scored,
            "before": str(row.mae.before),
            "after": str(row.mae.after),
            "absolute": str(row.mae.absolute),
            "relative": str(row.mae.relative),
        },
        "abstention": {
            "precision": str(row.abstention_precision),
            "emitted": row.emitted,
            "abstained": row.abstained,
        },
        "fusion_recall": str(row.fusion_recall),
        "cost_per_document": str(row.cost_per_document),
        "cost_by_agent": {agent: str(spent) for agent, spent in sorted(row.cost_by_agent.items())},
        "calls": row.calls,
    }


def deltas(table: AblationTable) -> tuple[tuple[str, Decimal, Decimal], ...]:
    """What each rung moved precision and recall by, against the rung below it.

    The reading the ladder exists for. The first rung has nothing below it and
    is not in the result.
    """
    return tuple(
        (
            above.rung.name,
            above.extraction.precision - below.extraction.precision,
            above.extraction.recall - below.extraction.recall,
        )
        for below, above in pairwise(table.rows)
    )
