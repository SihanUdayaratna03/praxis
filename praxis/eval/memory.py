"""Grading what the memory agents concluded, once extraction has run.

Phase 4 graded three questions -- was a decision found, was it read correctly,
did the citation point at something real. Phase 5 adds three more, and they are
about what the store *knows* rather than about what it copied: did the
assumption compile into something a machine can check, did the monitor reach the
right verdict about it, and did the detector find the pairs that cannot both
hold.

**Every number here is read back out of SQLite**, which is `praxis.eval.harness`'s
rule and holds for a reason that is sharper on this side. A monitoring verdict
is a *status on a record*; if the run reported `BREACHED` and the write was
refused, the store is what a person will read next month, so the store is what
is graded. The same is true of a compiled predicate and of a `contradicts` edge.

The one thing taken from a run rather than from the store is **which stage
settled a pair**. That is a fact about the run and not about the graph: the edge
is identical either way, which is exactly the property that makes re-running
free, and it is also why the recalls have to be reported apart. A low
contradiction recall means three different things if blocking never proposed the
pair, if the interval arithmetic could not read the predicates, or if the model
was asked and said no.

Records are joined to the answer key through the *assumption pairing* rather
than by id. The store's ids are allocated by the store and the key's are
allocated by the generator; nothing relates them but the passage both point at,
which is `praxis.eval.matching`'s whole subject. A record the pairing could not
name is counted rather than dropped -- a contradiction between two records the
key does not label is not thereby wrong, and scoring it as a false positive
would report the corpus's silence as the detector's error.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from praxis.agents.detection import DetectionRun
from praxis.agents.formalizer import is_checkable
from praxis.agents.results import Contradiction
from praxis.corpus.groundtruth import CorpusGroundTruth
from praxis.domain.enums import AssumptionStatus
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Link
from praxis.eval.matching import Pairing
from praxis.eval.metrics import (
    FormalizationScore,
    MonitoringScore,
    PairScore,
    blocking_recall,
    monitoring_score,
    pair_score,
)
from praxis.predicates.parser import parses
from praxis.store.repository import Repository

Pair = tuple[str, str]
"""Two item ids, sorted. `contradicts` is symmetric and the two sides allocate
their ids independently, so a pair only has one identity once it is ordered."""


@dataclass(frozen=True, slots=True)
class MemoryResult:
    """What the three memory passes concluded, against what the corpus expected.

    Attributes:
        formalization: How much of what was extracted is machine-checkable.
        monitoring: The confusion matrix over the corpus's expected verdicts.
        contradictions: Every `contradicts` edge the store holds, against the
            pairs the key labels.
        by_arithmetic: The same expectations, counting only the pairs the
            interval arithmetic settled.
        by_model: The same expectations, counting only the pairs a model judged.
        identified: Stored assumptions the pairing could name in the key's terms.
            The denominator under every number above that joins through it, and
            reported because a monitoring accuracy over three assumptions and
            over thirty are not the same evidence.
        proposed: Labelled pairs that blocking put forward as candidates.
        expected: Pairs the key labels.
        unnamed: `contradicts` edges touching a record the key does not label.
            Not false positives: the corpus labels the contradictions it
            *planted*, not every one that could truthfully be asserted.
    """

    formalization: FormalizationScore = field(default_factory=FormalizationScore)
    monitoring: MonitoringScore = field(default_factory=MonitoringScore)
    contradictions: PairScore = field(default_factory=lambda: PairScore(label="contradiction"))
    by_arithmetic: PairScore = field(default_factory=lambda: PairScore(label="arithmetic"))
    by_model: PairScore = field(default_factory=lambda: PairScore(label="model"))
    identified: int = 0
    proposed: int = 0
    expected: int = 0
    unnamed: int = 0

    @property
    def proposed_recall(self) -> Decimal:
        """Of the labelled contradictions, how many blocking proposed at all.

        The ceiling everything downstream sits under: a pair blocking never put
        forward is a pair no stage will ever look at, so a detector recall read
        without this one cannot say whether the model was wrong or never asked.
        """
        return blocking_recall(self.proposed, self.expected)


def grade_memory(
    repository: Repository,
    truth: CorpusGroundTruth,
    assumptions: Pairing,
    *,
    detection: DetectionRun | None = None,
) -> MemoryResult:
    """Grade the formalization, monitoring and detection passes over a store.

    Args:
        repository: The store the passes wrote into.
        truth: The answer key beside the documents that were ingested.
        assumptions: The assumption pairing, which is the only thing relating a
            stored record to an item in the key.
        detection: What the detection run reported, for the settlement split and
            for what blocking proposed. Nothing is graded from its edges -- those
            come out of the store.

    Returns:
        Every number the memory half of the report prints.
    """
    stored = repository.list_all(Assumption)
    named = names_in(assumptions)
    expected = expected_pairs(truth)
    found, unnamed = found_pairs(repository, named)
    settled = _settled(detection, named)
    return MemoryResult(
        formalization=formalization_score(stored),
        monitoring=monitoring_score(reached_verdicts(stored, named), expected_verdicts(truth)),
        contradictions=pair_score("contradiction", found, expected),
        by_arithmetic=pair_score("arithmetic", settled[0], expected),
        by_model=pair_score("model", settled[1], expected),
        identified=len(named),
        proposed=len(proposed_pairs(detection, named) & expected),
        expected=len(expected),
        unnamed=unnamed,
    )


def names_in(assumptions: Pairing) -> dict[str, str]:
    """Record id to answer-key item id, for every assumption that was paired.

    The join, and the reason it is a function rather than a dictionary
    comprehension at each use: three scores depend on it and three copies of it
    would be three chances to pair one of them differently.
    """
    return {match.claim.record_id: match.item.item_id for match in assumptions.matched}


def formalization_score(stored: Iterable[Assumption]) -> FormalizationScore:
    """How much of what the store holds is machine-checkable.

    Over every assumption, not only the paired ones: whether a predicate parses
    is a property of the text and needs no answer-key entry to be true. This is
    also why it is not a recall -- see `FormalizationScore`.
    """
    records = tuple(stored)
    return FormalizationScore(
        total=len(records),
        predicates_parsed=sum(1 for record in records if parses(record.predicate)),
        checkable=sum(1 for record in records if is_checkable(record)),
    )


def reached_verdicts(
    stored: Iterable[Assumption], named: Mapping[str, str]
) -> dict[str, AssumptionStatus]:
    """What the store now says about each assumption, in the key's ids.

    The status on the record rather than the verdict the run returned. A run
    reports what it concluded; the store holds what survived being written, and
    the second is what anyone reads afterwards.
    """
    return {named[record.id]: record.status for record in stored if record.id in named}


def expected_verdicts(truth: CorpusGroundTruth) -> dict[str, AssumptionStatus]:
    """What the corpus expects a monitoring run to conclude, by item id."""
    return {verdict.item_id: verdict.status for verdict in truth.monitoring.verdicts}


def expected_pairs(truth: CorpusGroundTruth) -> frozenset[Pair]:
    """Every contradiction the corpus planted, as an ordered pair of item ids."""
    return frozenset(
        _ordered(item.item_id, link.target_item_id)
        for item in truth.items
        for link in item.links
        if link.link_type is LinkType.CONTRADICTS
    )


def found_pairs(repository: Repository, named: Mapping[str, str]) -> tuple[frozenset[Pair], int]:
    """The `contradicts` edges the store holds, in the key's ids.

    Returns:
        The pairs both of whose records the key names, and how many edges
        touched a record it does not. The second is reported rather than scored:
        the key labels the contradictions it constructed, so an edge between two
        unlabelled records is unjudgeable rather than wrong.
    """
    edges = tuple(
        link for link in repository.list_all(Link) if link.link_type is LinkType.CONTRADICTS
    )
    pairs = {
        _ordered(named[link.source_id], named[link.target_id])
        for link in edges
        if link.source_id in named and link.target_id in named
    }
    return frozenset(pairs), sum(
        1 for link in edges if link.source_id not in named or link.target_id not in named
    )


def proposed_pairs(detection: DetectionRun | None, named: Mapping[str, str]) -> frozenset[Pair]:
    """What blocking put forward, in the key's ids.

    Taken from the run because a candidate is not written anywhere: it is the
    question the later stages were asked, and a pair that was never asked about
    is the one failure a recall over the answers cannot distinguish.
    """
    if detection is None:
        return frozenset()
    return frozenset(
        _ordered(named[candidate.left], named[candidate.right])
        for candidate in detection.result.blocking.candidates
        if candidate.left in named and candidate.right in named
    )


def _settled(
    detection: DetectionRun | None, named: Mapping[str, str]
) -> tuple[frozenset[Pair], frozenset[Pair]]:
    """The pairs each stage decided, in the key's ids: arithmetic, then model."""
    if detection is None:
        return frozenset(), frozenset()
    return (
        _named_pairs(detection.result.by_arithmetic, named),
        _named_pairs(detection.result.by_model, named),
    )


def _named_pairs(found: Iterable[Contradiction], named: Mapping[str, str]) -> frozenset[Pair]:
    """One stage's contradictions, keeping only those the key can name."""
    return frozenset(
        _ordered(named[left], named[right])
        for left, right in (contradiction.pair for contradiction in found)
        if left in named and right in named
    )


def _ordered(left: str, right: str) -> Pair:
    """One pair, one identity. `contradicts` is symmetric in both vocabularies."""
    return (left, right) if left <= right else (right, left)
