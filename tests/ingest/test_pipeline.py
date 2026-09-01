"""Ingestion end to end, over the corpus the generator writes.

The unit tests elsewhere hold each stage to its own contract. This holds the
sequence to the one claim that spans all of them: what ends up in the store
resolves against what is in the store. Every span is read back out of SQLite
and re-verified against the document read back out of SQLite -- not against
the objects that were written, which would only prove the pipeline agrees with
itself.

The corpus is the real generated one rather than a hand-written fixture,
because the two would drift and the one that drifted would be this file.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from praxis.config.settings import Settings
from praxis.corpus.generator import Controls, generate_corpus
from praxis.domain.enums import RecordKind
from praxis.domain.records import Document, Span
from praxis.domain.spans import SpanDefect, verify_span
from praxis.ingest.adapters import MARKDOWN_ADAPTER
from praxis.ingest.pipeline import PIPELINE_ACTOR, IngestionPipeline
from praxis.ingest.segmenter import SEGMENTER_NAME
from praxis.ingest.verifier import SpanRejection, VerificationReport, VerifierAgent
from praxis.llm.errors import ProviderUnavailableError
from praxis.llm.mock import MockProvider
from praxis.llm.provider import LLMProvider
from praxis.llm.trace import MemoryTraceSink
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository
from praxis.store.traces import SqliteTraceSink, trace_count

AT = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "corpus"
    # revisions=0 so the fixture means what it says. Revision notes are a
    # second pass in the generator and this file is about ingestion, not
    # about what the corpus contains.
    # Phase 10's control passes are switched off for the same reason.
    generate_corpus(
        root,
        documents=6,
        revisions=0,
        controls=Controls(clean=0, adversarial=0, orphans=0),
        seed=20260809,
        generated_at=AT,
    )
    return root / "documents"


def pipeline(store: Repository, settings: Settings, **kwargs) -> IngestionPipeline:
    provider = MockProvider(sink=SqliteTraceSink(store.connection), settings=settings)
    return IngestionPipeline(store, provider, **kwargs)


def source(text: str, uri: str = "notes.md"):
    return MARKDOWN_ADAPTER.normalise(text.encode("utf-8"), source_uri=uri)


# -- the whole sequence -------------------------------------------------------


def test_a_corpus_arrives_as_documents_and_spans(store, settings, corpus):
    run = pipeline(store, settings).ingest_directory(corpus, at=AT)

    assert run.ok
    assert run.documents_written == 6
    assert run.spans_written > 6
    assert run.spans_rejected == 0
    assert run.failed == ()
    assert store.stats().records[RecordKind.DOCUMENT] == 6
    assert store.stats().records[RecordKind.SPAN] == run.spans_written


def test_every_stored_span_resolves_against_the_stored_document(store, settings, corpus):
    """The claim the whole phase exists to make, asserted on what came *out*
    of SQLite rather than on what went in -- the latter would only prove the
    pipeline agrees with itself."""
    pipeline(store, settings).ingest_directory(corpus, at=AT)

    documents = {document.id: document for document in store.list_all(Document)}
    spans = store.list_all(Span)

    assert spans
    for span in spans:
        assert span.doc_id in documents
        assert verify_span(span, documents[span.doc_id]).ok


def test_every_write_left_exactly_one_audit_event(store, settings, corpus):
    run = pipeline(store, settings).ingest_directory(corpus, at=AT)
    stats = store.stats()

    assert stats.audit_events == run.documents_written + run.spans_written
    assert stats.versions == stats.audit_events


def test_the_audit_trail_names_the_pipeline_and_the_records_name_their_agent(
    store, settings, corpus
):
    """Two different questions: who ran the write, and what produced the
    record. The trail answers the first, `created_by` the second."""
    pipeline(store, settings).ingest_directory(corpus, at=AT)
    span = store.list_all(Span)[0]

    assert span.created_by == SEGMENTER_NAME
    assert all(event.actor == PIPELINE_ACTOR for event in store.audit_for(span.id))


def test_every_model_call_is_traced_into_the_store(store, settings, corpus):
    """The trace layer, wired through the seam rather than by the pipeline:
    a run can be accounted for afterwards without having been watched."""
    run = pipeline(store, settings).ingest_directory(corpus, at=AT)

    assert trace_count(store.connection) == run.calls == 6


def test_the_document_is_written_before_any_span_cites_it(store, settings):
    """Not an optimisation. A span whose document is not there yet cannot be
    verified, and the store's foreign keys would refuse it anyway."""
    result = pipeline(store, settings).ingest_source(
        source("# Title\n\nA decision was made here.\n"), at=AT
    )

    for span in result.spans:
        assert store.get(Document, span.doc_id) is not None


# -- ingesting the same thing twice -------------------------------------------


def test_a_second_run_over_one_corpus_writes_nothing(store, settings, corpus):
    """Otherwise a corpus ingested twice is counted twice in every metric
    computed over it."""
    first = pipeline(store, settings).ingest_directory(corpus, at=AT)
    second = pipeline(store, settings).ingest_directory(corpus, at=AT)

    assert second.documents_written == 0
    assert second.spans_written == 0
    assert all(result.already_present for result in second.ingested)
    assert store.stats().records[RecordKind.SPAN] == first.spans_written


def test_the_same_content_under_another_name_is_one_document(store, settings, tmp_path):
    """Recognition is by content, not by path: the same file copied twice is
    one document, and every span cut from it keeps one id."""
    body = "# Title\n\nWe chose SQLite over Postgres.\n"
    first, second = tmp_path / "a.md", tmp_path / "b.md"
    first.write_text(body, encoding="utf-8")
    second.write_text(body, encoding="utf-8")

    run = pipeline(store, settings).ingest_paths([first, second], at=AT)

    assert run.documents_written == 1
    assert [result.already_present for result in run.ingested] == [False, True]
    assert run.ingested[1].document.id == run.ingested[0].document.id


def test_a_recognised_document_reports_no_new_spans(store, settings):
    first = pipeline(store, settings).ingest_source(source("# One\n\nA decision.\n"), at=AT)
    again = pipeline(store, settings).ingest_source(source("# One\n\nA decision.\n"), at=AT)

    assert first.spans
    assert again.spans == ()
    assert again.document.id == first.document.id


# -- when a source is bad -----------------------------------------------------


def test_one_unreadable_source_does_not_stop_the_run(store, settings, tmp_path):
    """A run that died on the third file of two hundred would have said
    nothing about the other hundred and ninety-seven."""
    good = tmp_path / "good.md"
    good.write_text("# Fine\n\nA decision was made.\n", encoding="utf-8")
    broken = tmp_path / "broken.md"
    broken.write_bytes(b"not utf-8: \xff\xfe")
    unsupported = tmp_path / "report.pdf"
    unsupported.write_bytes(b"%PDF-1.4")

    run = pipeline(store, settings).ingest_paths([broken, good, unsupported], at=AT)

    assert run.documents_written == 1
    assert {failure.source_uri for failure in run.failed} == {
        broken.as_posix(),
        unsupported.as_posix(),
    }
    assert not run.ok


def test_a_missing_file_is_reported_rather_than_raised(store, settings, tmp_path):
    run = pipeline(store, settings).ingest_paths([tmp_path / "absent.md"], at=AT)

    assert len(run.failed) == 1
    assert store.stats().records.get(RecordKind.DOCUMENT, 0) == 0


def test_a_failure_about_the_run_stops_it(store, settings, tmp_path):
    """The cost ceiling and a dead network are not facts about one document,
    and swallowing them would produce a corpus that quietly stopped being
    segmented."""
    path = tmp_path / "one.md"
    path.write_text("# Title\n\nA decision was made.\n", encoding="utf-8")

    with pytest.raises(ProviderUnavailableError):
        IngestionPipeline(
            store, _Unavailable(sink=MemoryTraceSink(), settings=settings)
        ).ingest_paths([path], at=AT)


class _Unavailable(LLMProvider):
    """A provider whose failure is about the run rather than the document."""

    name = MockProvider.name
    bills = False

    def _invoke(self, request, spec):
        message = "no network"
        raise ProviderUnavailableError(message)


# -- when a citation is refused -----------------------------------------------


class _RefusesEverything(VerifierAgent):
    """Stands in for a verifier that finds something wrong with every span.

    The pipeline cannot produce a bad span on purpose -- the segmenter answers
    in block numbers and the offsets are read off the grid -- so the only
    honest way to test what happens when one is refused is to refuse them.
    """

    def verify_against(self, spans, source):
        return VerificationReport(
            accepted=(),
            rejected=tuple(
                SpanRejection(
                    span_id=span.id,
                    doc_id=span.doc_id,
                    defect=SpanDefect.TEXT_MISMATCH,
                    detail="refused by the test",
                )
                for span in spans
            ),
        )


def test_a_refused_citation_is_reported_and_never_stored(store, settings):
    result = pipeline(store, settings, verifier=_RefusesEverything()).ingest_source(
        source("# Title\n\nA decision was made here.\n\nAnd another one.\n"), at=AT
    )

    assert result.spans == ()
    assert result.rejected
    assert store.stats().records.get(RecordKind.SPAN, 0) == 0
    assert store.stats().records[RecordKind.DOCUMENT] == 1


def test_a_run_with_a_refused_citation_is_not_ok(store, settings, tmp_path):
    path = tmp_path / "one.md"
    path.write_text("# Title\n\nA decision was made.\n", encoding="utf-8")

    run = pipeline(store, settings, verifier=_RefusesEverything()).ingest_paths([path], at=AT)

    assert run.spans_rejected > 0
    assert not run.ok
    assert run.failed == ()


# -- degradation ---------------------------------------------------------------


def test_a_document_the_model_could_not_segment_is_still_ingested(store, settings, tmp_path):
    """The floor doing its job through the whole pipeline: a useless answer
    costs segmentation quality and never the document."""
    path = tmp_path / "one.md"
    path.write_text("# Title\n\nA decision.\n\nAnd another paragraph.\n", encoding="utf-8")
    provider = MockProvider(
        sink=SqliteTraceSink(store.connection), settings=settings, malformed_share=1.0
    )

    run = IngestionPipeline(store, provider).ingest_paths([path], at=AT)

    assert run.documents_written == 1
    assert run.spans_written == len(run.ingested[0].blocks)
    assert run.ingested[0].degraded


# -- order ---------------------------------------------------------------------


def test_a_directory_is_walked_in_a_stable_order(store, settings, corpus, tmp_path):
    """Document ids are sequential, so the order sources arrive in is the
    order they are numbered in -- and filesystem order is not the same on two
    machines."""
    first = pipeline(store, settings).ingest_directory(corpus, at=AT)

    second_store = Repository(connect(MEMORY))
    migrate(second_store.connection)
    second = pipeline(second_store, settings).ingest_directory(corpus, at=AT)
    second_store.close()

    assert [result.document.id for result in first.ingested] == [
        result.document.id for result in second.ingested
    ]
    assert [result.document.source_uri for result in first.ingested] == [
        result.document.source_uri for result in second.ingested
    ]
