"""Counting the pairs. Arithmetic, and deliberately nothing else.

Invariant 3: statistics are deterministic code, never a model, and this is the
module that invariant is about for Phase 4. Everything here is a function of
counts. Nothing reads a clock, nothing iterates a set, nothing decides what
counts as a match -- `praxis.eval.matching` did that, and the split exists so
this file can be property-tested with `hypothesis` over arbitrary counts rather
than over plausible-looking runs.

Two conventions that are choices rather than definitions, and both are the
conservative reading:

- **Precision with nothing extracted is 0, not 1.** The vacuous truth -- "every
  record I wrote was right" over zero records -- is exactly the number a broken
  extractor would score, and a table where a pipeline that produces nothing
  ties with a perfect one is a table nobody can read.
- **Recall with nothing expected is 1.** There was nothing to find and nothing
  was missed. Unlike the case above this cannot flatter a failure, because the
  corpus decides the denominator and the pipeline cannot affect it.

`Decimal` throughout, never float. These are summed across kinds and compared
between runs, and invariant 4's argument -- that binary floating point makes a
sum depend on its order -- is about quantities, of which a rate is one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

from praxis.agents.errors import Refusal
from praxis.agents.results import Refused, Stage
from praxis.corpus.groundtruth import ItemKind
from praxis.eval.matching import Match, Pairing

RATE_PLACES: Final = Decimal("0.0001")
"""How precisely a rate is reported.

Four places, and *every* rate goes through the quantizer including the ones
that come straight from a convention -- a table where one column says `0.0000`
and the next says `0` is a table whose reader wonders what the difference is,
and in a JSON file it is a diff between two runs that agreed.
"""

PERFECT: Final = Decimal(1)
EMPTY: Final = Decimal(0)


@dataclass(frozen=True, slots=True)
class Score:
    """Precision, recall and F1 over one kind of claim.

    Attributes:
        kind: What was being extracted.
        true_positives: Records paired with a real item.
        false_positives: Records paired with nothing, or with a distractor.
        false_negatives: Real items nothing was extracted for.
        distracted: How many of the false positives were the corpus's traps.
            Reported apart because it is the only false-positive count that says
            something about *judgement* rather than about noise.
        fields_expected: Expected fields across every matched record.
        fields_correct: How many of them the extraction got right.
    """

    kind: ItemKind
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    distracted: int = 0
    fields_expected: int = 0
    fields_correct: int = 0

    @property
    def precision(self) -> Decimal:
        """Of what was extracted, how much should have been."""
        return _ratio(self.true_positives, self.true_positives + self.false_positives, EMPTY)

    @property
    def recall(self) -> Decimal:
        """Of what should have been found, how much was."""
        return _ratio(self.true_positives, self.true_positives + self.false_negatives, PERFECT)

    @property
    def f1(self) -> Decimal:
        """The harmonic mean, which is zero when either half is."""
        total = self.precision + self.recall
        if total == 0:
            return _quantized(EMPTY)
        return _quantized(2 * self.precision * self.recall / total)

    @property
    def field_accuracy(self) -> Decimal:
        """Of the fields the key expected on matched records, how many were right.

        Conditioned on the match on purpose: a field is only gradeable once the
        record it belongs to has been paired with an item, so mixing this into
        recall would double-count a miss.
        """
        return _ratio(self.fields_correct, self.fields_expected, PERFECT)


@dataclass(frozen=True, slots=True)
class CitationIntegrity:
    """How often the pipeline's citations were honest, and how they failed.

    The number Phase 4 exists to establish a baseline for, and the one that is
    *not* an accuracy figure: a fabricated quotation that never reached the
    store is a success for the gate and a failure for the model, and both facts
    are worth a column.

    Attributes:
        offered: Claims that survived the citation gate and were stored.
        refused: Claims the gate stopped.
        by_refusal: How many of each defect.
        by_stage: Which agent lost them.
    """

    offered: int = 0
    refused: int = 0
    by_refusal: Mapping[Refusal, int] = field(default_factory=dict)
    by_stage: Mapping[Stage, int] = field(default_factory=dict)

    @property
    def integrity(self) -> Decimal:
        """Of every claim a model made, how many cited something real."""
        return _ratio(self.offered, self.offered + self.refused, PERFECT)

    @property
    def fabrication_rate(self) -> Decimal:
        """How often a quotation was in none of the passages the agent saw."""
        return _ratio(
            self.by_refusal.get(Refusal.FABRICATED_QUOTE, 0), self.offered + self.refused, EMPTY
        )

    @property
    def mis_attribution_rate(self) -> Decimal:
        """How often it was in a passage the agent saw, but not the cited one."""
        return _ratio(
            self.by_refusal.get(Refusal.MIS_ATTRIBUTED_QUOTE, 0),
            self.offered + self.refused,
            EMPTY,
        )


def score(pairing: Pairing, kind: ItemKind) -> Score:
    """Count one kind's pairing into a score."""
    return Score(
        kind=kind,
        true_positives=len(pairing.matched),
        false_positives=pairing.false_positives,
        false_negatives=len(pairing.missed),
        distracted=len(pairing.distracted),
        fields_expected=sum(len(match.fields) for match in pairing.matched),
        fields_correct=sum(match.fields_correct for match in pairing.matched),
    )


def exact_matches(matches: Iterable[Match]) -> int:
    """Matched records that got every expected field right.

    The strictest reading of a hit, reported beside the lenient one because the
    difference between them is the difference between finding a decision and
    reading it correctly.
    """
    return sum(1 for match in matches if match.exact)


def citation_integrity(refused: Sequence[Refused], offered: int) -> CitationIntegrity:
    """Summarise what the citation gate stopped, and how.

    Args:
        refused: Every record the run lost, from `ExtractionRun.refused`.
        offered: How many records reached the store.
    """
    by_refusal: dict[Refusal, int] = {}
    by_stage: dict[Stage, int] = {}
    for entry in refused:
        by_refusal[entry.refusal] = by_refusal.get(entry.refusal, 0) + 1
        by_stage[entry.stage] = by_stage.get(entry.stage, 0) + 1
    return CitationIntegrity(
        offered=offered,
        refused=len(refused),
        by_refusal=by_refusal,
        by_stage=by_stage,
    )


def fusion_recall(found: int, expected: int) -> Decimal:
    """Of the `estimated_as` edges the corpus labels, how many were written.

    Its own function rather than a `Score`, because there is no meaningful
    precision to report yet: an edge the corpus does not label is not thereby
    wrong -- the key labels the fusion edges it *constructed*, not every one
    that could truthfully be asserted. Reporting a precision against that
    denominator would be reporting a number nobody could act on.
    """
    return _ratio(found, expected, PERFECT)


def _ratio(numerator: int, denominator: int, when_empty: Decimal) -> Decimal:
    """A rate, or the stated convention when there is nothing to divide by."""
    if denominator == 0:
        return _quantized(when_empty)
    return _quantized(Decimal(numerator) / Decimal(denominator))


def _quantized(value: Decimal) -> Decimal:
    """Round a rate to the places a report prints, once and in one place."""
    return value.quantize(RATE_PLACES)
