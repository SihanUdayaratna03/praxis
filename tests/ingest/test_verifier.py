"""The citation gate, against citations built to get past it.

Every span the segmenter produces resolves by construction, so a suite fed only
by the segmenter would pass while testing nothing. These spans are fabricated
on purpose -- pointing at the wrong document, past the end of the right one,
into the middle of a character, and at text the document does not contain --
because a gate nobody has watched refuse something is a gate nobody knows the
shape of.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.config.models import UnknownAgentError, role_for_agent
from praxis.domain.ids import DocumentId, span_id_for
from praxis.domain.records import Document, Span
from praxis.domain.spans import SpanDefect
from praxis.ingest.adapters import MARKDOWN_ADAPTER, document_from
from praxis.ingest.verifier import (
    VERIFIER_NAME,
    ClaimMatch,
    VerifierAgent,
)

AT = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
BODY = "# Store location\n\nWe chose SQLite over Postgres.\nNobody will run a server.\n"


def document(text: str = BODY, doc_id: str = "DOC-0001") -> Document:
    source = MARKDOWN_ADAPTER.normalise(text.encode("utf-8"), source_uri="d.md")
    return document_from(source, doc_id=DocumentId(doc_id), ingested_at=AT)


def span_over(doc: Document, start: int, end: int) -> Span:
    return Span.covering(doc, start, end, created_by="test", created_at=AT)


def fabricated(doc_id: str, start: int, end: int, text: str) -> Span:
    """A well-formed span that quotes something other than what is there.

    Well-formed matters: the id addresses its own coordinates and the text is
    exactly as wide as the range, so nothing but re-reading the document can
    tell that it is a lie. That is the citation the gate exists for.
    """
    return Span(
        id=span_id_for(DocumentId(doc_id), start, end),
        doc_id=DocumentId(doc_id),
        start_byte=start,
        end_byte=end,
        text=text,
        created_by="a model",
        created_at=AT,
    )


class Store:
    """A document source backed by a dictionary."""

    def __init__(self, *documents: Document) -> None:
        self.documents = {doc.id: doc for doc in documents}

    def document_for(self, doc_id):
        return self.documents.get(doc_id)


# -- spans that resolve ------------------------------------------------------


def test_a_span_cut_from_the_document_is_accepted():
    doc = document()
    span = span_over(doc, 0, 16)

    report = VerifierAgent().verify_spans((span,), doc)

    assert report.ok
    assert report.accepted == (span,)
    assert bool(report) is True


def test_the_same_citation_twice_is_one_citation():
    """A span's id is a function of its coordinates, so two citations of one
    byte range are one -- and writing both would fail the store for a reason
    that says nothing about the corpus."""
    doc = document()
    span = span_over(doc, 0, 16)

    report = VerifierAgent().verify_spans((span, span), doc)

    assert report.accepted == (span,)
    assert report.rejected == ()


# -- spans that do not -------------------------------------------------------


def test_a_span_citing_another_document_is_refused():
    doc = document()
    other = fabricated("DOC-0009", 0, 5, BODY[:5])

    report = VerifierAgent().verify_spans((other,), doc)

    assert report.rejected[0].defect is SpanDefect.WRONG_DOCUMENT
    assert not report.ok


def test_a_span_running_past_the_end_is_refused():
    doc = document()
    beyond = fabricated("DOC-0001", 0, len(doc.content_bytes) + 40, "x" * (doc.byte_length + 40))

    report = VerifierAgent().verify_spans((beyond,), doc)

    assert report.rejected[0].defect is SpanDefect.OUT_OF_BOUNDS
    assert "bytes" in report.rejected[0].detail


def test_a_span_that_splits_a_character_is_refused():
    """Offsets that fall inside a multi-byte character denote no text at all,
    which a check working in characters would never notice."""
    doc = document("éab\n\nsecond", doc_id="DOC-0002")
    halfway = fabricated("DOC-0002", 1, 3, "xy")

    report = VerifierAgent().verify_spans((halfway,), doc)

    assert report.rejected[0].defect is SpanDefect.SPLIT_CHARACTER


def test_a_span_quoting_text_the_document_does_not_hold_is_refused():
    """The one that catches a fabricated citation: well-formed in every way
    except that the document says something else."""
    doc = document()
    invented = fabricated("DOC-0001", 0, 16, "# Store lication")

    report = VerifierAgent().verify_spans((invented,), doc)

    assert report.rejected[0].defect is SpanDefect.TEXT_MISMATCH
    assert "# Store location" in report.rejected[0].detail


def test_a_span_citing_a_document_nothing_ingested_is_refused():
    """The most complete way a citation can be fabricated, and the reason the
    verifier resolves documents rather than being handed one."""
    doc = document()
    invented = fabricated("DOC-0042", 0, 5, "# Sto")

    report = VerifierAgent().verify_against((invented,), Store(doc))

    assert report.rejected[0].defect is SpanDefect.UNKNOWN_DOCUMENT
    assert "DOC-0042" in report.rejected[0].detail


def test_good_and_bad_citations_in_one_batch_are_separated():
    doc = document()
    good = span_over(doc, 0, 16)
    bad = fabricated("DOC-0001", 0, 16, "# Store lication")

    report = VerifierAgent().verify_against((good, bad), Store(doc))

    assert report.accepted == (good,)
    assert len(report.rejected) == 1


def test_spans_across_several_documents_are_each_read_against_their_own():
    first, second = document(doc_id="DOC-0001"), document("Another source.\n", doc_id="DOC-0003")

    report = VerifierAgent().verify_against(
        (span_over(first, 0, 16), span_over(second, 0, 7)), Store(first, second)
    )

    assert report.ok
    assert len(report.accepted) == 2


# -- claims against spans ----------------------------------------------------


def test_a_claim_the_span_really_contains_is_exact():
    doc = document()
    span = span_over(doc, 18, 48)

    verdict = VerifierAgent().verify_claim("SQLite over Postgres", span, doc)

    assert verdict.match is ClaimMatch.EXACT
    assert verdict.ok


def test_a_claim_quoted_across_a_line_break_is_accepted_as_laid_out_differently():
    """A model quoting a sentence that wrapped writes it with a space. Refusing
    that would reject correct citations, which is worse than the failure the
    gate exists to catch."""
    doc = document()
    span = span_over(doc, 18, len(BODY.encode("utf-8")) - 1)

    verdict = VerifierAgent().verify_claim(
        "We chose SQLite over Postgres. Nobody will run a server.", span, doc
    )

    assert verdict.match is ClaimMatch.WHITESPACE
    assert verdict.ok


def test_a_claim_the_span_does_not_contain_is_absent():
    doc = document()
    span = span_over(doc, 18, 48)

    verdict = VerifierAgent().verify_claim("SQLite over MySQL", span, doc)

    assert verdict.match is ClaimMatch.ABSENT
    assert not verdict.ok
    assert "does not contain" in verdict.detail


@pytest.mark.parametrize(
    "claim",
    [
        "we chose sqlite over postgres",
        "We chose SQLite over Postgres!",
        "We chose SQLite over Postgres, mostly",
        # A zero-width space is invisible and is not whitespace, so collapsing
        # runs of whitespace does not remove it -- and it should not.
        "We chose  SQLite over  Postgres" + "\u200b",
    ],
)
def test_only_whitespace_is_normalised_away(claim):
    """Case and punctuation are where a real difference in meaning hides.
    `MUST` against `must` is sometimes a requirement level, and a dropped full
    stop can be a dropped sentence."""
    doc = document()
    span = span_over(doc, 18, 48)

    assert VerifierAgent().verify_claim(claim, span, doc).match is ClaimMatch.ABSENT


def test_an_empty_claim_is_absent_rather_than_trivially_contained():
    """Every span contains the empty string, so accepting it would let an
    agent cite anything by claiming nothing."""
    doc = document()

    verdict = VerifierAgent().verify_claim("   ", span_over(doc, 0, 16), doc)

    assert verdict.match is ClaimMatch.ABSENT
    assert verdict.detail == "the claim is empty"


def test_a_claim_on_a_span_that_no_longer_resolves_says_which_failure_it_was():
    """A re-ingested corpus and a fabricating model both produce a claim that
    cannot be verified, and they are not the same problem."""
    doc = document()
    stale = fabricated("DOC-0001", 0, 16, "# Store lication")

    verdict = VerifierAgent().verify_claim("# Store lication", stale, doc)

    assert verdict.match is ClaimMatch.ABSENT
    assert "does not resolve" in verdict.detail


# -- properties --------------------------------------------------------------


@given(st.integers(min_value=0, max_value=60), st.integers(min_value=1, max_value=15))
def test_any_substring_of_a_resolving_span_is_a_claim_it_contains(start, width):
    doc = document()
    limit = doc.byte_length
    span = span_over(doc, min(start, limit - 1), min(start + width + 1, limit))

    for offset in range(len(span.text)):
        claim = span.text[offset : offset + 4]
        if claim.strip():
            assert VerifierAgent().verify_claim(claim, span, doc).ok


@given(st.integers(min_value=0, max_value=40))
def test_changing_one_character_of_a_quotation_makes_it_absent(position):
    """The mutation a near-miss citation actually is: right length, right
    place, one word different."""
    doc = document()
    span = span_over(doc, 0, doc.byte_length)
    original = span.text
    index = position % len(original)
    mutated = f"{original[:index]}{'Z' if original[index] != 'Z' else 'Q'}{original[index + 1 :]}"

    assert VerifierAgent().verify_claim(mutated, span, doc).match is ClaimMatch.ABSENT


# -- the agent itself --------------------------------------------------------


def test_the_verifier_cannot_be_routed_to_a_model():
    """Invariant 3 from the agent's side. A hallucination check that could
    itself hallucinate is not a check, and this is where that stops being a
    sentence in a document."""
    with pytest.raises(UnknownAgentError, match="deterministic by design"):
        role_for_agent(VERIFIER_NAME)


def test_the_agent_reports_the_name_the_routing_table_refuses():
    assert VerifierAgent().name == "VerifierAgent"
