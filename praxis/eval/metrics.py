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
from praxis.agents.scoring import Backtest
from praxis.corpus.groundtruth import ItemKind
from praxis.domain.enums import AssumptionStatus
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
    def label(self) -> str:
        """What this row is called in a report.

        Present so that `Score` and `PairScore` render through one function.
        Phase 4's table has one row per extracted kind; Phase 5 adds rows that
        are not kinds at all, and a report reaching for `.kind` on some rows and
        something else on others would be a report with two shapes.
        """
        return self.kind.value

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


def blocking_recall(proposed: int, expected: int) -> Decimal:
    """Of the contradictions the corpus labels, how many blocking proposed.

    A recall and nothing else, for the reason `fusion_recall` gives. Blocking is
    a high-recall stage by design -- it puts forward every pair worth comparing
    and lets two later stages refuse -- so a pair it proposes that the key does
    not label is not thereby wrong, and a precision against that denominator
    would punish the stage for doing its job.

    It is reported because it is the ceiling the two settlement recalls sit
    under. A pair blocking never proposed is a pair nothing will ever look at,
    and without this number a low detection recall cannot be told apart from a
    model that was never asked.
    """
    return _ratio(proposed, expected, PERFECT)


def _ratio(numerator: int, denominator: int, when_empty: Decimal) -> Decimal:
    """A rate, or the stated convention when there is nothing to divide by."""
    if denominator == 0:
        return _quantized(when_empty)
    return _quantized(Decimal(numerator) / Decimal(denominator))


def _quantized(value: Decimal) -> Decimal:
    """Round a rate to the places a report prints, once and in one place."""
    return value.quantize(RATE_PLACES)


@dataclass(frozen=True, slots=True)
class PairScore:
    """Precision and recall over things identified by identity, not by overlap.

    `Score` pairs an extraction with an answer-key item by *byte overlap*,
    because an extraction points at a place and the two coordinate systems are
    cut by different processes. Everything Phase 5 grades is already identified:
    a `contradicts` edge is a pair of record ids and a monitoring verdict is
    about one assumption. Comparing those by overlap would invent a difficulty
    they do not have.

    Attributes:
        label: What this row is called in a report.
        true_positives: Found, and expected.
        false_positives: Found, and not expected.
        false_negatives: Expected, and not found.
    """

    label: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    @property
    def precision(self) -> Decimal:
        """Of what was found, how much should have been."""
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


def overall(scores: Iterable[Score], label: str = "overall") -> PairScore:
    """Micro-average the per-kind scores into one precision and one recall.

    Micro rather than macro: an ablation rung is read as one number and a macro
    average would let the rarest kind swing it. Counts are already comparable
    because every kind is paired the same way.
    """
    counted = tuple(scores)
    return PairScore(
        label=label,
        true_positives=sum(score.true_positives for score in counted),
        false_positives=sum(score.false_positives for score in counted),
        false_negatives=sum(score.false_negatives for score in counted),
    )


def pair_score[T](label: str, found: Iterable[T], expected: Iterable[T]) -> PairScore:
    """Count two sets of identified things into a score.

    Args:
        label: What this row is called.
        found: What the run produced.
        expected: What the corpus says is there.

    Returns:
        The score. Duplicates on either side collapse, which is right for
        everything this grades -- one contradiction found twice is one
        contradiction.
    """
    produced, wanted = set(found), set(expected)
    return PairScore(
        label=label,
        true_positives=len(produced & wanted),
        false_positives=len(produced - wanted),
        false_negatives=len(wanted - produced),
    )


@dataclass(frozen=True, slots=True)
class FormalizationScore:
    """How much of what was extracted is machine-checkable.

    Not a precision or a recall, and deliberately not squeezed into one. No
    answer-key entry says "this assumption should have compiled": the question
    is whether the predicate that got stored *parses*, which is a property of
    the text rather than a match against an expectation. Reporting it as a
    recall would invite a reader to compare it with the extraction recalls
    beside it, and the two mean different things.

    Attributes:
        label: What this row is called in a report.
        total: Assumptions the store holds.
        predicates_parsed: How many of their predicates parse.
        checkable: How many have a predicate *and* an expiry that both parse --
            the only ones `AssumptionMonitor` can reach a verdict about.
    """

    label: str = "formalization"
    total: int = 0
    predicates_parsed: int = 0
    checkable: int = 0

    @property
    def parse_rate(self) -> Decimal:
        """Of the predicates stored, how many are expressions."""
        return _ratio(self.predicates_parsed, self.total, PERFECT)

    @property
    def checkable_rate(self) -> Decimal:
        """Of the assumptions stored, how many the monitor could decide."""
        return _ratio(self.checkable, self.total, PERFECT)


_NOT_A_VIOLATION: Final[frozenset[AssumptionStatus]] = frozenset(
    {AssumptionStatus.EXPIRED, AssumptionStatus.UNVERIFIED}
)
"""The states an assumption is in when nothing has shown it false.

`HOLDING` is excluded. An assumption reported breached that the corpus expected
to hold is a wrong verdict, but it is a wrong verdict about a *measured*
quantity -- a different failure from confusing age with violation.
"""


@dataclass(frozen=True, slots=True)
class MonitoringScore:
    """What a monitoring run concluded against what the corpus expected.

    The number this phase exists to report is `aged_misreported_as_breached`.
    A monitor that cannot tell a violated predicate from an unmeasured or an
    expired one is a monitor whose findings nobody can act on, and that count
    measures it directly. **It should be zero, and by construction it is**:
    `praxis.monitor` reaches `BREACHED` only through arithmetic on facts, so a
    non-zero value means that property was broken rather than that a model was
    wrong.

    Attributes:
        label: What this row is called in a report.
        matrix: `(expected, reached)` to how many assumptions did that.
    """

    label: str = "monitoring"
    matrix: Mapping[tuple[AssumptionStatus, AssumptionStatus], int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        """Assumptions the corpus had an expectation about."""
        return sum(self.matrix.values())

    @property
    def agreed(self) -> int:
        """How many verdicts matched what was expected."""
        return sum(count for (wanted, got), count in self.matrix.items() if wanted is got)

    @property
    def accuracy(self) -> Decimal:
        """Of the expectations, how many were met."""
        return _ratio(self.agreed, self.total, PERFECT)

    @property
    def aged_misreported_as_breached(self) -> int:
        """Assumptions that had merely aged and were reported as violated.

        Expected `EXPIRED` or `UNVERIFIED` -- nothing measured them, or their
        last verdict went stale -- and reported `BREACHED`. Counted by name so
        a report cannot omit it by not thinking of it.
        """
        return sum(
            count
            for (wanted, got), count in self.matrix.items()
            if got is AssumptionStatus.BREACHED and wanted in _NOT_A_VIOLATION
        )

    @property
    def breaches(self) -> PairScore:
        """Precision and recall over the breaches specifically."""
        return PairScore(
            label="breach",
            true_positives=self.matrix.get(
                (AssumptionStatus.BREACHED, AssumptionStatus.BREACHED), 0
            ),
            false_positives=sum(
                count
                for (wanted, got), count in self.matrix.items()
                if got is AssumptionStatus.BREACHED and wanted is not AssumptionStatus.BREACHED
            ),
            false_negatives=sum(
                count
                for (wanted, got), count in self.matrix.items()
                if wanted is AssumptionStatus.BREACHED and got is not AssumptionStatus.BREACHED
            ),
        )


def monitoring_score(
    reached: Mapping[str, AssumptionStatus], expected: Mapping[str, AssumptionStatus]
) -> MonitoringScore:
    """Count a monitoring run against a corpus's expectations.

    Args:
        reached: What the run concluded, by answer-key item id.
        expected: What the corpus expects, by the same id.

    Returns:
        The confusion matrix over the assumptions the corpus has an expectation
        about. An expectation the run said nothing about counts as `UNVERIFIED`,
        because that is what an assumption nothing evaluated really is.
    """
    matrix: dict[tuple[AssumptionStatus, AssumptionStatus], int] = {}
    for item_id, wanted in expected.items():
        got = reached.get(item_id, AssumptionStatus.UNVERIFIED)
        matrix[(wanted, got)] = matrix.get((wanted, got), 0) + 1
    return MonitoringScore(matrix=matrix)


COST_PLACES: Final = Decimal("0.000001")
"""How precisely a per-document cost is reported.

Six places because these are fractions of a cent: ADR 0006's fifth assumption
is `cost_per_document_usd <= 0.05`, and rounding to cents would report most of
this pipeline as free.
"""


@dataclass(frozen=True, slots=True)
class ClassificationScore:
    """Whether the estimates in a store sit on the axis calibration groups by.

    Two numbers that are easy to confuse and mean opposite things. `classified`
    counts the estimates that carry any class at all; `agreed` counts those
    whose class is the one the answer key names. An agent that classifies
    everything wrongly scores 1.0 on the first and 0.0 on the second, and an
    agent that refuses everything scores 0.0 on both -- which is worse for a
    reader and better for `BiasDetective`, since an `unclassified` row is one it
    can exclude and a wrongly classified one is one it silently averages.

    `proposed` is the third number and it is about the *vocabulary* rather than
    about any one row. A run that invents a new class for every estimate has
    classified everything and grouped nothing, and that failure is invisible in
    a per-row accuracy: every class would be plausible and no two estimates
    would share one.

    Attributes:
        label: What this row is called in a report.
        total: Estimates the store holds.
        classified: How many carry something other than `unclassified`.
        identified: How many the answer key could name at all. The denominator
            under `agreed`, and reported because an accuracy over two estimates
            and over twenty are not the same evidence.
        agreed: Of those, how many carry the class the key names.
        proposed: Classes this run introduced that the store did not hold.
        vocabulary: Distinct classes across every stored estimate.
    """

    label: str = "classification"
    total: int = 0
    classified: int = 0
    identified: int = 0
    agreed: int = 0
    proposed: int = 0
    vocabulary: int = 0

    @property
    def classified_rate(self) -> Decimal:
        """Of the estimates stored, how many are on the axis at all."""
        return _ratio(self.classified, self.total, EMPTY)

    @property
    def accuracy(self) -> Decimal:
        """Of the estimates the key names, how many carry the right class."""
        return _ratio(self.agreed, self.identified, EMPTY)


@dataclass(frozen=True, slots=True)
class MatchScore:
    """What happened to every estimate `OutcomeMatcher` was asked about.

    The match rate is the number this agent is judged on, and it is reported
    beside the reasons rather than alone, because a low one means four different
    things and only two of them are about the model. A pair deterministic
    selection never proposed was lost before any model saw it; a pair the model
    read and declined is the ordinary case and usually correct; a citation that
    failed the gate is a claim whose evidence did not survive; and an
    incomparable unit is a refusal this agent is supposed to make.

    `asked` is every estimate that reached the agent, which is also every
    outcome row written -- one estimate always leaves with exactly one, resolved
    or not. That identity is what makes the rate a rate rather than a count over
    an unstated denominator.

    Attributes:
        label: What this row is called in a report.
        asked: Estimates the matcher was given.
        resolved: How many an outcome really answered.
        by_reason: For the rest, how many were lost to each cause, keyed by the
            `Unmatched` member's value.
    """

    label: str = "matching"
    asked: int = 0
    resolved: int = 0
    by_reason: Mapping[str, int] = field(default_factory=dict)

    @property
    def unresolved(self) -> int:
        """Estimates that left with an `unresolved` outcome. Rows, not silences."""
        return self.asked - self.resolved

    @property
    def match_rate(self) -> Decimal:
        """Of the estimates asked about, how many an actual answered.

        Empty rather than perfect when nothing was asked. A store with no
        estimates has not matched everything; it has matched nothing, and
        reporting 1.0 there would put a starved pipeline at the top of the table.
        """
        return _ratio(self.resolved, self.asked, EMPTY)


def cost_per_document(spent: Mapping[str, Decimal], documents: int) -> dict[str, Decimal]:
    """What each agent cost per document.

    Args:
        spent: Agent to total cost, from `praxis.store.traces.cost_by_agent`.
        documents: How many documents the run covered.

    Returns:
        Agent to cost per document. Empty when there were no documents --
        dividing by nothing would be a number nobody could act on.
    """
    if documents < 1:
        return {}
    return {
        agent: (total / Decimal(documents)).quantize(COST_PLACES) for agent, total in spent.items()
    }


@dataclass(frozen=True, slots=True)
class MaeImprovement:
    """The brief's calibration MAE improvement: error before and after.

    Errors are mean absolute *log* ratios, the scale `ScoringAgent` works in.

    Attributes:
        scored: Rows a correction existed for. Zero means not measured.
        before: Mean absolute log error of the raw estimates.
        after: The same after the fitted factor was applied.
    """

    scored: int = 0
    before: Decimal = EMPTY
    after: Decimal = EMPTY

    @property
    def measured(self) -> bool:
        """Whether anything was scored. Keeps "not measured" apart from "no change"."""
        return self.scored > 0

    @property
    def absolute(self) -> Decimal:
        """How much the error fell. Negative means the correction hurt."""
        return (self.before - self.after).quantize(RATE_PLACES)

    @property
    def relative(self) -> Decimal:
        """The same as a share of the error there was to remove.

        Zero when there was none -- an improvement on no error is undefined.
        """
        if self.before == 0:
            return _quantized(EMPTY)
        return _quantized((self.before - self.after) / self.before)


def mae_improvement(backtest: Backtest) -> MaeImprovement:
    """Read a prequential walk's two errors as the brief's metric."""
    return MaeImprovement(
        scored=backtest.scored, before=backtest.raw_error, after=backtest.corrected_error
    )
