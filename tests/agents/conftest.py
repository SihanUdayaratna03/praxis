"""Fixtures shared by the Half A agent tests.

Documents are built through the real adapter and cut by the real block grid, so
a span in these tests is the same object ingestion produces. Building spans by
hand would make every agent test pass against coordinates the pipeline never
emits, which is the failure mode `tests/ingest/test_verifier.py` exists to
cover from the other side.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from praxis.agents.offering import Offering, offering_of
from praxis.domain.ids import DocumentId
from praxis.domain.records import Document, Span
from praxis.ingest.adapters import MARKDOWN_ADAPTER, document_from
from praxis.ingest.blocks import blocks_in

AT = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)

ADR_BODY = """\
# ADR: the product search index

Status: accepted. Decided 2026-01-05 by the Discovery team.

## Decision

We are going with OpenSearch on managed nodes, rather than Postgres full-text
search or a hosted search vendor. Decided by Nadeesha for the Discovery team.

## Alternatives considered

- Postgres full-text search — ranking quality was not close on our own queries.
- A hosted search vendor — the per-query price does not survive our growth.

## Assumptions

- This rests on the assumption that the index stays under 50 GB for the next
  year. In predicate form: `index_size_gb <= 50`. Re-check
  when(indexed_documents >= 10000000).
- It also rests on the work finishing inside 4 weeks. In predicate form:
  `search_index_weeks <= 4`. Re-check on_event("the work ships").

## Effort

Nadeesha put this at 4 weeks of hands-on work, blocked time aside.

## Not decided here

If the vendor drops their price we would look at hosted search again.
"""


def make_document(text: str = ADR_BODY, doc_id: str = "DOC-0001") -> Document:
    """A document through the real adapter, so its offsets are real offsets."""
    source = MARKDOWN_ADAPTER.normalise(text.encode("utf-8"), source_uri="adr.md")
    return document_from(source, doc_id=DocumentId(doc_id), ingested_at=AT)


def spans_of(document: Document) -> tuple[Span, ...]:
    """One span per block -- the segmenter's deterministic floor.

    Used rather than a model's segmentation so that these tests do not depend on
    what the mock happened to answer about grouping.
    """
    return tuple(
        Span.covering(document, block.start_byte, block.end_byte, created_by="test", created_at=AT)
        for block in blocks_in(document)
    )


class Documents:
    """A `DocumentSource` backed by a dictionary."""

    def __init__(self, *documents: Document) -> None:
        self.documents = {document.id: document for document in documents}

    def document_for(self, doc_id):
        return self.documents.get(doc_id)


@pytest.fixture
def document() -> Document:
    return make_document()


@pytest.fixture
def spans(document: Document) -> tuple[Span, ...]:
    return spans_of(document)


@pytest.fixture
def offering(spans: tuple[Span, ...]) -> Offering:
    return offering_of(spans)


@pytest.fixture
def documents(document: Document) -> Documents:
    return Documents(document)
