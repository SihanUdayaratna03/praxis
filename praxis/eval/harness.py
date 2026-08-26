"""Running a corpus end to end and grading what came out.

The join everything else avoids having to know about: `praxis.corpus` writes
documents and an answer key, `praxis.ingest` turns the documents into spans,
`praxis.agents` turns the spans into records, and this reads the records back
out of the store, reduces them to `Claim`s and hands them to `matching`.

Two decisions worth stating.

**The claims are read back out of SQLite, not taken from the run's result.**
The run's result is a report of what the agents produced; the store is what
survived being written. Grading the first would grade the pipeline's opinion of
itself, and a record refused by a foreign key would score as a hit.

**A `Claim`'s coordinates come from the span it cites, not from the record.** A
record carries a `span_id` and nothing else about where it was read from, which
is the whole point of invariant 6 -- so the join to the answer key runs through
the span, and a record citing a span the store does not hold is not graded as
anything. It cannot exist: the store's foreign keys refuse it. It is worth
saying because that is the property doing the work here, rather than a check
this module performs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from praxis.agents.detection import DetectionRun, detect_in_store
from praxis.agents.extraction import ExtractionPipeline
from praxis.agents.formalization import formalize_store
from praxis.agents.results import ExtractionRun
from praxis.corpus.groundtruth import (
    DOCUMENTS_DIRNAME,
    CorpusGroundTruth,
    ItemKind,
    load_ground_truth,
)
from praxis.domain.ids import DocumentId, SpanId
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Decision, Document, Estimate, Link, Span
from praxis.eval.matching import SET_SEPARATOR, Claim, Pairing, fusion_pairs, pair
from praxis.eval.memory import MemoryResult, grade_memory
from praxis.eval.metrics import (
    CitationIntegrity,
    Score,
    citation_integrity,
    cost_per_document,
    exact_matches,
    fusion_recall,
    score,
)
from praxis.ingest.pipeline import IngestionPipeline
from praxis.llm.provider import LLMProvider
from praxis.monitor.facts import FactsFile
from praxis.monitor.run import monitor_store
from praxis.obs.logging import get_logger
from praxis.store.repository import Repository
from praxis.store.traces import cost_by_agent

_log = get_logger(__name__)

GRADED_KINDS: tuple[ItemKind, ...] = (ItemKind.DECISION, ItemKind.ASSUMPTION, ItemKind.ESTIMATE)
"""What Half A can produce. `OUTCOME` is Half B's and is graded from Phase 6;
listing it here with a permanent zero would read as a regression rather than as
work that has not started."""


@dataclass(frozen=True, slots=True)
class KindResult:
    """One kind's pairing and the numbers over it."""

    pairing: Pairing
    score: Score
    exact: int

    @property
    def kind(self) -> ItemKind:
        """What was being extracted."""
        return self.score.kind


@dataclass(frozen=True, slots=True)
class EvalResult:
    """Everything one graded run knows.

    Attributes:
        run: What the extraction produced, refusals included.
        kinds: One result per graded kind, in `GRADED_KINDS` order.
        citations: How honest the citations were, and how they failed.
        fusion_found: `estimated_as` edges written.
        fusion_expected: `estimated_as` edges the corpus labels.
        documents: How many documents were graded.
        memory: What the formalization, monitoring and detection passes
            concluded. Present whether or not they ran: a store nothing
            formalized reports a parse rate over the predicates it does hold,
            which is a true statement about that store.
        cost: What each agent spent per document, from the trace table.
    """

    run: ExtractionRun
    kinds: tuple[KindResult, ...]
    citations: CitationIntegrity
    fusion_found: int
    fusion_expected: int
    documents: int
    memory: MemoryResult = field(default_factory=MemoryResult)
    cost: Mapping[str, Decimal] = field(default_factory=dict)

    @property
    def fusion_recall(self) -> Decimal:
        """Of the labelled fusion edges, how many were found."""
        return fusion_recall(self.fusion_found, self.fusion_expected)

    def for_kind(self, kind: ItemKind) -> KindResult | None:
        """One kind's result, or `None` if it was not graded."""
        return next((result for result in self.kinds if result.kind is kind), None)


def grade(
    repository: Repository,
    truth: CorpusGroundTruth,
    run: ExtractionRun,
    *,
    detection: DetectionRun | None = None,
    run_id: str | None = None,
) -> EvalResult:
    """Grade what a store holds against the corpus's answer key.

    Args:
        repository: The store the run wrote into.
        truth: The answer key beside the documents that were ingested.
        run: What the extraction reported, for the refusal counts. Nothing is
            graded from it -- the records come out of the store.
        detection: What the detection run reported, for the settlement split
            and what blocking proposed. Neither is written anywhere, which is
            why they are the two things taken from a run rather than a store.
        run_id: The run whose trace rows the cost column totals. Without one
            there is no cost to report -- the trace table is keyed by run, and
            summing every row would total every run this store ever held.

    Returns:
        Every number the report prints.
    """
    claims = claims_in(repository)
    kinds = tuple(_graded(claims, truth, kind) for kind in GRADED_KINDS)
    documents = len(truth.documents)
    return EvalResult(
        run=run,
        kinds=kinds,
        citations=citation_integrity(run.refused, offered=len(claims)),
        fusion_found=sum(
            1 for link in repository.list_all(Link) if link.link_type is LinkType.ESTIMATED_AS
        ),
        fusion_expected=len(fusion_pairs(truth)),
        documents=documents,
        memory=grade_memory(
            repository, truth, _pairing_for(kinds, ItemKind.ASSUMPTION), detection=detection
        ),
        cost=_cost(repository, documents, run_id),
    )


def evaluate(
    repository: Repository,
    provider: LLMProvider,
    corpus: Path,
    *,
    at: datetime | None = None,
    run_id: str | None = None,
) -> EvalResult:
    """Ingest a corpus, extract from it, and grade the result.

    The whole harness in one call, and the entry point `praxis eval` uses. The
    store is passed in rather than opened here so that a caller can keep it and
    look at what was written, which is what makes a failing number debuggable.

    Args:
        repository: An open, migrated store to ingest and extract into.
        provider: The seam, from `provider_for`. Every agent uses this one.
        corpus: The corpus root -- the directory holding `documents/` and the
            answer key beside it.
        at: When this ran. Defaults to now, in UTC, per stage.
        run_id: Recorded on every audit row this writes.

    Returns:
        The graded result.
    """
    truth = load_ground_truth(corpus)
    ingestion = IngestionPipeline(repository, provider).ingest_directory(
        corpus / DOCUMENTS_DIRNAME, at=at
    )
    run = ExtractionPipeline(repository, provider).extract_store(at=at, run_id=run_id)
    memory = _remember(repository, provider, truth, at=at, run_id=run_id)
    _log.info(
        "eval_run",
        documents=len(ingestion.ingested),
        spans=ingestion.spans_written,
        decisions=run.decisions,
        assumptions=run.assumptions,
        estimates=run.estimates,
        calls=ingestion.calls + run.calls + memory.calls,
    )
    return grade(repository, truth, run, detection=memory.detection, run_id=run_id)


@dataclass(frozen=True, slots=True)
class MemoryPasses:
    """What the three passes after extraction did, before anything is graded.

    Attributes:
        formalized: Assumptions compiled into checkable predicates.
        revised: Assumptions whose status the monitor changed.
        detection: The detection run, kept because the settlement split and
            what blocking proposed are the two facts no store holds.
        calls: Model calls the three passes made between them.
    """

    formalized: int
    revised: int
    detection: DetectionRun
    calls: int


def _remember(
    repository: Repository,
    provider: LLMProvider,
    truth: CorpusGroundTruth,
    *,
    at: datetime | None,
    run_id: str | None,
) -> MemoryPasses:
    """Compile, evaluate and compare, in that order and for that reason.

    Formalization runs first because the monitor can only reach a verdict about
    a predicate that parses, and detection runs last because the interval
    arithmetic reads the same compiled predicates and blocking buckets records
    by the quantities they constrain. Running them in any other order would
    measure the order rather than the agents.

    The monitor is given the corpus's own world -- the measurements and
    observations the answer key states -- because a verdict is only gradeable
    against the facts the key computed it from.
    """
    formalization = formalize_store(repository, provider, at=at, run_id=run_id)
    monitoring = monitor_store(
        repository,
        provider=provider,
        supplied=FactsFile(
            facts=truth.monitoring.facts,
            events=truth.monitoring.events,
            as_of=truth.monitoring.as_of,
        ),
        at=at,
        run_id=run_id,
    )
    detection = detect_in_store(repository, provider=provider, at=at, run_id=run_id)
    return MemoryPasses(
        formalized=len(formalization.checkable),
        revised=monitoring.revised,
        detection=detection,
        calls=formalization.calls + monitoring.calls + detection.result.calls,
    )


def claims_in(repository: Repository) -> tuple[Claim, ...]:
    """Reduce everything Half A stored to the view grading needs.

    Read back out of SQLite rather than taken from the run, so that what is
    graded is what survived being written.

    The field names are the answer key's, not the records': `praxis.corpus`
    writes what it expects under `ExpectedField.name`, and this is the one place
    the two vocabularies meet. Anywhere else would be two places.
    """
    where = _Where(
        spans={span.id: span for span in repository.list_all(Span)},
        hashes={document.id: document.content_hash for document in repository.list_all(Document)},
    )
    return (
        *(
            where.claim(
                record.id,
                ItemKind.DECISION,
                record.span_id,
                {
                    "chosen": record.chosen,
                    "rejected": SET_SEPARATOR.join(option.option for option in record.rejected),
                    "decision_maker": record.decision_maker,
                    "title": record.title,
                },
            )
            for record in repository.list_all(Decision)
        ),
        *(
            where.claim(
                record.id,
                ItemKind.ASSUMPTION,
                record.span_id,
                {
                    "statement": record.statement,
                    "predicate": record.predicate,
                    "expiry_condition": record.expiry_condition,
                },
            )
            for record in repository.list_all(Assumption)
        ),
        *(
            where.claim(
                record.id,
                ItemKind.ESTIMATE,
                record.span_id,
                {
                    "active_quantity": str(record.active_quantity),
                    "unit": record.unit.value,
                    "owner": record.owner,
                    "work_class": record.work_class,
                },
            )
            for record in repository.list_all(Estimate)
        ),
    )


@dataclass(frozen=True, slots=True)
class _Where:
    """Everything needed to place a record in the answer key's coordinates.

    A record carries a `span_id` and nothing else about where it was read from,
    which is invariant 6 working as intended -- so every claim's position is
    resolved through the span, and the document's content hash is what joins it
    to `DocumentGroundTruth.content_sha256`.
    """

    spans: Mapping[SpanId, Span]
    hashes: Mapping[DocumentId, str]

    def claim(
        self, record_id: str, kind: ItemKind, span_id: SpanId, fields: Mapping[str, str]
    ) -> Claim:
        """One claim, placed."""
        span = self.spans[span_id]
        return Claim(
            record_id=record_id,
            kind=kind,
            content_hash=self.hashes[span.doc_id],
            start_byte=span.start_byte,
            end_byte=span.end_byte,
            fields=fields,
        )


def _graded(claims: tuple[Claim, ...], truth: CorpusGroundTruth, kind: ItemKind) -> KindResult:
    """Pair and count one kind."""
    pairing = pair(claims, truth, kind=kind)
    return KindResult(
        pairing=pairing, score=score(pairing, kind), exact=exact_matches(pairing.matched)
    )


def _pairing_for(kinds: tuple[KindResult, ...], kind: ItemKind) -> Pairing:
    """One kind's pairing, reused rather than recomputed.

    `praxis.eval.memory` joins stored records to answer-key items through the
    assumption pairing, and pairing twice would be two chances to pair one
    record differently -- the greedy walk is deterministic, but two callers
    agreeing by construction beats two callers agreeing by argument.
    """
    return next(result.pairing for result in kinds if result.kind is kind)


def _cost(repository: Repository, documents: int, run_id: str | None) -> dict[str, Decimal]:
    """What each agent spent per document, or nothing when there is no run to total."""
    if run_id is None:
        return {}
    return cost_per_document(cost_by_agent(repository.connection, run_id), documents)
