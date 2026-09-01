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
from decimal import Decimal
from typing import Any, Final

from praxis.eval.calibration import CalibrationScore
from praxis.eval.estimation import EstimationResult
from praxis.eval.fusion import FusionScore
from praxis.eval.governance import GovernanceScore
from praxis.eval.harness import EvalResult, KindResult
from praxis.eval.memory import MemoryResult
from praxis.eval.metrics import CitationIntegrity, MatchScore, PairScore

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

PAIR_HEADINGS: Final = ("Stage", "Found", "Missed", "Spurious", "Precision", "Recall", "F1")
"""The contradiction rows. `Stage` rather than `Kind`: these are not kinds of
claim, they are the three answers to one question -- everything the store holds,
and the two stages that settled it."""

PROVENANCE_SEPARATOR: Final = " -- "
"""Between two provenance entries. ASCII; see `_provenance`."""

NOTHING_TO_GRADE: Final = "--"
"""Shown where a rate exists by convention but no evidence went into it."""

CLASSIFICATION_NOTE: Final = (
    "A classified rate and a class accuracy are not the same claim, and the "
    "higher one is not the better one. An agent that classifies everything "
    "wrongly scores 1.0 on the first and 0.0 on the second; one that classifies "
    "nothing scores 0.0 on both, and that is the safer failure -- BiasDetective "
    "can exclude an unclassified row and cannot detect a confidently wrong one."
)
"""Printed above the two rates, because a reader comparing them without it will
compare them the wrong way round."""

MATCH_CEILING_NOTE: Final = (
    "The match rate has a ceiling below 1 by construction. The corpus states "
    "nine estimates and resolves three, because an estimate nobody ever wrote "
    "an actual for is the ordinary case in a real corpus -- which is what an "
    "unresolved outcome exists for. A matcher scoring 1.0 here would have "
    "invented the other six pairings."
)
"""Printed beneath the match rate so that a 0.33 is not read as a failure."""

FUSION_NOTE = (
    "A flip needs a calibration factor, and a factor needs five resolved "
    "estimates in one group. On a corpus this size no group reaches that, so a "
    "flip count of zero here is the threshold holding rather than the layer "
    "failing. The two booleans below are what carry a claim."
)
"""Printed above the fusion numbers, for the reason `REFUSAL_NOTE` is printed
above the calibration ones: a reader who meets a column of zeros before the
sentence explaining them has already formed the wrong conclusion."""


GOVERNANCE_NOTE = (
    "A concede rate needs a challenger that reasons, and offline there is not "
    "one: MockProvider answers a bare boolean true seven times in ten, so a "
    "rate produced against it measures schema synthesis. It is printed with "
    "the provider attached rather than withheld, because withholding it would "
    "hide that the metric exists. The abstention rate is not a shortfall -- a "
    "refusal to conclude is this layer's output. The three booleans below are "
    "what carry a claim."
)
"""Printed above the governance numbers, for the reason `FUSION_NOTE` is printed
above the fusion ones: a reader who meets the concede rate before the sentence
qualifying it has already concluded something about an agent that never ran."""


REFUSAL_NOTE: Final = (
    "Zeros here are the correct answer rather than a missing measurement. "
    "BiasDetective refuses below five resolved estimates in a group, with no "
    "override, and no group in a corpus this size reaches that -- so nothing is "
    "measured, nothing is corrected and nothing is backtested. A run reporting "
    "a factor against this corpus would have broken the threshold, not beaten "
    "it. What is graded is that the threshold held and that the pass-through "
    "fired on exactly the groups that refused."
)
"""Printed above the calibration numbers, because every one of them is a zero
against this corpus and a reader who meets them without this will read the whole
section as a failed stage."""

AGED_NOTE: Final = (
    "Should be zero by construction: the monitor reaches a breach only through "
    "arithmetic on facts, so a non-zero count here is a broken property rather "
    "than a model being wrong."
)
"""Printed beside the count, because a zero with no explanation reads as a
column nobody filled in."""

UNLABELLED_EDGE_NOTE: Final = (
    "An edge the key does not label is unjudgeable rather than wrong -- the corpus "
    "labels the fusion edges it constructed, not every one that could truthfully be "
    "asserted. So the recall above counts only the labelled pairs an edge really "
    "reached, and this counts everything the layer wrote."
)
"""Why the two fusion numbers differ. Before Phase 10 the recall's numerator was
this count, which made it a ratio that could exceed 1 -- and did, the first time
a run wrote more edges than the key holds."""

UNNAMED_NOTE: Final = (
    "not false positives. The corpus labels the contradictions it planted, so a "
    "pair it never labelled is unjudgeable rather than wrong."
)
"""Printed beside the count, so the table does not invite the reading that the
detector produced that many errors."""

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
        table_row(SCORE_HEADINGS),
        table_row(["---"] * len(SCORE_HEADINGS)),
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
        f"Edges written in total: **{result.fusion_written}**. {UNLABELLED_EDGE_NOTE}",
        "",
        f"Documents graded: {result.documents}. "
        f"Model calls: {result.run.calls}. "
        f"Windows never answered about: {result.run.blind_windows}.",
        "",
        *_memory_lines(result.memory),
        *_estimation_lines(result.estimation),
        *_calibration_lines(result.calibration),
        *_fusion_lines(result.fusion),
        *_governance_lines(result.governance),
        *_cost_lines(result.cost),
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
        # One "fusion" object rather than two keys. Edge recall (graded against
        # the corpus) and the layer's own numbers (which have no answer key) are
        # the same subject, and splitting them would let a reader find one and
        # conclude the other was not measured.
        "fusion": {
            "found": result.fusion_found,
            "written": result.fusion_written,
            "expected": result.fusion_expected,
            "recall": str(result.fusion_recall),
            **_fusion_data(result.fusion),
        },
        "memory": _memory_data(result.memory),
        "estimation": _estimation_data(result.estimation),
        "calibration": _calibration_data(result.calibration),
        "governance": _governance_data(result.governance),
        # Strings for the reason the rates are strings: these are `Decimal` and
        # JSON floats would put back the representation invariant 4 excludes.
        "cost_per_document": {agent: str(spent) for agent, spent in sorted(result.cost.items())},
    }
    if provenance:
        payload["provenance"] = dict(provenance)
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _fusion_lines(fusion: FusionScore) -> list[str]:
    """What the fusion layer concluded, rendered.

    Two booleans carry the claim and everything else is context. `refusals_hold`
    says no edge reported a factor for a group `BiasDetective` refuses -- the
    threshold surviving one more caller. `flips_hold` says every finding really
    came from a predicate that moved, which is the phase's central claim and the
    one output that would make the layer noise if it were false.

    `cross_document` is printed as a count with no rate beside it, deliberately.
    The corpus plants no cross-document `estimated_as` edges as ground truth --
    doing so would fix the answer before anyone asked the question -- so there is
    nothing to score it against and a percentage would imply otherwise.
    """
    return [
        "## Fusion layer",
        "",
        f"> {FUSION_NOTE}",
        "",
        f"Edges priced: **{fusion.priced}**, flips: **{fusion.flips}** "
        f"(**{fusion.flip_rate}**). Cross-document edges priced: "
        f"**{fusion.cross_document}** (count only -- the corpus plants none).",
        "",
        *_verdict_lines(fusion.by_verdict, "priced edges"),
        f"Refusals held: **{_yes(fusion.refusals_hold)}**. "
        f"Every flip moved a verdict: **{_yes(fusion.flips_hold)}**.",
        "",
        f"Misses surveyed: **{fusion.misses}**, of which something rested on "
        f"**{fusion.damaging}** (**{fusion.damaging_rate}**). Findings standing: "
        f"**{fusion.stale_findings}** stale-decision, "
        f"**{fusion.collateral_findings}** collateral-impact.",
        "",
    ]


def _fusion_data(fusion: FusionScore) -> dict[str, Any]:
    """The same numbers as data, for Phase 10's ablation table."""
    return {
        "priced": fusion.priced,
        "flips": fusion.flips,
        "flip_rate": str(fusion.flip_rate),
        "by_verdict": dict(fusion.by_verdict),
        "refusals_hold": fusion.refusals_hold,
        "flips_hold": fusion.flips_hold,
        "cross_document": fusion.cross_document,
        "misses": fusion.misses,
        "damaging": fusion.damaging,
        "damaging_rate": str(fusion.damaging_rate),
        "stale_findings": fusion.stale_findings,
        "collateral_findings": fusion.collateral_findings,
    }


def _governance_lines(governance: GovernanceScore) -> list[str]:
    """What the governance layer concluded, rendered.

    Three rates, one answer key and three booleans, in that order of how much
    they can be trusted.

    The **abstention rate leads** because it is the number this layer exists to
    produce and the one most likely to be misread as a shortfall. The **concede
    rate carries its provider on the same line**, because printed bare it is the
    most misleading number in this whole report -- a reader has no way to tell a
    challenger's judgement from `_TRUE_BIAS`. **Merge recall is the only
    comparison against truth here**, and it is stated as one so the other two
    are not mistaken for scores.
    """
    caveat = " *(mock -- see the note above)*" if governance.mock_provider else ""
    decided = (
        f"**{governance.concede_rate}** of {governance.decided} decided{caveat}"
        if governance.decided
        else "**no measurement** -- nothing was decided"
    )
    withheld = (
        f"**{governance.abstention_precision}** -- "
        f"{governance.abstained_with_cause} of {governance.abstained} abstentions "
        f"really fail a rule"
        if governance.abstained
        else "**no measurement** -- nothing was withheld"
    )
    return [
        "## Adversarial and governance",
        "",
        f"> {GOVERNANCE_NOTE}",
        "",
        f"Findings standing: **{governance.findings}**, challenged "
        f"**{governance.challenged}**, decided **{governance.decided}**.",
        "",
        f"Abstention rate: **{governance.abstention_rate}** "
        f"({governance.abstained} withheld, {governance.emitted} concluded). "
        f"Concede rate: {decided}.",
        "",
        f"Abstention precision: {withheld}.",
        "",
        *_insufficiency_lines(governance.by_insufficiency),
        f"Assumptions curated: **{governance.assumptions}**, retirement rate "
        f"**{governance.retirement_rate}** ({governance.retirements} retired). "
        f"Kept because a live decision rests on them: "
        f"**{governance.kept_under_a_decision}**.",
        "",
        f"Merges proposed: **{governance.merges}** of {governance.merges_expected} "
        f"the corpus labels (recall **{governance.merge_recall}**).",
        "",
        f"Verdicts carry their challenge: **{_yes(governance.verdicts_hold)}**. "
        f"Nothing retired was depended on or deleted: "
        f"**{_yes(governance.retirements_hold)}**. "
        f"The gate agrees with its own rules: **{_yes(governance.abstentions_hold)}**.",
        "",
    ]


def _insufficiency_lines(counted: Mapping[str, int]) -> list[str]:
    """Which rule withheld how many conclusions.

    Omitted entirely when nothing was withheld, rather than printed as an empty
    table: a heading over no rows reads as a measurement that failed, and
    nothing withheld is a real and reportable state.
    """
    if not counted:
        return []
    return [
        table_row(("withheld because", "findings")),
        table_row(("---", "---")),
        *(
            table_row((rule, str(count)))
            for rule, count in sorted(counted.items(), key=lambda pair: (-pair[1], pair[0]))
        ),
        "",
    ]


def _governance_data(governance: GovernanceScore) -> dict[str, Any]:
    """The same numbers as data, for Phase 10's ablation table."""
    return {
        "findings": governance.findings,
        "challenged": governance.challenged,
        "decided": governance.decided,
        "conceded": governance.conceded,
        "concede_rate": str(governance.concede_rate),
        "mock_provider": governance.mock_provider,
        "assumptions": governance.assumptions,
        "merges": governance.merges,
        "merges_expected": governance.merges_expected,
        "merge_recall": str(governance.merge_recall),
        "retirements": governance.retirements,
        "retirement_rate": str(governance.retirement_rate),
        "kept_under_a_decision": governance.kept_under_a_decision,
        "emitted": governance.emitted,
        "abstained": governance.abstained,
        "abstention_rate": str(governance.abstention_rate),
        "abstained_with_cause": governance.abstained_with_cause,
        "abstention_precision": str(governance.abstention_precision),
        "by_insufficiency": dict(governance.by_insufficiency),
        "verdicts_hold": governance.verdicts_hold,
        "retirements_hold": governance.retirements_hold,
        "abstentions_hold": governance.abstentions_hold,
    }


def _calibration_lines(calibration: CalibrationScore) -> list[str]:
    """What the calibration half concluded about itself, rendered.

    Led by the caveat rather than followed by it. Every number below is zero
    against this corpus, and a reader who meets a column of zeros before the
    sentence explaining them has already formed the wrong conclusion.

    The two booleans are the row that actually carries a claim. `threshold_holds`
    is checked in both directions -- no factor below the sample size, and one
    above it -- because "never speaks" satisfies the first half on its own.
    """
    backtest = calibration.backtest
    scored = (
        f"**{backtest.score}** of {backtest.scored} corrections landed closer "
        f"(mean log error **{backtest.raw_error}** to **{backtest.corrected_error}**)."
        if backtest.graded
        else (
            f"Nothing to score: {backtest.considered} resolved estimates, none in a "
            f"group that reached the threshold."
        )
    )
    return [
        "## Calibration",
        "",
        f"> {REFUSAL_NOTE}",
        "",
        f"Groups: **{calibration.groups}**, with enough history to speak: "
        f"**{calibration.measured}** (**{calibration.measured_rate}**). "
        f"Calibration findings standing: **{calibration.findings}**.",
        "",
        *_verdict_lines(calibration.by_verdict),
        f"Refusal threshold held: **{_yes(calibration.threshold_holds)}**. "
        f"Pass-through fired on exactly the groups that refused: "
        f"**{_yes(calibration.pass_through_exact)}** "
        f"({calibration.pass_through} passed through, {calibration.corrected} corrected).",
        "",
        f"Backtest: {scored}",
        "",
    ]


def _verdict_lines(by_verdict: Mapping[str, int], subject: str = "groups") -> list[str]:
    """How a set of things split across their verdicts, or that none were read.

    `subject` names what was counted, because this renders both the calibration
    quarter (groups) and the fusion one (edges), and an empty-state sentence
    saying "no groups" under a heading about edges sends a reader looking for a
    grouping step that does not exist there.
    """
    if not by_verdict:
        return [f"No {subject} in the store, so no verdict was reached.", ""]
    named = ", ".join(f"**{count}** {verdict}" for verdict, count in sorted(by_verdict.items()))
    return [f"By verdict: {named}.", ""]


def _yes(held: bool) -> str:
    """A boolean as a word, because `True` in a metrics table reads as a value."""
    return "yes" if held else "NO"


def _calibration_data(calibration: CalibrationScore) -> dict[str, Any]:
    """The same numbers as data, for Phase 10's ablation table."""
    return {
        "groups": calibration.groups,
        "measured": calibration.measured,
        "measured_rate": str(calibration.measured_rate),
        "refused": calibration.refused,
        "by_verdict": dict(sorted(calibration.by_verdict.items())),
        "threshold_holds": calibration.threshold_holds,
        "pass_through": calibration.pass_through,
        "corrected": calibration.corrected,
        "pass_through_exact": calibration.pass_through_exact,
        "findings": calibration.findings,
        "backtest": {
            "considered": calibration.backtest.considered,
            "scored": calibration.backtest.scored,
            "graded": calibration.backtest.graded,
            "improved": calibration.backtest.improved,
            "worsened": calibration.backtest.worsened,
            "unchanged": calibration.backtest.unchanged,
            "score": str(calibration.backtest.score),
            "raw_error": str(calibration.backtest.raw_error),
            "corrected_error": str(calibration.backtest.corrected_error),
        },
    }


def _estimation_lines(estimation: EstimationResult) -> list[str]:
    """What the store learned about its own estimates, rendered.

    Two sections rather than one table, because the two are not comparable: a
    classification rate is a property of the stored records, and a match rate is
    a property of a search that mostly and correctly comes back empty.

    Every value is already computed. This module does no arithmetic, so a change
    to how a number is printed can never move it -- the rule Phase 4 set for the
    extraction table and the reason a formatting commit is safe to review
    quickly.
    """
    classification = estimation.classification
    matching = estimation.matching
    return [
        "## Estimation",
        "",
        f"Estimates on the calibration axis: **{classification.classified}** of "
        f"{classification.total} (**{classification.classified_rate}**), across "
        f"{classification.vocabulary} classes, {classification.proposed} of them not in "
        f"the corpus's own vocabulary.",
        "",
        f"Work class agreed with the answer key on **{classification.agreed}** of "
        f"{classification.identified} estimates the key could name "
        f"(**{classification.accuracy}**).",
        "",
        f"> {CLASSIFICATION_NOTE}",
        "",
        f"Estimates an outcome resolved: **{matching.resolved}** of {matching.asked} "
        f"(match rate **{matching.match_rate}**), against "
        f"{estimation.expected_resolutions} the corpus resolves.",
        "",
        table_row(PAIR_HEADINGS),
        table_row(["---"] * len(PAIR_HEADINGS)),
        _pair_row(estimation.resolution),
        "",
        *_unmatched_lines(matching),
        f"> {MATCH_CEILING_NOTE}",
        "",
    ]


def _unmatched_lines(matching: MatchScore) -> list[str]:
    """Why each unmatched estimate went unmatched, or that none did.

    Printed as a list rather than folded into the rate, because a low match rate
    means four different things and only two of them are about the model. An
    empty split is stated rather than skipped: "the causes were not reported"
    and "nothing was lost" are different, and a missing section would read as
    the second.
    """
    if not matching.unresolved:
        return ["No estimate went unmatched.", ""]
    if not matching.by_reason:
        return [f"Unmatched: {matching.unresolved}, causes not reported.", ""]
    return [
        f"Unmatched, by what lost the pairing ({matching.unresolved} in total):",
        "",
        *(
            f"- `{reason}` — {count}"
            for reason, count in sorted(matching.by_reason.items(), key=lambda row: -row[1])
        ),
        "",
    ]


def _estimation_data(estimation: EstimationResult) -> dict[str, Any]:
    """The same numbers as data, for Phase 10's ablation table."""
    return {
        "classification": {
            "total": estimation.classification.total,
            "classified": estimation.classification.classified,
            "classified_rate": str(estimation.classification.classified_rate),
            "identified": estimation.classification.identified,
            "agreed": estimation.classification.agreed,
            "accuracy": str(estimation.classification.accuracy),
            "proposed": estimation.classification.proposed,
            "vocabulary": estimation.classification.vocabulary,
        },
        "matching": {
            "asked": estimation.matching.asked,
            "resolved": estimation.matching.resolved,
            "unresolved": estimation.matching.unresolved,
            "match_rate": str(estimation.matching.match_rate),
            "expected_resolutions": estimation.expected_resolutions,
            "by_reason": dict(sorted(estimation.matching.by_reason.items())),
        },
        "resolution": _pair_data(estimation.resolution),
        "identified": estimation.identified,
    }


def _memory_lines(memory: MemoryResult) -> list[str]:
    """What the store remembers, rendered. Every value is already computed.

    Three sections rather than one table, because the three are not comparable:
    a parse rate is a property of stored text, a monitoring accuracy is a
    confusion matrix collapsed to one number, and the contradiction rows are a
    precision and a recall over identified pairs.
    """
    return [
        "## Formalization",
        "",
        f"- Assumptions stored: **{memory.formalization.total}**, "
        f"predicates that parse: **{memory.formalization.predicates_parsed}** "
        f"(**{memory.formalization.parse_rate}**).",
        f"- Checkable -- predicate *and* expiry parse: "
        f"**{memory.formalization.checkable}** "
        f"(**{memory.formalization.checkable_rate}**). "
        f"Only these can be given a verdict.",
        "",
        "## Monitoring",
        "",
        f"- Expectations: **{memory.monitoring.total}**, "
        f"agreed: **{memory.monitoring.agreed}** "
        f"(accuracy **{memory.monitoring.accuracy}**).",
        f"- Breaches: precision **{memory.monitoring.breaches.precision}**, "
        f"recall **{memory.monitoring.breaches.recall}**.",
        f"- Aged, misreported as breached: "
        f"**{memory.monitoring.aged_misreported_as_breached}**. "
        f"{AGED_NOTE}",
        "",
        "## Contradictions",
        "",
        table_row(PAIR_HEADINGS),
        table_row(["---"] * len(PAIR_HEADINGS)),
        *(
            _pair_row(found)
            for found in (memory.contradictions, memory.by_arithmetic, memory.by_model)
        ),
        "",
        f"Pairs the corpus planted: **{memory.expected}**, "
        f"of which blocking proposed **{memory.proposed}** "
        f"(recall **{memory.proposed_recall}**).",
        "",
        f"Assumptions the answer key could name: **{memory.identified}**. "
        f"Edges touching a record it could not: **{memory.unnamed}** -- "
        f"{UNNAMED_NOTE}",
        "",
    ]


def _pair_row(found: PairScore) -> str:
    """One contradiction stage as a table row."""
    return table_row(
        [
            found.label,
            str(found.true_positives),
            str(found.false_negatives),
            str(found.false_positives),
            str(found.precision),
            str(found.recall),
            str(found.f1),
        ]
    )


def _cost_lines(cost: Mapping[str, Decimal]) -> list[str]:
    """What each agent spent per document, or nothing when no run was totalled."""
    if not cost:
        return []
    return [
        "## Cost per document",
        "",
        table_row(("Agent", "USD per document")),
        table_row(("---", "---")),
        *(table_row((agent, str(spent))) for agent, spent in sorted(cost.items())),
        "",
    ]


def _memory_data(memory: MemoryResult) -> dict[str, Any]:
    """The memory half as data, for the ablation table."""
    return {
        "formalization": {
            "total": memory.formalization.total,
            "predicates_parsed": memory.formalization.predicates_parsed,
            "checkable": memory.formalization.checkable,
            "parse_rate": str(memory.formalization.parse_rate),
            "checkable_rate": str(memory.formalization.checkable_rate),
        },
        "monitoring": {
            "total": memory.monitoring.total,
            "agreed": memory.monitoring.agreed,
            "accuracy": str(memory.monitoring.accuracy),
            "aged_misreported_as_breached": memory.monitoring.aged_misreported_as_breached,
            "breaches": _pair_data(memory.monitoring.breaches),
            # Keyed "expected>reached" so the matrix survives JSON, which has no
            # tuple key. The arrow reads as the claim it is: the corpus said
            # this, the store says that.
            "matrix": {
                f"{wanted.value}>{got.value}": count
                for (wanted, got), count in sorted(
                    memory.monitoring.matrix.items(), key=lambda entry: entry[0][0].value
                )
            },
        },
        "contradictions": {
            "expected": memory.expected,
            "proposed": memory.proposed,
            "proposed_recall": str(memory.proposed_recall),
            "identified": memory.identified,
            "unnamed": memory.unnamed,
            "all": _pair_data(memory.contradictions),
            "arithmetic": _pair_data(memory.by_arithmetic),
            "model": _pair_data(memory.by_model),
        },
    }


def _pair_data(found: PairScore) -> dict[str, Any]:
    """One `PairScore`'s numbers, already computed."""
    return {
        "true_positives": found.true_positives,
        "false_positives": found.false_positives,
        "false_negatives": found.false_negatives,
        "precision": str(found.precision),
        "recall": str(found.recall),
        "f1": str(found.f1),
    }


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
    return table_row(
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
        lines.append(table_row(("Refusal", "Count")))
        lines.append(table_row(("---", "---")))
        lines += [
            table_row((refusal.value, str(count)))
            for refusal, count in sorted(
                citations.by_refusal.items(), key=lambda entry: (-entry[1], entry[0].value)
            )
        ]
    return lines


def _provenance(provenance: Mapping[str, str]) -> str:
    """What produced these numbers, on one line, in a stable order.

    The separator is ASCII, and that is not fussiness. `praxis eval` prints this
    table, and a person redirecting it into a file on Windows gets the console's
    codepage rather than UTF-8 -- so a middot arrives as a replacement byte and
    the artefact is mojibake at exactly the line that says which run it was.
    """
    return PROVENANCE_SEPARATOR.join(
        f"**{key}**: {value}" for key, value in sorted(provenance.items())
    )


def table_row(cells: tuple[str, ...] | list[str]) -> str:
    """One markdown table row. Public so the ablation renders the same shape."""
    return "| " + " | ".join(cells) + " |"
