"""The one gate every extracted claim's citation passes through.

`DecisionStructurer` had this inline, and `AssumptionExtractor` needs exactly
the same thing: resolve the ordinal the model cited, re-read the span against
its document, check the quotation, and name precisely which of the four
failures it was. Two copies would be two vocabularies within a phase, and the
eval harness groups by `Refusal` -- a refusal spelled differently by two agents
is a row that splits in the table for no reason anyone could act on.

The order the checks run in is the substance, and it is not the obvious one.

1. **Was a passage cited at all, and one that was offered?** An ordinal outside
   the listing is `UNOFFERED_SPAN`, which ADR 0015 makes the only citation
   target a model can get wrong.
2. **Does that span still resolve against its document?** Asked *before* the
   quotation, because a span this pipeline produced that will not re-read means
   the document changed underneath the run. That is `SPAN_DOES_NOT_RESOLVE`,
   and reporting it as a fabrication would put a re-ingested corpus in the
   hallucination column.
3. **Is the quotation really in it?** `VerifierAgent` decides, against the
   document rather than against the listing -- invariant 6, and the reason the
   verifier is deterministic code.
4. **If not, where did the quotation come from?** In another passage the agent
   was shown is `MIS_ATTRIBUTED_QUOTE`: the model read the material and pointed
   at the wrong part of it. In none of them is `FABRICATED_QUOTE`. ADR 0015
   argues why those two stay apart, and this is where the distinction is drawn.

Nothing here raises. A bad citation is an answer about one claim, and the
caller's next move is the same either way: keep it or record why it was
dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

from praxis.agents.errors import Refusal
from praxis.agents.offering import Offering, QuoteVerdict, Rejection
from praxis.domain.records import Document, Span
from praxis.ingest.verifier import ClaimMatch, VerifierAgent


@dataclass(frozen=True, slots=True)
class Cited:
    """A citation that survived every check, and how closely it matched.

    Attributes:
        span: The passage, re-read against its document.
        quote: What the model quoted, kept for the report.
        match: Exact, or the same text laid out differently.
    """

    span: Span
    quote: str
    match: ClaimMatch


@dataclass(frozen=True, slots=True)
class Uncited:
    """A citation that did not, and precisely which failure it was.

    Attributes:
        refusal: The defect, from the vocabulary the eval harness groups by.
        detail: The evidence against it. A verdict reported without this is a
            verdict nobody can check.
        ordinal: What the model cited, where it cited anything at all.
    """

    refusal: Refusal
    detail: str
    ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class CitationGate:
    """One listing, one document, and the checks every claim about them faces.

    A gate rather than a function because a call's answer usually carries
    several claims -- an extraction finds three assumptions in one reply -- and
    all of them are cited into the same listing. Building the gate once makes
    that structural rather than a parameter three callers have to keep in step.

    Attributes:
        offering: The numbered listing the agent was shown.
        document: The version to re-read cited spans against.
        verifier: The deterministic gate, held by the caller so one run has one.
        subject: What is being cited, for a refusal's detail -- "decision",
            "assumption". Prose only; nothing branches on it.
    """

    offering: Offering
    document: Document
    verifier: VerifierAgent
    subject: str

    def check(self, ordinal: int | None, quote: str | None) -> Cited | Uncited:
        """Resolve and verify one claim's citation.

        Args:
            ordinal: The passage the model named, or `None` if it named none.
            quote: The text it says that passage contains.

        Returns:
            The verified evidence, or a refusal naming the defect.
        """
        resolved = self._resolve(ordinal)
        if isinstance(resolved, Uncited):
            return resolved
        resolution = self.verifier.verify_spans((resolved,), self.document)
        if not resolution.ok:
            return Uncited(
                refusal=Refusal.SPAN_DOES_NOT_RESOLVE,
                detail=resolution.rejected[0].detail,
                ordinal=ordinal,
            )
        said = quote or ""
        verdict = self.verifier.verify_claim(said, resolved, self.document)
        if not verdict.ok:
            return self._misquotation(said, resolved, verdict.detail, ordinal)
        return Cited(span=resolved, quote=said, match=verdict.match)

    def _resolve(self, ordinal: int | None) -> Span | Uncited:
        """Turn the cited ordinal into a span, or refuse it."""
        if ordinal is None:
            return Uncited(
                refusal=Refusal.UNOFFERED_SPAN,
                detail=f"the answer claims a {self.subject} was found and cites no passage",
            )
        resolved = self.offering.resolve(ordinal)
        if isinstance(resolved, Rejection):
            return Uncited(
                refusal=Refusal.UNOFFERED_SPAN,
                detail=resolved.reason,
                ordinal=resolved.ordinal,
            )
        return resolved

    def _misquotation(self, quote: str, cited: Span, detail: str, ordinal: int | None) -> Uncited:
        """Say which quotation failure this was."""
        if self.offering.where_quoted(quote, cited.id) is QuoteVerdict.IN_ANOTHER_OFFERED_SPAN:
            return Uncited(
                refusal=Refusal.MIS_ATTRIBUTED_QUOTE,
                detail=f"the quotation is in another passage that was offered, not {cited.id}",
                ordinal=ordinal,
            )
        if not quote.strip():
            return Uncited(
                refusal=Refusal.FABRICATED_QUOTE,
                detail=f"the answer claims a {self.subject} was found and quotes nothing",
                ordinal=ordinal,
            )
        return Uncited(refusal=Refusal.FABRICATED_QUOTE, detail=detail, ordinal=ordinal)
