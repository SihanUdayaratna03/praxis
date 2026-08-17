"""Bytes to stored spans: adapter, segmenter, verifier, store, in that order.

The order is the argument. Normalisation decides what an offset means, the
segmenter chooses boundaries and never offsets, the verifier re-reads every
span against the document, and only then does anything reach the store --
which is `ARCHITECTURE.md`'s sentence "nothing enters the store without passing
`VerifierAgent`" as a call sequence rather than a claim.

Four decisions worth stating, because each one is a thing that could have been
done the easy way:

- **A rejected span is not stored, and not silently dropped either.** It comes
  back in the result with the defect and the evidence. Nothing writes a
  `Finding` yet -- `FindingKind` has no member for a fabricated citation and
  the SQL `CHECK` constraints mirror that enum, so inventing one would mean a
  migration for a record nothing reads. It is in `BACKLOG.md` with that reason.
- **A document already in the store is recognised, not re-written.** Recognised
  by the hash of its normalised content, so a file that arrives twice under two
  names is one document. Writing a second copy would fork every span cut from
  it and double-count the corpus in every metric computed over it.
- **The document is written before its spans.** A span whose document is not
  there yet cannot be verified, and the store's foreign keys would refuse it
  anyway. The order is not an optimisation, it is the only one that works.
- **One failed source does not stop a run.** `ingest_paths` catches what is
  wrong with *one document* and carries on, because a corpus run that dies on
  the third file of two hundred has said nothing about the other hundred and
  ninety-seven.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from praxis.domain.enums import RecordKind
from praxis.domain.ids import DocumentId
from praxis.domain.records import Document, Span
from praxis.ingest.adapters import ADAPTER_NAME, NormalisedSource, document_from, read_source
from praxis.ingest.blocks import Block
from praxis.ingest.errors import IngestionError
from praxis.ingest.segmenter import SegmenterAgent
from praxis.ingest.verifier import SpanRejection, VerifierAgent
from praxis.llm.provider import LLMProvider
from praxis.obs.logging import get_logger
from praxis.store.repository import Repository

_log = get_logger(__name__)

PIPELINE_ACTOR: Final = "ingestion"
"""The actor on the audit rows this pipeline writes.

Not `SourceAdapter` or `SegmenterAgent`: the audit trail records *who wrote a
record*, and what wrote these is the pipeline running both. The records
themselves carry the agent that produced them in `created_by`.
"""


@dataclass(frozen=True, slots=True)
class Ingested:
    """What one source produced.

    Attributes:
        document: The document, whether written now or recognised from before.
        spans: The spans written now. Empty for a document already ingested.
        blocks: The grid the spans were cut from.
        rejected: Spans the verifier refused. Never stored.
        already_present: Whether the store already held this exact content.
        degraded: Whether any window fell back to one span per block.
        calls: Model calls made, repairs included.
    """

    document: Document
    spans: tuple[Span, ...] = ()
    blocks: tuple[Block, ...] = ()
    rejected: tuple[SpanRejection, ...] = ()
    already_present: bool = False
    degraded: bool = False
    calls: int = 0


@dataclass(frozen=True, slots=True)
class Failed:
    """One source that could not be ingested, and why."""

    source_uri: str
    reason: str


@dataclass(frozen=True, slots=True)
class IngestionRun:
    """Everything one run of the pipeline did.

    Reported rather than logged, because the numbers here are the ones Phase 10
    charts and the ones a person needs after ingesting a directory: how much
    arrived, how much was already there, how much was refused.
    """

    ingested: tuple[Ingested, ...] = ()
    failed: tuple[Failed, ...] = ()

    @property
    def documents_written(self) -> int:
        """Documents this run added to the store."""
        return sum(1 for result in self.ingested if not result.already_present)

    @property
    def spans_written(self) -> int:
        """Spans this run added to the store."""
        return sum(len(result.spans) for result in self.ingested)

    @property
    def spans_rejected(self) -> int:
        """Citations the verifier refused, and therefore did not store."""
        return sum(len(result.rejected) for result in self.ingested)

    @property
    def calls(self) -> int:
        """Model calls made across the run, repairs included."""
        return sum(result.calls for result in self.ingested)

    @property
    def ok(self) -> bool:
        """Whether every source was ingested and every span survived."""
        return not self.failed and self.spans_rejected == 0


@dataclass
class _StoreDocuments:
    """A `DocumentSource` reading through the repository.

    The verifier resolves a span's document rather than being handed one, which
    is what lets it refuse a citation of something nothing ever ingested. Here
    that resolution is a store read, and it is cached for the run because a
    document is immutable within one.
    """

    repository: Repository
    seen: dict[DocumentId, Document | None] = field(default_factory=dict)

    def document_for(self, doc_id: DocumentId) -> Document | None:
        """Return the current version of a document, or `None` if unknown."""
        if doc_id not in self.seen:
            self.seen[doc_id] = self.repository.get(Document, doc_id)
        return self.seen[doc_id]


class IngestionPipeline:
    """Runs one source, or a directory of them, from bytes to stored spans."""

    def __init__(
        self,
        repository: Repository,
        provider: LLMProvider,
        *,
        segmenter: SegmenterAgent | None = None,
        verifier: VerifierAgent | None = None,
    ) -> None:
        """Wire the pipeline to a store and a provider it did not choose.

        Args:
            repository: An open, migrated store.
            provider: The seam, from `provider_for`. Passed through to the
                segmenter, which is the only stage that calls a model.
            segmenter: Supplied only to vary its window size or attempt budget.
            verifier: Supplied only by a test. There is one behaviour and it is
                deterministic, which is the whole point of the agent.
        """
        self._repository = repository
        self._segmenter = segmenter if segmenter is not None else SegmenterAgent(provider)
        self._verifier = verifier if verifier is not None else VerifierAgent()
        self._documents = _StoreDocuments(repository)

    def ingest_path(self, path: Path, *, at: datetime | None = None) -> Ingested:
        """Ingest one file.

        Raises:
            IngestionError: if the file cannot be normalised.
            OSError: if it cannot be read.
            ProviderError: for a model failure about the run rather than about
                this document.
            StoreError: if the write fails.
        """
        return self.ingest_source(read_source(path), at=at)

    def ingest_source(self, source: NormalisedSource, *, at: datetime | None = None) -> Ingested:
        """Ingest a source that has already been normalised.

        The entry point a caller with bytes rather than a file uses, and the
        one `ingest_path` is written in terms of.

        Args:
            source: Normalised text and where it came from.
            at: When this ran. Defaults to now, in UTC.

        Returns:
            What was written, or what was recognised.
        """
        moment = at if at is not None else datetime.now(UTC)
        raw = source.text.encode("utf-8")
        existing = self._repository.document_with_content(_hash_of(raw))
        if existing is not None:
            return Ingested(
                document=self._repository.require(Document, existing), already_present=True
            )

        document = self._store_document(source, at=moment)
        segmentation = self._segmenter.segment(document, at=moment)
        report = self._verifier.verify_against(segmentation.spans, self._documents)
        for span in report.accepted:
            self._repository.add(
                span,
                actor=PIPELINE_ACTOR,
                reason=f"segmented {document.id} from {source.source_uri}",
                at=moment,
            )
        _log_rejections(document, report.rejected)
        return Ingested(
            document=document,
            spans=report.accepted,
            blocks=segmentation.blocks,
            rejected=report.rejected,
            already_present=False,
            degraded=segmentation.degraded,
            calls=segmentation.calls,
        )

    def ingest_paths(self, paths: Iterable[Path], *, at: datetime | None = None) -> IngestionRun:
        """Ingest many files, carrying on past the ones that cannot be.

        A run that died on the third file of two hundred would have said
        nothing about the other hundred and ninety-seven, so a source that
        fails is recorded and the run continues. A failure about the *run* --
        the cost ceiling, a store that will not write -- is not caught here and
        stops everything, which is the correct difference.
        """
        ingested: list[Ingested] = []
        failed: list[Failed] = []
        for path in paths:
            try:
                ingested.append(self.ingest_path(path, at=at))
            except (IngestionError, OSError) as exc:
                failed.append(Failed(source_uri=path.as_posix(), reason=str(exc)))
                _log.warning("ingestion_failed", source=path.as_posix(), reason=str(exc))
        run = IngestionRun(ingested=tuple(ingested), failed=tuple(failed))
        _log.info(
            "ingestion_run",
            documents=run.documents_written,
            recognised=len(run.ingested) - run.documents_written,
            spans=run.spans_written,
            rejected=run.spans_rejected,
            failed=len(run.failed),
            calls=run.calls,
        )
        return run

    def ingest_directory(self, root: Path, *, at: datetime | None = None) -> IngestionRun:
        """Ingest every file under a directory, in a stable order.

        Sorted rather than in filesystem order, so two runs over one directory
        allocate the same document ids on two machines -- ids are sequential,
        so the order files arrive in is the order they are numbered in.
        """
        return self.ingest_paths(sorted(path for path in root.rglob("*") if path.is_file()), at=at)

    def _store_document(self, source: NormalisedSource, *, at: datetime) -> Document:
        """Allocate an id and write the document, before any span cites it."""
        document = document_from(
            source,
            doc_id=DocumentId(self._repository.next_id(RecordKind.DOCUMENT)),
            ingested_at=at,
            created_by=ADAPTER_NAME,
        )
        return self._repository.add(
            document,
            actor=PIPELINE_ACTOR,
            reason=f"ingested {source.source_uri}",
            at=at,
        )


def _log_rejections(document: Document, rejected: Sequence[SpanRejection]) -> None:
    """Report refused citations one line each.

    Warning rather than info: a span this pipeline produced should never fail
    verification -- the segmenter answers in block numbers and the offsets are
    read off the grid -- so one that does is either a bug here or a document
    that changed underneath a run, and both are worth waking up to.
    """
    for rejection in rejected:
        _log.warning(
            "span_rejected",
            doc_id=document.id,
            span_id=rejection.span_id,
            defect=rejection.defect.value,
            detail=rejection.detail,
        )


def _hash_of(raw: bytes) -> str:
    """The content hash a document would carry, before one has been built.

    Spelled here rather than by constructing a throwaway `Document`, because
    building one needs an id and allocating an id is the thing the lookup
    exists to avoid doing twice.
    """
    return hashlib.sha256(raw).hexdigest()
