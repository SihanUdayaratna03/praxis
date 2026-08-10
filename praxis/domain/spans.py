"""Re-read a span against its document and say whether it still resolves.

This is the deterministic half of the hallucinated-citation gate. Phase 3's
`VerifierAgent` rejects any extracted claim whose span does not survive
`verify_span`, so a model that invents a quotation cannot get it into the store
by attaching a plausible-looking byte range to it.

Nothing here calls a model, and nothing here ever will: a hallucination check
that could itself hallucinate is not a check. That is why the function returns a
reason string rather than a bare boolean -- the reason is evidence, and it goes
into the `Finding` that rejecting a claim produces.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from praxis.domain.records import Document, Span


class SpanDefect(StrEnum):
    """Why a span failed to resolve. One member per way a citation can lie."""

    WRONG_DOCUMENT = "wrong_document"
    """Checked against a document the span does not point at. A caller error
    rather than a bad span, and worth distinguishing for that reason."""

    OUT_OF_BOUNDS = "out_of_bounds"
    """The range runs past the end of the document."""

    SPLIT_CHARACTER = "split_character"
    """The offsets fall inside a multi-byte character, so the range does not
    denote text at all."""

    TEXT_MISMATCH = "text_mismatch"
    """The range resolves, and to something other than the quoted text. This is
    the one that catches a fabricated citation."""


@dataclass(frozen=True, slots=True)
class SpanVerification:
    """The verdict on one span, with the evidence for it."""

    ok: bool
    defect: SpanDefect | None = None
    detail: str = ""

    def __bool__(self) -> bool:
        return self.ok


class SpanIntegrityError(ValueError):
    """Raised when a span that was required to resolve did not."""


_OK = SpanVerification(ok=True)


def verify_span(span: Span, document: Document) -> SpanVerification:
    """Confirm that `span` still addresses the text it claims to quote.

    The document passed in is the one the caller wants to check against, which
    matters because documents are versioned: a re-ingested source becomes a new
    version, and a span cut from the old one is expected to stop resolving
    against the new. That is a real finding, not a bug in this function.

    Args:
        span: The citation to check.
        document: The document version to re-read.

    Returns:
        A verdict carrying the defect and a human-readable detail when it fails.
    """
    if span.doc_id != document.id:
        return SpanVerification(
            ok=False,
            defect=SpanDefect.WRONG_DOCUMENT,
            detail=f"span cites {span.doc_id}, checked against {document.id}",
        )

    raw = document.content_bytes
    if span.end_byte > len(raw):
        return SpanVerification(
            ok=False,
            defect=SpanDefect.OUT_OF_BOUNDS,
            detail=f"range ends at {span.end_byte}, document is {len(raw)} bytes",
        )

    try:
        found = raw[span.start_byte : span.end_byte].decode("utf-8")
    except UnicodeDecodeError as exc:
        return SpanVerification(
            ok=False,
            defect=SpanDefect.SPLIT_CHARACTER,
            detail=f"[{span.start_byte}, {span.end_byte}) splits a character: {exc.reason}",
        )

    if found != span.text:
        return SpanVerification(
            ok=False,
            defect=SpanDefect.TEXT_MISMATCH,
            detail=f"document holds {found!r} where the span quotes {span.text!r}",
        )
    return _OK


def require_span_resolves(span: Span, document: Document) -> None:
    """Verify a span, raising rather than returning a verdict.

    For call sites where a span that does not resolve is a programming error
    rather than a finding about the corpus.

    Raises:
        SpanIntegrityError: if the span does not resolve.
    """
    verdict = verify_span(span, document)
    if verdict.ok:
        return
    defect = verdict.defect.value if verdict.defect is not None else "unknown"
    message = f"{span.id} does not resolve ({defect}): {verdict.detail}"
    raise SpanIntegrityError(message)
