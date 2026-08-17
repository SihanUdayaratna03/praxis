"""The gate: nothing enters the store without being re-read against its source.

`VerifierAgent` is deterministic Python and always will be. A hallucination
check that could itself hallucinate is not a check -- it is a second opinion
from the same kind of thing that produced the first, and the whole reason
invariant 6 is worth stating is that this one is not. The agent is in
`NON_LLM_AGENTS`, `role_for_agent` refuses to route it, and `praxis doctor`
fails if it ever acquires a model.

There are two questions, and they are different.

**Does this span resolve?** Its document exists, its offsets fall inside that
document, they do not split a character, and the text they address is exactly
the text the span quotes. `praxis.domain.spans.verify_span` answers most of
that; this module adds the first part, because a span can cite a document
nothing ever ingested and that is the most complete way a citation can be
fabricated.

**Does this span contain that claim?** The invariant-6 question, and the one
every later agent depends on: an extracted decision, assumption or estimate
carries a `span_id`, and this is what re-reads the span and refuses the claim
if the span does not say it.

The only latitude given anywhere here is whitespace. A model quoting a sentence
that wrapped across two lines writes it with a space, and refusing that would
reject correct citations -- which is worse than the failure the gate exists to
catch, because it would train everyone to route around the gate. Whitespace is
a rendering artefact. Nothing else is: a changed word, a different number, a
dropped negation are all the fabrication this refuses, and none of them is
normalised away before comparing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from praxis.domain.ids import DocumentId, SpanId
from praxis.domain.records import Document, Span
from praxis.domain.spans import SpanDefect, verify_span

VERIFIER_NAME: Final = "VerifierAgent"
"""Spelled as `NON_LLM_AGENTS` spells it, so the set that refuses to route this
agent refuses to route this agent."""


class DocumentSource(Protocol):
    """Somewhere the document a span cites can be read back from.

    A protocol rather than the repository, so the gate can be exercised without
    a database and so `praxis.ingest` does not have to know that the store is
    SQLite. The pipeline passes something repository-backed; a test passes a
    dictionary.
    """

    def document_for(self, doc_id: DocumentId) -> Document | None:
        """Return the current version of a document, or `None` if unknown."""
        ...


class ClaimMatch(StrEnum):
    """How closely a quoted claim matches the span it cites."""

    EXACT = "exact"
    """The claim is in the span's text, character for character."""

    WHITESPACE = "whitespace"
    """It is there once runs of whitespace are treated as equal.

    Accepted. A sentence that wrapped across two lines in the source is quoted
    back with a space, and that is a fact about rendering rather than about
    what the document said."""

    ABSENT = "absent"
    """It is not there. This is the hallucinated citation."""


@dataclass(frozen=True, slots=True)
class SpanRejection:
    """One span that did not survive the gate, with the evidence against it.

    The detail is not decoration: it is what a `Finding` about a fabricated
    citation would have to quote, and a rejection reported without it is a
    verdict nobody can check.
    """

    span_id: SpanId
    doc_id: DocumentId
    defect: SpanDefect
    detail: str


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """What survived verification, and what did not.

    Attributes:
        accepted: Spans that resolve, in the order they were offered, with
            repeats collapsed.
        rejected: Spans that did not, with the defect and the evidence.
    """

    accepted: tuple[Span, ...]
    rejected: tuple[SpanRejection, ...]

    @property
    def ok(self) -> bool:
        """Whether every span offered survived."""
        return not self.rejected

    def __bool__(self) -> bool:
        return self.ok


@dataclass(frozen=True, slots=True)
class ClaimVerdict:
    """Whether a span really contains the claim attached to it."""

    match: ClaimMatch
    detail: str = ""

    @property
    def ok(self) -> bool:
        """Whether the claim may be stored against this span."""
        return self.match is not ClaimMatch.ABSENT

    def __bool__(self) -> bool:
        return self.ok


class VerifierAgent:
    """Re-reads citations against their sources. Deterministic, no model."""

    name: Final = VERIFIER_NAME

    def verify_spans(self, spans: tuple[Span, ...], document: Document) -> VerificationReport:
        """Check every span against one document it should be citing.

        Repeats collapse rather than being reported twice: a span's id is a
        function of its own coordinates, so two citations of one byte range are
        one citation, and writing both would fail the store for a reason that
        says nothing about the corpus.

        Args:
            spans: The citations to check.
            document: The version to re-read them against.

        Returns:
            The accepted spans and the rejections, with a defect each.
        """
        return self.verify_against(spans, _AlwaysThisDocument(document))

    def verify_against(self, spans: tuple[Span, ...], source: DocumentSource) -> VerificationReport:
        """Check spans that may cite several documents, resolving each one.

        The entry point the pipeline and every later agent uses, because a
        claim's span id arrives on its own and the document it names is
        whatever the store holds -- including, when a model invented the id,
        nothing at all.

        Args:
            spans: The citations to check.
            source: Where a document is read back from.

        Returns:
            The accepted spans and the rejections.
        """
        accepted: dict[SpanId, Span] = {}
        rejected: list[SpanRejection] = []
        documents: dict[DocumentId, Document | None] = {}
        for span in spans:
            if span.doc_id not in documents:
                documents[span.doc_id] = source.document_for(span.doc_id)
            document = documents[span.doc_id]
            if document is None:
                rejected.append(
                    _rejection(
                        span,
                        SpanDefect.UNKNOWN_DOCUMENT,
                        f"nothing has ingested {span.doc_id}",
                    )
                )
                continue
            verdict = verify_span(span, document)
            if verdict.ok:
                accepted.setdefault(span.id, span)
            else:
                rejected.append(_rejection(span, verdict.defect, verdict.detail))
        return VerificationReport(accepted=tuple(accepted.values()), rejected=tuple(rejected))

    def verify_claim(self, claim: str, span: Span, document: Document) -> ClaimVerdict:
        """Check that a span really contains the claim attached to it.

        Invariant 6, and the reason every extracted record carries a `span_id`
        rather than a copy of the text: the copy would agree with itself.

        The span is re-read against the document first. A claim quoted from a
        span that no longer resolves is not a claim about anything, and
        reporting the two failures separately is what lets a re-ingested
        corpus be told apart from a fabricating model.

        Args:
            claim: The text an agent says the span contains.
            span: The citation.
            document: The version to re-read.

        Returns:
            A verdict, `ABSENT` when the span does not say it.
        """
        resolved = verify_span(span, document)
        if not resolved.ok:
            defect = resolved.defect.value if resolved.defect is not None else "unknown"
            return ClaimVerdict(
                match=ClaimMatch.ABSENT,
                detail=f"the cited span does not resolve ({defect}): {resolved.detail}",
            )
        if not claim.strip():
            return ClaimVerdict(match=ClaimMatch.ABSENT, detail="the claim is empty")
        if claim in span.text:
            return ClaimVerdict(match=ClaimMatch.EXACT)
        if _flattened(claim) in _flattened(span.text):
            return ClaimVerdict(
                match=ClaimMatch.WHITESPACE,
                detail="the span says it, laid out differently",
            )
        return ClaimVerdict(
            match=ClaimMatch.ABSENT,
            detail=f"{span.id} does not contain {claim!r}",
        )


@dataclass(frozen=True, slots=True)
class _AlwaysThisDocument:
    """A source that answers every id with the one document it was handed.

    So that both entry points share one body. Answering regardless of the id
    asked for is deliberate: a span citing something else then fails as
    `WRONG_DOCUMENT` rather than as `UNKNOWN_DOCUMENT`, which is the honest
    answer when the caller supplied the document -- it is right there, and the
    span is simply not about it.
    """

    document: Document

    def document_for(self, doc_id: DocumentId) -> Document | None:  # noqa: ARG002
        """Return the document, whichever id was asked for."""
        return self.document


def _rejection(span: Span, defect: SpanDefect | None, detail: str) -> SpanRejection:
    """Build a rejection, defaulting a missing defect to the mismatch case.

    `verify_span` never returns a failed verdict without a defect. The default
    exists so that a future member arriving without one is recorded as the
    worst case rather than crashing the gate -- a verifier that dies on an
    unfamiliar failure is a verifier that stops verifying.
    """
    return SpanRejection(
        span_id=span.id,
        doc_id=span.doc_id,
        defect=defect if defect is not None else SpanDefect.TEXT_MISMATCH,
        detail=detail,
    )


def _flattened(text: str) -> str:
    """Collapse runs of whitespace to single spaces, and nothing else.

    Deliberately not case folding and not stripping punctuation. Those are
    where a real difference in meaning hides -- `MUST` against `must` is
    usually nothing and sometimes a requirement level, and a dropped full stop
    can be a dropped sentence.
    """
    return " ".join(text.split())
