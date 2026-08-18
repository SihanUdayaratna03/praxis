"""Stored spans to a graph: scout, structurer, extractor, store, in that order.

Phase 3's `IngestionPipeline` stops at spans. This is what turns them into the
records the product is about, and it is deliberately the same shape: a hand
rolled sequence with the stages in the only order that works, reported rather
than logged, degrading about one document rather than about the run. ADR 0004's
orchestrator replaces the *wiring* later; the stages and their order are not
what it changes.

The order is the argument again. The scout is generous and cheap because it
runs once per span over the whole corpus; the structurer is careful and mid
tier because it runs once per candidate, and the scout's low precision is what
keeps that number small; the extractor reasons and is the most expensive of the
three, so it runs once per *verified decision* rather than once per candidate.
Each stage is paid for by the one before it filtering.

Four decisions worth stating.

**Nothing is written until it has been verified, and each agent verifies its
own output.** This pipeline never re-checks a citation, because a record that
reached it has already passed `praxis.agents.citation`. What it does do is
write in dependency order -- a decision before the edge that cites it, an
assumption before the edge saying a decision rests on it -- which is the store's
foreign keys as a call sequence rather than a claim.

**Ids come from a counter seeded off the store, not from `next_id` per record.**
`Repository.next_id` reads the highest ordinal, so asking it twice before
writing anything returns one id twice, and one call to the extractor can
produce three assumptions. The allocator here asks the store once per kind and
counts from there.

**A document that already has decisions is skipped, not extracted again.**
Sequential ids mean a second run would write a second `D-…` for the same
passage, and content-addressed `Link` ids mean the edges would collide outright.
Recognising the work as done is the same move `IngestionPipeline` makes for a
document it has already seen.

**One bad document does not stop a run.** Same rule, same reason, and the same
difference: a failure about the *run* -- the cost ceiling, a store that will
not write -- is not caught here and stops everything.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Final

from praxis.agents.errors import ExtractionError
from praxis.agents.extractor import (
    AssumptionExtractor,
    ExtractedAssumption,
    ExtractionRejection,
)
from praxis.agents.results import (
    DocumentExtraction,
    ExtractionRun,
    Refused,
    Stage,
)
from praxis.agents.scout import DecisionScout
from praxis.agents.structurer import DecisionStructurer, StructuredDecision, StructureRejection
from praxis.domain.base import VersionedRecord
from praxis.domain.enums import RecordKind
from praxis.domain.ids import DocumentId, format_sequential_id, ordinal_of
from praxis.domain.records import Decision, Document, Span
from praxis.llm.provider import LLMProvider
from praxis.obs.logging import get_logger
from praxis.store.repository import Repository

_log = get_logger(__name__)

EXTRACTION_ACTOR: Final = "extraction"
"""The actor on the audit rows this pipeline writes.

Not the agent that produced the record: the audit trail records who *wrote* it,
and what wrote these is the pipeline running all three. Each record carries the
agent that produced it in `created_by`, so both questions have an answer and
neither is inferred from the other.
"""


class _Allocator:
    """Sequential ids from a counter seeded off the store, once per kind.

    `Repository.next_id` reads the highest ordinal in the store, which is right
    for a caller that writes between calls and wrong for one that does not --
    and the extractor builds every assumption in an answer before any of them is
    written. Asking the store once and counting from there is the same sequence
    the store would have produced, arrived at without a read per record.
    """

    def __init__(self, repository: Repository) -> None:
        """Seed nothing; each kind is asked about the first time it is used."""
        self._repository = repository
        self._next: dict[RecordKind, int] = {}

    def __call__(self, kind: RecordKind) -> str:
        """Allocate the next id of a kind."""
        if kind not in self._next:
            self._next[kind] = ordinal_of(self._repository.next_id(kind))
        ordinal = self._next[kind]
        self._next[kind] = ordinal + 1
        return format_sequential_id(kind, ordinal)


class ExtractionPipeline:
    """Runs one document, or a whole store, from spans to a graph."""

    def __init__(
        self,
        repository: Repository,
        provider: LLMProvider,
        *,
        scout: DecisionScout | None = None,
        structurer: DecisionStructurer | None = None,
        extractor: AssumptionExtractor | None = None,
    ) -> None:
        """Wire the pipeline to a store and a provider it did not choose.

        Args:
            repository: An open, migrated store holding documents and spans.
            provider: The seam, from `provider_for`. Every stage calls a model,
                and none of them chose which.
            scout: Supplied only to vary its window size or attempt budget.
            structurer: Likewise.
            extractor: Likewise.
        """
        self._repository = repository
        self._scout = scout if scout is not None else DecisionScout(provider)
        self._structurer = structurer if structurer is not None else DecisionStructurer(provider)
        self._extractor = extractor if extractor is not None else AssumptionExtractor(provider)

    def extract_document(
        self,
        document: Document,
        spans: Sequence[Span],
        *,
        at: datetime | None = None,
        run_id: str | None = None,
        already_extracted: bool = False,
    ) -> DocumentExtraction:
        """Run all three agents over one document and write what survives.

        Args:
            document: The document, for re-reading every citation.
            spans: Its spans, in document order.
            at: When this ran. Defaults to now, in UTC.
            run_id: The run these writes belong to, recorded on every audit row.
            already_extracted: Set by `extract_store` for a document the store
                already holds decisions for. Nothing is read or written.

        Returns:
            What was written and what was refused.

        Raises:
            ProviderError: for a model failure about the run rather than this
                document.
            StoreError: if a write fails.
        """
        if already_extracted or not spans:
            return DocumentExtraction(document=document, already_extracted=already_extracted)
        moment = at if at is not None else datetime.now(UTC)
        allocate = _Allocator(self._repository)
        sighted = self._scout.scan(tuple(spans))
        refused = [
            Refused(
                stage=Stage.SCAN,
                refusal=rejection.refusal,
                doc_id=document.id,
                detail=rejection.detail,
                lost=RecordKind.DECISION,
                ordinal=rejection.ordinal,
            )
            for rejection in sighted.rejections
        ]
        decisions: list[StructuredDecision] = []
        assumptions: list[ExtractedAssumption] = []
        calls = sighted.calls
        for candidate in sighted.candidates:
            structured = self._structurer.structure(
                candidate,
                spans,
                document,
                decision_id=allocate(RecordKind.DECISION),
                at=moment,
            )
            calls += structured.calls
            if structured.decision is None:
                refused.append(_from_structure(structured.rejection, document.id))
                continue
            decisions.append(structured.decision)
            self._write_decision(structured.decision, at=moment, run_id=run_id)
            found = self._extractor.extract(
                structured.decision.decision, spans, document, allocate=allocate, at=moment
            )
            calls += found.calls
            refused.extend(_from_extraction(found.rejections, document.id))
            assumptions.extend(found.assumptions)
            for extracted in found.assumptions:
                self._write_assumption(extracted, at=moment, run_id=run_id)
        return DocumentExtraction(
            document=document,
            decisions=tuple(decisions),
            assumptions=tuple(assumptions),
            refused=tuple(refused),
            candidates=len(sighted.candidates),
            calls=calls,
            blind_windows=sighted.blind_windows,
        )

    def extract_store(
        self, *, at: datetime | None = None, run_id: str | None = None
    ) -> ExtractionRun:
        """Run over every document the store holds, in id order.

        In id order rather than in whatever the store returns, because ids are
        allocated in the order documents were ingested and two runs over one
        corpus have to produce the same numbers -- the property the whole
        ablation table rests on.
        """
        documents = sorted(self._repository.list_all(Document), key=lambda entry: entry.id)
        spans = _by_document(self._repository.list_all(Span))
        done = _documents_with_decisions(self._repository.list_all(Decision), spans)
        results = tuple(
            self.extract_document(
                document,
                spans.get(document.id, ()),
                at=at,
                run_id=run_id,
                already_extracted=document.id in done,
            )
            for document in documents
        )
        run = ExtractionRun(documents=results)
        _log.info(
            "extraction_run",
            documents=len(results),
            decisions=run.decisions,
            assumptions=run.assumptions,
            estimates=run.estimates,
            refused=len(run.refused),
            blind_windows=run.blind_windows,
            calls=run.calls,
        )
        return run

    def _write_decision(
        self, structured: StructuredDecision, *, at: datetime, run_id: str | None
    ) -> None:
        """Write a decision, then the edge saying what it was read from."""
        self._add(structured.decision, at=at, run_id=run_id, reason="structured from a candidate")
        self._add(
            structured.justification,
            at=at,
            run_id=run_id,
            reason=f"{structured.decision.id} was read from {structured.decision.span_id}",
        )

    def _write_assumption(
        self, extracted: ExtractedAssumption, *, at: datetime, run_id: str | None
    ) -> None:
        """Write an assumption, its edges, and the estimate hiding in it.

        In dependency order throughout: a node before any edge naming it. The
        store's foreign keys would refuse the other order, which makes this the
        only sequence that works rather than a preference.
        """
        assumption = extracted.assumption
        self._add(assumption, at=at, run_id=run_id, reason="extracted from a decision's document")
        self._add(
            extracted.justification,
            at=at,
            run_id=run_id,
            reason=f"{assumption.id} was read from {assumption.span_id}",
        )
        self._add(
            extracted.assumes,
            at=at,
            run_id=run_id,
            reason=f"{extracted.assumes.source_id} rests on {assumption.id}",
        )
        if extracted.estimate is None:
            return
        self._add(
            extracted.estimate.estimate,
            at=at,
            run_id=run_id,
            reason=f"{assumption.id} is a quantified forward-looking claim",
        )
        self._add(
            extracted.estimate.estimated_as,
            at=at,
            run_id=run_id,
            reason=f"{assumption.id} is an estimate wearing an assumption's clothes",
        )

    def _add(
        self, record: VersionedRecord, *, at: datetime, run_id: str | None, reason: str
    ) -> None:
        """One write, one audit row, one actor."""
        self._repository.add(record, actor=EXTRACTION_ACTOR, reason=reason, at=at, run_id=run_id)


def _from_structure(rejection: StructureRejection | None, doc_id: DocumentId) -> Refused:
    """Carry a structurer refusal into the shape the run reports in.

    The stage and the lost kind are supplied here rather than by the agent. A
    structurer that knew it was the second of three stages would be an agent
    that knows about the pipeline running it, and the point of the seam is that
    it does not.

    Raises:
        ExtractionError: if the result carried neither a decision nor a
            refusal. `StructureResult` promises exactly one, and a promise
            nothing checks is a comment.
    """
    if rejection is None:
        message = f"a structure result about {doc_id} carried neither a decision nor a refusal"
        raise ExtractionError(message)
    return Refused(
        stage=Stage.STRUCTURE,
        refusal=rejection.refusal,
        doc_id=doc_id,
        detail=rejection.detail,
        lost=RecordKind.DECISION,
        ordinal=rejection.ordinal,
    )


def _from_extraction(
    rejections: Iterable[ExtractionRejection], doc_id: DocumentId
) -> list[Refused]:
    """The same for the extractor, which says itself which record it lost."""
    return [
        Refused(
            stage=Stage.EXTRACT,
            refusal=rejection.refusal,
            doc_id=doc_id,
            detail=rejection.detail,
            lost=rejection.lost,
            ordinal=rejection.ordinal,
        )
        for rejection in rejections
    ]


def _by_document(spans: Iterable[Span]) -> dict[DocumentId, tuple[Span, ...]]:
    """Group every span by its document, in document order within each.

    One read rather than one per document. Sorted by start byte because the
    agents are given spans in document order and the store returns them in
    write order, which is the same thing today and is not guaranteed to be.
    """
    grouped: dict[DocumentId, list[Span]] = defaultdict(list)
    for span in spans:
        grouped[span.doc_id].append(span)
    return {
        doc_id: tuple(sorted(found, key=lambda span: span.start_byte))
        for doc_id, found in grouped.items()
    }


def _documents_with_decisions(
    decisions: Iterable[Decision], spans: dict[DocumentId, tuple[Span, ...]]
) -> frozenset[DocumentId]:
    """Which documents the store already holds decisions for.

    Resolved through the spans rather than by a query, because a `Decision`
    cites a span and not a document -- the same indirection that makes a
    decision's provenance re-readable is what makes this a join.
    """
    home = {span.id: doc_id for doc_id, found in spans.items() for span in found}
    return frozenset(home[decision.span_id] for decision in decisions if decision.span_id in home)
