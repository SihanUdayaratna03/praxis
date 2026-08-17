"""The numbered listing of spans an agent may cite, and the way back from it.

ADR 0011 gave `SegmenterAgent` a grid of numbered blocks and took its answer in
block numbers, so that a citation of text that is not there was not something
the agent could express. ADR 0015 extends exactly that to extraction: an agent
is shown its spans **numbered**, and every claim it makes names an ordinal from
that listing rather than a `SpanId`.

The consequence is worth stating precisely, because it is easy to overclaim. A
`SpanId` is a 16-hex digest of coordinates, so a model asked for one can produce
a well-formed id that addresses nothing -- `praxis.llm.synthesis` does exactly
that on purpose, and `VerifierAgent` catches it. An ordinal cannot be
well-formed and wrong in the same way: it is either in `0..n-1`, in which case
it resolves to a span that was really offered, or it is not, in which case it is
refused here with a reason. **What this removes is the fabricated citation
target. What it does not remove is the wrong one** -- an agent can still cite
passage 3 and quote passage 5, which is why `VerifierAgent.verify_claim` runs
over every extraction regardless and why `QuoteVerdict` below distinguishes the
two failures.

That distinction is not bookkeeping. "Quoted something from another passage I
was shown" is a model that read the material and mis-attributed; "quoted
something in none of them" is a model that invented. They call for different
responses in a live run, and lumping them together would hide the difference in
a single citation-accuracy figure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from praxis.domain.ids import DocumentId, SpanId
from praxis.domain.records import Span

MAX_OFFERED_CHARS: Final = 1_200
"""How much of one span's text is shown before it is elided.

A span is a unit of meaning and is usually a paragraph, but the block grid
admits whole tables and whole fenced code, and one of those can be most of a
window's tokens. Elision is marked, and the *offsets are never elided* -- the
span is still the whole span, so a claim about the elided tail is refused by
`verify_claim` rather than accepted against text nobody read.
"""

_ELISION: Final = "\n[...]"


class QuoteVerdict(StrEnum):
    """Where an agent's quotation actually came from.

    Ordered from best to worst, and each member is a different fault.
    """

    IN_CITED_SPAN = "in_cited_span"
    """The quotation is in the span the agent cited. The only acceptable answer."""

    IN_ANOTHER_OFFERED_SPAN = "in_another_offered_span"
    """It is in a span the agent was shown, but not the one it cited. A
    mis-attribution: the model read the material and pointed at the wrong part
    of it. Refused, and refused *as this*, because it is evidence of a
    different defect from the one below."""

    NOWHERE = "nowhere"
    """It is in none of the spans the agent was shown. This is the fabricated
    quotation, and the reason `VerifierAgent` exists."""


@dataclass(frozen=True, slots=True)
class Offered:
    """One span as it was put in front of an agent."""

    ordinal: int
    span: Span


@dataclass(frozen=True, slots=True)
class Rejection:
    """A citation that could not be resolved, and why.

    Carried out rather than logged, for the reason `GroupRejection` is: the rate
    at which a model cites something it was not offered is one of the few things
    about model quality this pipeline can measure without a labelled corpus.
    """

    ordinal: int
    reason: str


@dataclass(frozen=True, slots=True)
class Offering:
    """The spans one call may cite, numbered, and the resolution back to ids.

    Ordinals are positions in *this* listing and start at zero, so they are
    small numbers a model handles reliably. They are deliberately not the span's
    position in the document: a window is what the agent can see, and numbering
    by anything it cannot see would let it reason about spans it was not shown.
    """

    entries: tuple[Offered, ...]

    def __post_init__(self) -> None:
        """Reject a listing that could not be cited into."""
        if not self.entries:
            message = "an offering with no spans gives an agent nothing to cite"
            raise ValueError(message)
        expected = tuple(range(len(self.entries)))
        actual = tuple(entry.ordinal for entry in self.entries)
        if actual != expected:
            message = f"ordinals must run 0..{len(self.entries) - 1} in order, got {actual}"
            raise ValueError(message)

    def __len__(self) -> int:
        """How many spans are on offer."""
        return len(self.entries)

    @property
    def doc_id(self) -> DocumentId:
        """The document these spans cite. One offering never crosses documents."""
        return self.entries[0].span.doc_id

    @property
    def spans(self) -> tuple[Span, ...]:
        """The spans, in the order they were offered."""
        return tuple(entry.span for entry in self.entries)

    def render(self) -> str:
        """Render the listing the agent reads.

        `[3]` is the same label shape `praxis.ingest.segmenter` uses, which is
        not cosmetic: `praxis.llm.synthesis` recognises it and draws its offline
        ordinals from labels the prompt really presented. A different shape here
        would make every offline answer cite a passage that was never offered,
        which is a systematic failure rather than a realistic one.
        """
        return "\n\n".join(f"[{entry.ordinal}] {_shown(entry.span.text)}" for entry in self.entries)

    def span_for(self, ordinal: int) -> Span | None:
        """The span at an ordinal, or `None` if it was never offered."""
        if 0 <= ordinal < len(self.entries):
            return self.entries[ordinal].span
        return None

    def resolve(self, ordinal: int) -> Span | Rejection:
        """Resolve a cited ordinal, or say why it cannot be.

        Returns:
            The span, or a `Rejection` naming what was actually on offer. The
            union is deliberate: every caller's next move is the same for both
            -- keep it or record why it was dropped -- and an exception here
            would make one bad citation end a document.
        """
        span = self.span_for(ordinal)
        if span is None:
            return Rejection(
                ordinal=ordinal,
                reason=f"passages 0-{len(self.entries) - 1} were offered",
            )
        return span

    def where_quoted(self, quote: str, cited: SpanId) -> QuoteVerdict:
        """Say which of the offered spans a quotation is really in.

        Whitespace is flattened before comparing, and nothing else is. That is
        the same latitude `VerifierAgent` gives and for the same reason: a
        sentence that wrapped across two lines is quoted back with a space, and
        refusing that would reject correct citations. A changed word, a
        different number or a dropped negation is not whitespace.

        This does **not** decide whether a claim may be stored -- `VerifierAgent`
        does, against the document rather than against this listing. It exists to
        tell the two failures apart for the run's report.
        """
        wanted = _flattened(quote)
        if not wanted:
            return QuoteVerdict.NOWHERE
        for entry in self.entries:
            if wanted in _flattened(entry.span.text):
                return (
                    QuoteVerdict.IN_CITED_SPAN
                    if entry.span.id == cited
                    else QuoteVerdict.IN_ANOTHER_OFFERED_SPAN
                )
        return QuoteVerdict.NOWHERE


def offering_of(spans: Sequence[Span]) -> Offering:
    """Number a run of spans for one call.

    Raises:
        ValueError: if the spans are empty or do not all cite one document. A
            listing spanning two documents would let an agent assemble one claim
            out of two sources without saying so.
    """
    if not spans:
        message = "an offering with no spans gives an agent nothing to cite"
        raise ValueError(message)
    documents = {span.doc_id for span in spans}
    if len(documents) != 1:
        message = f"one offering, one document; got {len(documents)}: {sorted(documents)}"
        raise ValueError(message)
    return Offering(
        entries=tuple(Offered(ordinal=index, span=span) for index, span in enumerate(spans))
    )


def windows_of(spans: Sequence[Span], size: int) -> tuple[Offering, ...]:
    """Cut a document's spans into the listings put in front of a model, in order.

    Raises:
        ValueError: if the window is not positive, which would make a document
            silently produce no calls.
    """
    if size < 1:
        message = f"a window must hold at least one span, got {size}"
        raise ValueError(message)
    return tuple(offering_of(spans[start : start + size]) for start in range(0, len(spans), size))


def around(spans: Sequence[Span], index: int, *, reach: int) -> Offering:
    """The span at `index` with its neighbours, as one listing.

    What the structurer and the extractor are given: a candidate is rarely
    self-contained -- an ADR states its decision under one heading and its
    assumptions under another -- so the material is the candidate plus what sits
    beside it. The candidate keeps no special ordinal; an agent that has to pick
    it out of its neighbours is being asked the question the live run asks.

    Raises:
        ValueError: if `reach` is negative or `index` is outside `spans`.
    """
    if reach < 0:
        message = f"reach is a distance, so it cannot be {reach}"
        raise ValueError(message)
    if not 0 <= index < len(spans):
        message = f"{index} is not a position in {len(spans)} spans"
        raise ValueError(message)
    start = max(0, index - reach)
    return offering_of(spans[start : index + reach + 1])


def _shown(text: str) -> str:
    """One span's text as the agent sees it, elided if it is very long."""
    if len(text) <= MAX_OFFERED_CHARS:
        return text
    return text[:MAX_OFFERED_CHARS] + _ELISION


def _flattened(text: str) -> str:
    """Collapse runs of whitespace to single spaces, and nothing else."""
    return " ".join(text.split())
