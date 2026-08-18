"""DecisionStructurer: one scout candidate, read carefully, into a record.

The extract tier, one call per candidate. Where the scout asked "did something
get decided here", this asks "what exactly", and the two are priced differently
on purpose -- noticing costs one cheap call per span, reading costs one mid-tier
call per candidate, and the scout's low precision is what makes the second
number small.

Four decisions in here are worth stating, because each could have gone the easy
way.

**The rationale is on the edge, not on the record.** `Decision` has no
`rationale` field and this does not add one. The stated reasoning is written on
the `justified_by` link from the decision to the span it was read from, because
that is what a rationale *is*: not a property of a decision on its own, but the
reason it points at that evidence. It also means the reasoning is versioned and
audited exactly like the edge is, with no migration and no tenth record type.

**A decision with no alternatives is recorded as one, not invented into two.**
`Decision.rejected` requires at least one entry, and the prompt asks for an
entry saying the document records no alternative rather than for a plausible
alternative. A decision taken without considering anything else is a fact worth
having; a fabricated option is not.

**A missing date falls back to the document, and says that it did.** A document
states a date, not an instant, so a stated date becomes midnight UTC -- the
convention is recorded here rather than inferred by whoever reads the column
later. Where no date is stated at all, `Document.ingested_at` is used, which is
the earliest time anyone can prove the decision existed by, and
`date_was_stated` carries the difference so the eval harness can report it
instead of a reader assuming.

**The agent verifies its own citation before returning anything.** It holds a
`VerifierAgent` and refuses its own output, so no caller is ever handed an
unverified `Decision` that it could write. `Offering.where_quoted` then says
*which* citation failure it was -- a quotation from another offered passage is
a mis-attribution and a quotation from none of them is a fabrication, and ADR
0015 argues why those stay apart. A cited span that no longer resolves against
its document at all is asked about first and named separately again, because
that one is not about the model: it means the document changed underneath the
run, and charging it to the model would hide a re-ingestion as a hallucination.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from praxis.agents.errors import ExtractionError, Refusal
from praxis.agents.offering import Offering, QuoteVerdict, Rejection, around
from praxis.agents.scout import Candidate
from praxis.domain.enums import DecisionScope, DecisionStatus, Impact
from praxis.domain.ids import DecisionId
from praxis.domain.links import LinkType
from praxis.domain.records import Decision, Document, Link, RejectedOption, Span
from praxis.ingest.verifier import ClaimMatch, VerifierAgent
from praxis.llm.errors import MalformedOutputError, ProviderRefusalError
from praxis.llm.provider import LLMProvider
from praxis.llm.structured import REPAIR_ATTEMPTS, ask_for
from praxis.llm.types import LLMRequest, Message, MessageRole
from praxis.obs.logging import get_logger
from praxis.prompts.library import load

_log = get_logger(__name__)

STRUCTURER_NAME: Final = "DecisionStructurer"
"""Spelled as the routing table spells it, so the model tier follows the name."""

STRUCTURE_TASK: Final = "structure_decision"
"""Also the name of the prompt file this agent reads -- ADR 0014."""

DEFAULT_REACH: Final = 2
"""How many spans either side of the candidate are offered as context.

A decision is rarely written down in one place -- an ADR states the choice under
one heading and its alternatives under another -- so the candidate alone is not
the material. Two either side covers the ADR shape without making the listing
large enough for its ordinals to become ambiguous, which is ADR 0015's second
assumption.
"""

NO_ALTERNATIVE_RECORDED: Final = "the document records no alternative"
"""What a rejected option's reason says when there genuinely was not one."""


class RejectedAnswer(BaseModel):
    """One alternative the model says was turned down."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    option: str
    reason: str


class DecisionAnswer(BaseModel):
    """What the model says about one candidate.

    Every field is nullable because `found` may be false, and the structured
    dialect requires every declared property to be present -- so "not found" is
    expressed as nulls rather than as absent keys. That is the same asymmetry
    `praxis.llm.structured` documents: a field that is genuinely optional says
    so by being nullable, which is a fact about the value rather than about
    whether the key was sent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    found: bool
    title: str | None
    chosen: str | None
    rejected: tuple[RejectedAnswer, ...]
    decision_maker: str | None
    decided_on: date | None
    rationale: str | None
    scope: DecisionScope | None
    impact: Impact | None
    status: DecisionStatus | None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ordinal: int | None
    """Named to end in `ordinal` so the offline provider draws it from the
    bracketed labels the prompt really presented -- ADR 0015."""

    evidence_quote: str | None
    """Named to end in `quote` so the offline provider fills it with a sentence
    that really occurs in the prompt, which is what gives `VerifierAgent`
    something real to check offline and still reject."""


@dataclass(frozen=True, slots=True)
class StructuredDecision:
    """A verified decision and the edge carrying why it was taken.

    Attributes:
        decision: The record, ready to store. Its `span_id` has been re-read.
        justification: A `justified_by` edge to the evidence span, carrying the
            stated rationale.
        quote: What the model quoted, kept for the report.
        quote_match: Whether the span said it exactly or laid out differently.
        date_was_stated: False when the document gave no date and
            `Document.ingested_at` stood in.
    """

    decision: Decision
    justification: Link
    quote: str
    quote_match: ClaimMatch
    date_was_stated: bool


@dataclass(frozen=True, slots=True)
class StructureRejection:
    """One candidate that produced nothing storable, and why."""

    refusal: Refusal
    detail: str
    ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class StructureResult:
    """What one candidate cost and what it produced.

    Exactly one of `decision` and `rejection` is set. A candidate the model read
    and found nothing in is a `rejection` carrying `EMPTY_ANSWER`, which is not
    a failure -- the scout is built to over-mark -- but is counted, because
    "found nothing" and "was never asked" are different rows in an eval table.
    """

    decision: StructuredDecision | None = None
    rejection: StructureRejection | None = None
    calls: int = 0

    @property
    def ok(self) -> bool:
        """Whether this candidate produced a storable decision."""
        return self.decision is not None


class DecisionStructurer:
    """Turns one candidate span into a verified `Decision`."""

    name: Final = STRUCTURER_NAME

    def __init__(
        self,
        provider: LLMProvider,
        *,
        reach: int = DEFAULT_REACH,
        max_attempts: int = REPAIR_ATTEMPTS,
        verifier: VerifierAgent | None = None,
    ) -> None:
        """Wire the agent to a provider it did not choose.

        Args:
            provider: The seam, from `provider_for`.
            reach: Spans either side of the candidate offered as context.
            max_attempts: Attempts per call, repairs included.
            verifier: Supplied only by a test. There is one behaviour and it is
                deterministic, which is the whole point of the agent.

        Raises:
            ValueError: if `reach` is negative.
        """
        if reach < 0:
            message = f"reach is a distance, so it cannot be {reach}"
            raise ValueError(message)
        self._provider = provider
        self._reach = reach
        self._max_attempts = max_attempts
        self._verifier = verifier if verifier is not None else VerifierAgent()

    def structure(
        self,
        candidate: Candidate,
        spans: Sequence[Span],
        document: Document,
        *,
        decision_id: str,
        at: datetime,
    ) -> StructureResult:
        """Read one candidate and produce a verified decision, or say why not.

        Args:
            candidate: What the scout marked.
            spans: The document's spans, in order. Used to offer context.
            document: The document, for re-reading the citation.
            decision_id: The id the store allocated. Passed in rather than
                allocated here, so the agent never holds a repository.
            at: When this ran, for the records written. Timezone-aware.

        Returns:
            A decision, or a rejection naming the defect.

        Raises:
            ProviderError: for failures about the run rather than this document.
            ExtractionError: if the candidate is not one of `spans`.
        """
        offering = self._context_for(candidate, spans)
        cited_ordinal = next(
            entry.ordinal for entry in offering.entries if entry.span.id == candidate.span.id
        )
        answer, calls = self._answer_for(offering, cited_ordinal, document)
        if answer is None:
            return StructureResult(
                rejection=StructureRejection(
                    refusal=Refusal.NO_USABLE_ANSWER,
                    detail="the model refused or never satisfied the schema",
                ),
                calls=calls,
            )
        if not answer.found:
            return StructureResult(
                rejection=StructureRejection(
                    refusal=Refusal.EMPTY_ANSWER,
                    detail="the model read the passages and found no decision taken",
                ),
                calls=calls,
            )
        built = self._build(answer, offering, document, decision_id=decision_id, at=at)
        return (
            StructureResult(decision=built, calls=calls)
            if isinstance(built, StructuredDecision)
            else StructureResult(rejection=built, calls=calls)
        )

    def _context_for(self, candidate: Candidate, spans: Sequence[Span]) -> Offering:
        """Offer the candidate with its neighbours, the candidate unmarked."""
        index = next(
            (position for position, span in enumerate(spans) if span.id == candidate.span.id),
            None,
        )
        if index is None:
            message = f"{candidate.span.id} is not one of the {len(spans)} spans offered"
            raise ExtractionError(message)
        return around(spans, index, reach=self._reach)

    def _answer_for(
        self, offering: Offering, cited_ordinal: int, document: Document
    ) -> tuple[DecisionAnswer | None, int]:
        """Ask about one candidate, returning `None` if the answer was unusable."""
        prompt = load(STRUCTURE_TASK)
        request = LLMRequest(
            agent=self.name,
            task=STRUCTURE_TASK,
            system=prompt.render(candidate_ordinal=cited_ordinal),
            messages=(Message(role=MessageRole.USER, content=offering.render()),),
            prompt_id=prompt.id,
            prompt_sha=prompt.sha256,
            metadata={"doc_id": document.id, "candidate_ordinal": str(cited_ordinal)},
        )
        try:
            result = ask_for(
                self._provider, request, DecisionAnswer, max_attempts=self._max_attempts
            )
        except (MalformedOutputError, ProviderRefusalError) as exc:
            _log.warning(
                "structure_failed",
                agent=self.name,
                doc_id=document.id,
                reason=type(exc).__name__,
            )
            return None, exc.attempts if isinstance(exc, MalformedOutputError) else 1
        return result.value, result.attempts

    def _build(
        self,
        answer: DecisionAnswer,
        offering: Offering,
        document: Document,
        *,
        decision_id: str,
        at: datetime,
    ) -> StructuredDecision | StructureRejection:
        """Resolve the citation, verify it, then build the record.

        In that order on purpose. Building first and verifying afterwards would
        mean a fabricated citation is caught only after a `Decision` object
        exists, and an object that exists is an object something can write.
        """
        cited = _cited_span(answer, offering)
        if isinstance(cited, StructureRejection):
            return cited
        resolution = self._verifier.verify_spans((cited,), document)
        if not resolution.ok:
            # Asked before the quotation is judged, because the two failures
            # have different causes and only one of them is about the model. A
            # span this pipeline produced that no longer resolves means the
            # document changed underneath the run, and reporting that as a
            # fabricated quotation would blame the model for a re-ingestion.
            return StructureRejection(
                refusal=Refusal.SPAN_DOES_NOT_RESOLVE,
                detail=resolution.rejected[0].detail,
            )
        quote = answer.evidence_quote or ""
        verdict = self._verifier.verify_claim(quote, cited, document)
        if not verdict.ok:
            return _citation_rejection(offering, quote, cited, verdict.detail)

        stated = answer.decided_on is not None
        try:
            decision = Decision(
                id=DecisionId(decision_id),
                title=answer.title or "",
                chosen=answer.chosen or "",
                rejected=_rejected(answer),
                decision_maker=answer.decision_maker or "",
                decided_at=_decided_at(answer.decided_on, document),
                scope=answer.scope or DecisionScope.TEAM,
                impact=answer.impact or Impact.MEDIUM,
                status=answer.status or DecisionStatus.PROPOSED,
                span_id=cited.id,
                confidence=answer.confidence,
                created_by=self.name,
                created_at=at,
            )
        except ValidationError as exc:
            return StructureRejection(
                refusal=Refusal.INCOHERENT_RECORD,
                detail=f"the answer is not a decision this store can hold: {exc.error_count()} "
                f"problems, first: {exc.errors()[0]['msg']}",
            )
        return StructuredDecision(
            decision=decision,
            justification=Link.between(
                LinkType.JUSTIFIED_BY,
                decision.id,
                cited.id,
                rationale=answer.rationale or f"read from {cited.id}",
                confidence=answer.confidence,
                created_by=self.name,
                created_at=at,
                span_id=cited.id,
            ),
            quote=quote,
            quote_match=verdict.match,
            date_was_stated=stated,
        )


def _cited_span(answer: DecisionAnswer, offering: Offering) -> Span | StructureRejection:
    """Resolve the ordinal the model cited, or refuse it."""
    if answer.evidence_ordinal is None:
        return StructureRejection(
            refusal=Refusal.UNOFFERED_SPAN,
            detail="the answer claims a decision was found and cites no passage",
        )
    resolved = offering.resolve(answer.evidence_ordinal)
    if isinstance(resolved, Rejection):
        return StructureRejection(
            refusal=Refusal.UNOFFERED_SPAN,
            detail=resolved.reason,
            ordinal=resolved.ordinal,
        )
    return resolved


def _citation_rejection(
    offering: Offering, quote: str, cited: Span, detail: str
) -> StructureRejection:
    """Say which citation failure this was.

    The distinction ADR 0015 argues for: a quotation found in another passage
    the agent was shown is a model that read the material and mis-attributed,
    and a quotation found in none of them is a model that invented. Both are
    refused; only one of them means the material was read.
    """
    where = offering.where_quoted(quote, cited.id)
    if where is QuoteVerdict.IN_ANOTHER_OFFERED_SPAN:
        return StructureRejection(
            refusal=Refusal.MIS_ATTRIBUTED_QUOTE,
            detail=f"the quotation is in another passage that was offered, not {cited.id}",
        )
    if not quote.strip():
        return StructureRejection(
            refusal=Refusal.FABRICATED_QUOTE,
            detail="the answer claims a decision was found and quotes nothing",
        )
    return StructureRejection(refusal=Refusal.FABRICATED_QUOTE, detail=detail)


def _rejected(answer: DecisionAnswer) -> tuple[RejectedOption, ...]:
    """The alternatives, or the honest statement that there were none.

    A `Decision` needs at least one rejected option -- the project's own ADR
    convention, enforced rather than trusted. Where the model reports none, the
    record says the document recorded none, which is a fact worth keeping. The
    alternative is fabricating an option, and a fabricated alternative is a lie
    that reads exactly like evidence.
    """
    kept = tuple(
        RejectedOption(option=entry.option, reason=entry.reason)
        for entry in answer.rejected
        if entry.option.strip() and entry.reason.strip()
    )
    if kept:
        return kept
    return (
        RejectedOption(
            option="none recorded",
            reason=NO_ALTERNATIVE_RECORDED,
        ),
    )


def _decided_at(stated: date | None, document: Document) -> datetime:
    """When the decision was made, as an instant.

    A document states a date and not an instant, so midnight UTC is a
    convention. It is applied here rather than left to whoever reads the column,
    because two callers inventing two conventions is how a decision ends up a
    day apart from itself.

    Where nothing is stated, the document's ingestion time stands in: it is the
    earliest moment anyone can prove the decision existed by, which is a weaker
    claim than a date and a true one. `StructuredDecision.date_was_stated`
    carries the difference so nothing has to guess which it is looking at.
    """
    if stated is None:
        return document.ingested_at
    return datetime.combine(stated, time.min, tzinfo=UTC)
