"""Span verification -- the deterministic half of the hallucinated-citation gate.

Phase 3 rejects any extracted claim whose span does not survive `verify_span`,
so every way a citation can lie needs a test here. If this file is weak, a
fabricated quotation with a plausible byte range becomes undetectable, and the
provenance half of the product is decorative.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from praxis.domain.records import Document, Span
from praxis.domain.spans import (
    SpanDefect,
    SpanIntegrityError,
    require_span_resolves,
    verify_span,
)

from tests import strategies as s

NOW = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)


def document(content: str, doc_id: str = "DOC-0001") -> Document:
    return Document(
        id=doc_id,
        source_uri="corpus/sample.md",
        source_kind="markdown",
        content=content,
        ingested_at=NOW,
        created_at=NOW,
        created_by="SourceAdapter",
    )


def test_a_span_cut_from_a_document_resolves_against_it():
    doc = document("the migration will take six weeks")
    span = Span.covering(doc, 4, 13, created_by="t", created_at=NOW)
    assert span.text == "migration"
    assert verify_span(span, doc)


@given(
    document_and_span=s.documents()
    .filter(lambda d: d.content)
    .flatmap(lambda d: s.spans(document=d).map(lambda sp: (d, sp)))
)
def test_any_span_cut_from_a_document_resolves(document_and_span):
    doc, span = document_and_span
    verdict = verify_span(span, doc)
    assert verdict.ok, verdict.detail


def test_a_fabricated_quotation_is_caught():
    # The span is structurally perfect: right document, in-bounds range, text of
    # exactly the right byte width. Only the words are invented.
    doc = document("the migration will take six weeks")
    forged = Span(
        doc_id=doc.id,
        start_byte=0,
        end_byte=3,
        text="XYZ",
        created_by="AssumptionExtractor",
        created_at=NOW,
    )
    verdict = verify_span(forged, doc)
    assert not verdict.ok
    assert verdict.defect is SpanDefect.TEXT_MISMATCH
    assert "the" in verdict.detail


def test_a_span_checked_against_the_wrong_document_says_so():
    doc = document("alpha")
    other = document("alpha", doc_id="DOC-0002")
    span = Span.covering(doc, 0, 5, created_by="t", created_at=NOW)
    verdict = verify_span(span, other)
    assert verdict.defect is SpanDefect.WRONG_DOCUMENT


def test_a_span_past_the_end_of_a_shorter_version_is_caught():
    # Documents are versioned, so a span cut from a long version is expected to
    # stop resolving against a re-ingested shorter one. That is a finding about
    # the corpus, not a bug.
    long_version = document("a much longer body of text")
    span = Span.covering(long_version, 0, 26, created_by="t", created_at=NOW)
    short_version = document("short")
    verdict = verify_span(span, short_version)
    assert verdict.defect is SpanDefect.OUT_OF_BOUNDS


def test_offsets_inside_a_multibyte_character_are_caught():
    doc = document("wörld")  # o-umlaut occupies bytes 1 and 2
    split = Span(
        doc_id=doc.id,
        start_byte=1,
        end_byte=2,
        text="?",
        created_by="t",
        created_at=NOW,
    )
    verdict = verify_span(split, doc)
    assert verdict.defect is SpanDefect.SPLIT_CHARACTER


def test_verification_is_falsey_when_it_fails():
    doc = document("alpha")
    other = document("alpha", doc_id="DOC-0002")
    span = Span.covering(doc, 0, 5, created_by="t", created_at=NOW)
    assert not verify_span(span, other)
    assert verify_span(span, doc)


def test_require_span_resolves_raises_with_the_defect_named():
    doc = document("the migration will take six weeks")
    forged = Span(
        doc_id=doc.id, start_byte=0, end_byte=3, text="XYZ", created_by="t", created_at=NOW
    )
    with pytest.raises(SpanIntegrityError, match="text_mismatch"):
        require_span_resolves(forged, doc)
    require_span_resolves(Span.covering(doc, 0, 3, created_by="t", created_at=NOW), doc)
