"""Rendering a graded run. No arithmetic, so formatting cannot move a number.

That constraint is the reason this is a fourth module rather than a method on
`EvalResult`. Everything here reads values `praxis.eval.metrics` already
computed and turns them into a string; nothing divides, rounds, or sums. A test
can therefore assert that the table says what the metrics say, which is a
different claim from asserting that the metrics are right, and both are worth
making separately.

Two audiences, two renderings, one set of numbers:

- `as_markdown` writes the table that goes in `docs/reports/phase-N.md`, which
  is read by a person deciding whether a prompt change helped.
- `as_json` writes the same numbers as data, which is what Phase 10's ablation
  table reads. Keys sorted and rates as strings, so two runs producing the same
  numbers produce byte-identical files and a diff is a diff of the content.

The provenance line is not decoration. A metrics table with no note of what
produced it is the failure mode ADR 0014 exists to prevent one level down: a
number is meaningless without the prompt version, the provider and the corpus
seed that produced it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

from praxis.eval.harness import EvalResult, KindResult
from praxis.eval.metrics import CitationIntegrity

SCORE_HEADINGS: Final = (
    "Kind",
    "Found",
    "Missed",
    "Spurious",
    "Distracted",
    "Precision",
    "Recall",
    "F1",
    "Fields",
    "Exact",
)

NOTHING_TO_GRADE: Final = "--"
"""Shown where a rate exists by convention but no evidence went into it."""

OFFLINE_CAVEAT: Final = (
    "Every number above was produced by the offline provider, which draws each "
    "field of an answer independently -- so a cited passage and a quotation "
    "agree only by chance. These measure the pipeline, not a model. See ADR 0016."
)
"""Printed under any table produced without a real provider.

Stated in the artefact rather than left to whoever reads it, because a metrics
table outlives the conversation in which everyone knew what it meant.
"""


def as_markdown(
    result: EvalResult, *, provenance: Mapping[str, str] | None = None, offline: bool = False
) -> str:
    """Render a graded run as the table a phase report carries.

    Args:
        result: What `praxis.eval.harness.grade` produced.
        provenance: What produced these numbers -- provider, corpus seed, prompt
            versions. Rendered above the table, because a number without it
            cannot be compared with another number.
        offline: Whether the offline provider produced them. Prints the caveat,
            in the artefact rather than in the conversation that produced it.

    Returns:
        Markdown, ending in a newline.
    """
    lines = ["## Extraction quality", ""]
    if provenance:
        lines += [_provenance(provenance), ""]
    lines += [
        _row(SCORE_HEADINGS),
        _row(["---"] * len(SCORE_HEADINGS)),
        *(_score_row(kind) for kind in result.kinds),
        "",
        "## Citation integrity",
        "",
        *_citation_lines(result.citations),
        "",
        "## Fusion",
        "",
        f"`estimated_as` edges found: **{result.fusion_found}** of "
        f"{result.fusion_expected} the corpus labels "
        f"(recall **{result.fusion_recall}**).",
        "",
        f"Documents graded: {result.documents}. "
        f"Model calls: {result.run.calls}. "
        f"Windows never answered about: {result.run.blind_windows}.",
        "",
    ]
    if offline:
        lines += [f"> {OFFLINE_CAVEAT}", ""]
    return "\n".join(lines)


def as_json(result: EvalResult, *, provenance: Mapping[str, str] | None = None) -> str:
    """Render the same numbers as data, for Phase 10's ablation table.

    Rates are strings rather than JSON numbers, on purpose: they are `Decimal`
    and writing them as floats would put back exactly the representation
    invariant 4 keeps out of the arithmetic.
    """
    payload: dict[str, Any] = {
        "documents": result.documents,
        "calls": result.run.calls,
        "blind_windows": result.run.blind_windows,
        "kinds": {kind.kind.value: _score_data(kind) for kind in result.kinds},
        "citations": {
            "offered": result.citations.offered,
            "refused": result.citations.refused,
            "integrity": str(result.citations.integrity),
            "fabrication_rate": str(result.citations.fabrication_rate),
            "mis_attribution_rate": str(result.citations.mis_attribution_rate),
            "by_refusal": {
                refusal.value: count for refusal, count in result.citations.by_refusal.items()
            },
            "by_stage": {stage.value: count for stage, count in result.citations.by_stage.items()},
        },
        "fusion": {
            "found": result.fusion_found,
            "expected": result.fusion_expected,
            "recall": str(result.fusion_recall),
        },
    }
    if provenance:
        payload["provenance"] = dict(provenance)
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _score_data(kind: KindResult) -> dict[str, Any]:
    """One kind's numbers, already computed."""
    return {
        "true_positives": kind.score.true_positives,
        "false_positives": kind.score.false_positives,
        "false_negatives": kind.score.false_negatives,
        "distracted": kind.score.distracted,
        "exact": kind.exact,
        "precision": str(kind.score.precision),
        "recall": str(kind.score.recall),
        "f1": str(kind.score.f1),
        "field_accuracy": str(kind.score.field_accuracy),
    }


def _score_row(kind: KindResult) -> str:
    """One kind as a table row."""
    return _row(
        [
            kind.kind.value,
            str(kind.score.true_positives),
            str(kind.score.false_negatives),
            str(kind.score.false_positives - kind.score.distracted),
            str(kind.score.distracted),
            str(kind.score.precision),
            str(kind.score.recall),
            str(kind.score.f1),
            _fields_cell(kind),
            str(kind.exact),
        ]
    )


def _fields_cell(kind: KindResult) -> str:
    """Field accuracy, or a dash when no record was matched to grade fields on.

    Formatting rather than arithmetic, and the distinction matters: the metric
    is 1 by the stated convention -- nothing was expected and nothing was got
    wrong -- but a column reading `1.0000` beside a recall of `0.0000` invites
    exactly one misreading, and it is the flattering one.
    """
    if kind.score.fields_expected == 0:
        return NOTHING_TO_GRADE
    return str(kind.score.field_accuracy)


def _citation_lines(citations: CitationIntegrity) -> list[str]:
    """The gate's own numbers, and what it stopped.

    Reported as counts beside rates because the rates are small and the counts
    are what someone reproduces. A rate of 0.0250 over four hundred claims and
    over four are the same number and not the same evidence.
    """
    lines = [
        f"- Claims stored: **{citations.offered}**, refused: **{citations.refused}** "
        f"(integrity **{citations.integrity}**).",
        f"- Fabricated quotations: **{citations.fabrication_rate}**; "
        f"mis-attributions: **{citations.mis_attribution_rate}**.",
    ]
    if citations.by_refusal:
        lines.append("")
        lines.append(_row(("Refusal", "Count")))
        lines.append(_row(("---", "---")))
        lines += [
            _row((refusal.value, str(count)))
            for refusal, count in sorted(
                citations.by_refusal.items(), key=lambda entry: (-entry[1], entry[0].value)
            )
        ]
    return lines


def _provenance(provenance: Mapping[str, str]) -> str:
    """What produced these numbers, on one line, in a stable order."""
    return " · ".join(f"**{key}**: {value}" for key, value in sorted(provenance.items()))


def _row(cells: tuple[str, ...] | list[str]) -> str:
    """One markdown table row."""
    return "| " + " | ".join(cells) + " |"
