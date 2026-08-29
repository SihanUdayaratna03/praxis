"""Grading Half B: what the store learned about its own estimates.

The counterpart to `praxis.eval.memory`, and the same rule holds for the same
reason: **every number here is read back out of SQLite.** A work class is a
field on a record, and if a run reported one and the write was refused, the
store is what a person will read next month. The one thing taken from a run
rather than from a store is *why* an estimate went unmatched -- selection never
proposed a passage, the model declined, a citation failed, a unit could not be
reconciled -- because the outcome row is identical in every case, which is
exactly the property that makes re-running free, and it is also why those four
have to be reported apart. A low match rate means four different things and
only two of them are about the model.

Records join to the answer key through the *estimate pairing* rather than by
id. The store's ids are allocated by the store, the key's by the generator, and
nothing relates them but the passage both point at -- `praxis.eval.matching`'s
whole subject.

Two numbers here are worth stating carefully before anyone reads the table.

**A classified rate and a class accuracy are not the same claim, and the higher
one is not the better one.** An agent that classifies everything wrongly scores
1.0 on the first and 0.0 on the second; an agent that classifies nothing scores
0.0 on both, and that is the *safer* failure -- `BiasDetective` can exclude an
`unclassified` row and cannot detect a confidently wrong one.

**The match rate has a ceiling below 1 by construction, and that is correct.**
The corpus states nine estimates and resolves three of them, because an estimate
nobody ever wrote an actual for is the ordinary case in a real corpus. The
remaining six are what `unresolved` outcomes exist for; a matcher scoring 1.0
here would have invented six pairings.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from praxis.agents.classifier import UNCLASSIFIED, is_classified
from praxis.agents.estimation import EstimationRun
from praxis.corpus.groundtruth import CorpusGroundTruth, ItemKind
from praxis.domain.enums import MatchQuality
from praxis.domain.records import Estimate, Outcome
from praxis.eval.matching import Pairing
from praxis.eval.metrics import ClassificationScore, MatchScore, PairScore, pair_score
from praxis.store.repository import Repository

WORK_CLASS_FIELD = "work_class"
"""The answer key's own name for the field, which is not the record's accident.

`praxis.corpus.templates` writes what it expects under `ExpectedField.name`, and
this module is one of the two places the two vocabularies meet. Spelling it
here rather than reaching for `Estimate.work_class` keeps the join to the key
explicit.
"""


@dataclass(frozen=True, slots=True)
class EstimationResult:
    """What Half B concluded, against what the corpus expected.

    Attributes:
        classification: Whether the store's estimates sit on the calibration
            axis, and whether they sit on the right part of it.
        matching: What happened to every estimate the matcher was asked about,
            with the losses split by the stage that caused them.
        resolution: Precision and recall over *which* estimates were resolved,
            against the ones the key says have an actual. Distinct from the
            match rate: that says how many were answered, this says whether
            they were the right ones.
        identified: Stored estimates the pairing could name in the key's terms.
            The denominator under everything that joins through it.
        expected_resolutions: Estimates the key resolves. The ceiling the match
            rate sits under, reported so nobody reads a 0.33 as a failure.
    """

    classification: ClassificationScore = field(default_factory=ClassificationScore)
    matching: MatchScore = field(default_factory=MatchScore)
    resolution: PairScore = field(default_factory=lambda: PairScore(label="resolution"))
    identified: int = 0
    expected_resolutions: int = 0


def grade_estimation(
    repository: Repository,
    truth: CorpusGroundTruth,
    estimates: Pairing,
    *,
    run: EstimationRun | None = None,
) -> EstimationResult:
    """Grade the classification and matching passes over a store.

    Args:
        repository: The store the passes wrote into.
        truth: The answer key beside the documents that were ingested.
        estimates: The estimate pairing, which is the only thing relating a
            stored record to an item in the key.
        run: What the Half B pass reported, for the split of *why* an estimate
            went unmatched. Nothing is graded from its records -- those come out
            of the store.

    Returns:
        Every number the estimation half of the report prints.
    """
    stored = repository.list_all(Estimate)
    named = names_in(estimates)
    outcomes = repository.list_all(Outcome)
    expected = expected_resolutions(truth)
    return EstimationResult(
        classification=classification_score(stored, named, expected_classes(truth)),
        matching=match_score(outcomes, run),
        resolution=pair_score("resolution", resolved_in(outcomes, named), expected),
        identified=len(named),
        expected_resolutions=len(expected),
    )


def names_in(estimates: Pairing) -> dict[str, str]:
    """Record id to answer-key item id, for every estimate that was paired.

    The join, and a function rather than a comprehension at each use because
    two scores depend on it and two copies would be two chances to pair one
    record differently.
    """
    return {match.claim.record_id: match.item.item_id for match in estimates.matched}


def expected_classes(truth: CorpusGroundTruth) -> dict[str, str]:
    """What class the key says each estimate is, by item id."""
    return {
        item.item_id: expected.value
        for item in truth.items
        if item.kind is ItemKind.ESTIMATE
        for expected in item.fields
        if expected.name == WORK_CLASS_FIELD
    }


def classification_score(
    stored: Iterable[Estimate], named: Mapping[str, str], expected: Mapping[str, str]
) -> ClassificationScore:
    """Whether the store's estimates are on the axis, and on the right part of it.

    `classified` counts over every stored estimate, because carrying a class is
    a property of the record and needs no answer-key entry to be true. `agreed`
    counts only over the ones the key can name, which is why both denominators
    are reported rather than one.
    """
    records = tuple(stored)
    classes = {record.work_class for record in records if is_classified(record)}
    agreed = sum(
        1
        for record in records
        if record.id in named and record.work_class == expected.get(named[record.id])
    )
    return ClassificationScore(
        total=len(records),
        classified=sum(1 for record in records if is_classified(record)),
        identified=sum(1 for record in records if record.id in named),
        agreed=agreed,
        proposed=len(classes - set(expected.values())),
        vocabulary=len(classes),
    )


def match_score(outcomes: Iterable[Outcome], run: EstimationRun | None) -> MatchScore:
    """What happened to every estimate the matcher was asked about.

    The counts come from the store -- one outcome row per estimate asked about,
    which is the property the agent guarantees -- and the *split* comes from the
    run, because the row is identical whichever stage lost the pairing. That is
    the same division `praxis.eval.memory` draws for a contradiction's
    settlement stage, and for the same reason.
    """
    rows = tuple(outcomes)
    return MatchScore(
        asked=len(rows),
        resolved=sum(1 for row in rows if row.match_quality is not MatchQuality.UNRESOLVED),
        by_reason=_reasons(run),
    )


def resolved_in(outcomes: Iterable[Outcome], named: Mapping[str, str]) -> frozenset[str]:
    """Which estimates the store really resolved, in the key's ids.

    Only the ones the pairing could name. An outcome against an estimate the key
    does not label is not thereby wrong -- the corpus labels the resolutions it
    planted, not every one that could truthfully be asserted -- so it is left out
    of the score rather than counted as a false positive.
    """
    return frozenset(
        named[str(outcome.estimate_id)]
        for outcome in outcomes
        if outcome.match_quality is not MatchQuality.UNRESOLVED
        and str(outcome.estimate_id) in named
    )


def expected_resolutions(truth: CorpusGroundTruth) -> frozenset[str]:
    """Every estimate the corpus resolves, as the item id of the estimate.

    Keyed on the estimate rather than on the outcome, because that is what the
    store can be asked about: a record either has a resolved outcome standing
    against it or it does not, and the outcome's own id is allocated by whichever
    side wrote it.
    """
    return frozenset(
        item.resolves_item_id
        for item in truth.items
        if item.kind is ItemKind.OUTCOME and item.resolves_item_id is not None
    )


def _reasons(run: EstimationRun | None) -> dict[str, int]:
    """How many estimates each cause of an unmatched pairing accounted for.

    An empty mapping when no run was supplied, rather than zeros for every
    member: "the split was not reported" and "nothing was lost to any of these"
    are different, and a table of zeros would claim the second.
    """
    if run is None:
        return {}
    counted: dict[str, int] = {}
    for lost in run.unmatched:
        counted[lost.reason.value] = counted.get(lost.reason.value, 0) + 1
    return counted


def unclassified_in(stored: Iterable[Estimate]) -> int:
    """Estimates still off the axis, which is the row a person would act on."""
    return sum(1 for record in stored if record.work_class == UNCLASSIFIED)
