"""AssumptionExtractor: what a decision rests on, and which of those are bets.

The reason tier, one call per decision. The scout notices and the structurer
reads; this asks the harder question, which is why it is the only Half A agent
routed above `EXTRACT`: an assumption is not a phrase to be located, it is a
claim the decision *depends on*, and the difference between "we chose
OpenSearch" and "the index stays under 50 GB" is not visible in the words.

Four decisions are worth stating.

**This writes the `estimated_as` edge, and `ARCHITECTURE.md` says `FusionBridge`
does.** The architecture still describes where the edge belongs in a finished
system; what moved is when it is first written, because Phase 4 is the phase
that has a corpus labelled with those edges and nothing that can produce one to
compare against. What this agent does is the *cheap* half of the recognition --
the model has the assumption and its quantity in front of it in one call, and
noticing that "the work finishes inside 4 weeks" is an effort claim costs
nothing extra there. `FusionBridge` still has to do the expensive half across
documents, over assumptions no single call ever saw together. See ADR 0016.

**An estimate is a second record with its own citation, not a field.** The
quantity is usually written somewhere else -- an ADR states the assumption
under one heading and the effort under another -- so the estimate carries its
own ordinal and its own quotation, and passes the same gate separately. An
estimate inheriting the assumption's span would be a citation nobody checked.

**A malformed estimate does not cost the assumption.** They are refused
independently and both refusals are reported, because the assumption is the
thing a decision rests on and losing it to salvage nothing would be the wrong
trade. `ExtractionRejection.lost` says which record was dropped, so the eval
table can report the two recalls apart.

**`work_class` falls back to `unclassified` rather than to a guess.**
`WorkClassifier` owns that field and arrives in Half B; calibration is per
estimator *per work class*, so a plausible-looking guess here would split one
estimator's history into two classes with half the sample each. An honest
`unclassified` is a row `BiasDetective` can exclude. A wrong one is not.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from praxis.agents.citation import CitationGate, Uncited
from praxis.agents.errors import ExtractionError, Refusal
from praxis.agents.offering import Offering, spread
from praxis.domain.enums import RecordKind, Unit
from praxis.domain.ids import AssumptionId, EstimateId
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Decision, Document, Estimate, Link, Span
from praxis.ingest.verifier import ClaimMatch, VerifierAgent
from praxis.llm.errors import MalformedOutputError, ProviderRefusalError
from praxis.llm.provider import LLMProvider
from praxis.llm.structured import REPAIR_ATTEMPTS, ask_for
from praxis.llm.types import LLMRequest, Message, MessageRole
from praxis.obs.logging import get_logger
from praxis.prompts.library import load

_log = get_logger(__name__)

EXTRACTOR_NAME: Final = "AssumptionExtractor"
"""Spelled as the routing table spells it, so the model tier follows the name."""

EXTRACT_TASK: Final = "extract_assumptions"
"""Also the name of the prompt file this agent reads -- ADR 0014."""

DEFAULT_BEHIND: Final = 3
DEFAULT_AHEAD: Final = 8
"""How much of the document either side of the decision is offered.

Wider than the structurer's two, and deliberately lopsided. A decision and its
alternatives sit together; a decision and its assumptions do not -- both
document shapes this project generates state the assumptions *after* the
decision, an ADR under a later heading and meeting notes under "what we are
carrying". Reaching equally in both directions would spend half the listing on
the title block and still stop short of the effort section.

The sum is the number that is constrained. ADR 0015's second assumption is that
an offering stays small enough for its ordinals to be unambiguous, written as
`spans_per_offering <= 12`, and `3 + 8 + 1` is exactly that. Widening either
part means breaching a recorded assumption rather than changing a constant.
"""

UNCLASSIFIED: Final = "unclassified"
"""What `work_class` says when the document did not make the kind of work clear.

Not a default in the sense of a guess. `WorkClassifier` owns this field in Half
B, and a row it can recognise as unclassified is one it can revise; a row that
says `data-migration` because that seemed likely is one nobody will ever look
at again.
"""

NOT_STATED: Final = "not stated"
"""What an owner or subject says when the document names none. `Estimate.owner`
is a calibration key and cannot be empty, and an invented name would put one
person's miss in another person's history."""

_CLASS_RE: Final = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
"""`WorkClass`'s own spelling, so this module cannot drift from the record."""

IdAllocator = Callable[[RecordKind], str]
"""How this agent gets ids without holding a repository.

`Repository.next_id` reads the highest ordinal in the store, so asking it twice
before writing anything returns one id twice. The caller passes something that
counts, and the agent stays ignorant of where records are written -- the same
seam `DecisionStructurer.structure` takes a `decision_id` through.
"""


@dataclass(frozen=True, slots=True)
class _Writing:
    """Where ids come from and when this run happened.

    One value rather than two arguments threaded through every builder. It is
    also the whole of what this agent knows about the store: an allocator it
    calls and a clock it was handed.
    """

    allocate: IdAllocator
    at: datetime


class AssumptionAnswer(BaseModel):
    """One assumption the model says a decision rests on.

    Every field the answer may omit is nullable rather than absent, which is the
    asymmetry `praxis.llm.structured` documents: the structured dialect requires
    each declared property to be present, so "the document does not say" is a
    null and not a missing key.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    statement: str | None
    predicate: str | None
    expiry_condition: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ordinal: int | None
    evidence_quote: str | None

    quantified: bool
    """Whether this assumption is a quantified forward-looking claim about
    effort -- an estimate wearing an assumption's clothes, and the relationship
    the whole product exists to find."""

    estimate_subject: str | None
    estimate_owner: str | None
    estimate_work_class: str | None
    estimate_active_quantity: Decimal | None
    estimate_blocked_quantity: Decimal | None
    estimate_unit: Unit | None
    estimate_ordinal: int | None
    """The passage the *quantity* is stated in, which is often not the one the
    assumption is stated in. Named to end in `ordinal` so the offline provider
    draws it from the bracketed labels -- ADR 0015."""

    estimate_quote: str | None


class AssumptionAnswers(BaseModel):
    """Everything one call says about one decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assumptions: tuple[AssumptionAnswer, ...]


@dataclass(frozen=True, slots=True)
class ExtractedEstimate:
    """A verified estimate and the fusion edge to the assumption it hides in.

    Attributes:
        estimate: The record, ready to store, citing its own span.
        estimated_as: The `estimated_as` edge, assumption to estimate.
        quote: What the model quoted for the quantity.
        quote_match: Whether the span said it exactly or laid out differently.
    """

    estimate: Estimate
    estimated_as: Link
    quote: str
    quote_match: ClaimMatch


@dataclass(frozen=True, slots=True)
class ExtractedAssumption:
    """A verified assumption, its evidence, and the decision that rests on it.

    Attributes:
        assumption: The record, ready to store.
        justification: A `justified_by` edge to the span it was read from.
        assumes: The `assumes` edge from the decision to this assumption. What
            makes a breach of this assumption reach a decision at all.
        quote: What the model quoted, kept for the report.
        quote_match: Exact, or the same text laid out differently.
        estimate: Present when the assumption is a quantified forward-looking
            claim and the quantity survived its own citation gate.
    """

    assumption: Assumption
    justification: Link
    assumes: Link
    quote: str
    quote_match: ClaimMatch
    estimate: ExtractedEstimate | None = None


@dataclass(frozen=True, slots=True)
class ExtractionRejection:
    """One record that did not enter the store, and why.

    Attributes:
        refusal: The defect, from the vocabulary the eval harness groups by.
        detail: The evidence against it.
        lost: Which record was dropped. An assumption whose estimate was refused
            reports the estimate here and still returns the assumption, so the
            two recalls can be read apart.
        ordinal: What the model cited, where it cited anything.
    """

    refusal: Refusal
    detail: str
    lost: RecordKind
    ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """What one decision's assumptions cost and what they produced.

    Attributes:
        assumptions: What survived, in the order the model reported it.
        rejections: What did not, one per record lost.
        calls: Model calls made, repairs included.
        blind: True when the model refused or never satisfied the schema, so
            this decision's assumptions were not found rather than absent.
    """

    assumptions: tuple[ExtractedAssumption, ...] = ()
    rejections: tuple[ExtractionRejection, ...] = ()
    calls: int = 0
    blind: bool = False

    @property
    def estimates(self) -> tuple[ExtractedEstimate, ...]:
        """The fusion half: assumptions that turned out to be estimates."""
        return tuple(
            extracted.estimate for extracted in self.assumptions if extracted.estimate is not None
        )


class AssumptionExtractor:
    """Reads one decision's assumptions out of the document it came from."""

    name: Final = EXTRACTOR_NAME

    def __init__(
        self,
        provider: LLMProvider,
        *,
        behind: int = DEFAULT_BEHIND,
        ahead: int = DEFAULT_AHEAD,
        max_attempts: int = REPAIR_ATTEMPTS,
        verifier: VerifierAgent | None = None,
    ) -> None:
        """Wire the agent to a provider it did not choose.

        Args:
            provider: The seam, from `provider_for`.
            behind: Spans before the decision's evidence to offer.
            ahead: Spans after it. Larger, because that is where assumptions
                are written.
            max_attempts: Attempts per call, repairs included.
            verifier: Supplied only by a test. There is one behaviour and it is
                deterministic, which is the whole point of the gate.

        Raises:
            ValueError: if either distance is negative.
        """
        if behind < 0 or ahead < 0:
            message = f"a distance cannot be negative, got behind={behind}, ahead={ahead}"
            raise ValueError(message)
        self._provider = provider
        self._behind = behind
        self._ahead = ahead
        self._max_attempts = max_attempts
        self._verifier = verifier if verifier is not None else VerifierAgent()

    def extract(
        self,
        decision: Decision,
        spans: Sequence[Span],
        document: Document,
        *,
        allocate: IdAllocator,
        at: datetime,
    ) -> ExtractionResult:
        """Find what one decision rests on, and which of those are estimates.

        Args:
            decision: The decision, already verified by the structurer. Its
                `span_id` is where the offering is centred.
            spans: The document's spans, in order.
            document: The document, for re-reading citations.
            allocate: Where record ids come from, so the agent holds no store.
            at: When this ran, for the records written. Timezone-aware.

        Returns:
            The assumptions that survived, and one rejection per record lost.

        Raises:
            ProviderError: for failures about the run rather than this document.
            ExtractionError: if the decision's span is not one of `spans`.
        """
        offering = self._context_for(decision, spans)
        cited_ordinal = next(
            entry.ordinal for entry in offering.entries if entry.span.id == decision.span_id
        )
        writing = _Writing(allocate=allocate, at=at)
        answers, calls = self._answers_for(offering, cited_ordinal, decision, document)
        if answers is None:
            return ExtractionResult(
                rejections=(
                    ExtractionRejection(
                        refusal=Refusal.NO_USABLE_ANSWER,
                        detail="the model refused or never satisfied the schema",
                        lost=RecordKind.ASSUMPTION,
                    ),
                ),
                calls=calls,
                blind=True,
            )
        gate = CitationGate(
            offering=offering,
            document=document,
            verifier=self._verifier,
            subject="assumption",
        )
        kept: list[ExtractedAssumption] = []
        refused: list[ExtractionRejection] = []
        for answer in answers.assumptions:
            built = self._build(answer, gate, decision, writing)
            if isinstance(built, ExtractionRejection):
                refused.append(built)
                continue
            extracted, estimate_rejection = built
            kept.append(extracted)
            if estimate_rejection is not None:
                refused.append(estimate_rejection)
        return ExtractionResult(assumptions=tuple(kept), rejections=tuple(refused), calls=calls)

    def _context_for(self, decision: Decision, spans: Sequence[Span]) -> Offering:
        """Offer the decision's evidence with as much of the document as reach allows."""
        index = next(
            (position for position, span in enumerate(spans) if span.id == decision.span_id),
            None,
        )
        if index is None:
            message = f"{decision.span_id} is not one of the {len(spans)} spans offered"
            raise ExtractionError(message)
        return spread(spans, index, behind=self._behind, ahead=self._ahead)

    def _answers_for(
        self, offering: Offering, cited_ordinal: int, decision: Decision, document: Document
    ) -> tuple[AssumptionAnswers | None, int]:
        """Ask about one decision, returning `None` if the answer was unusable."""
        prompt = load(EXTRACT_TASK)
        request = LLMRequest(
            agent=self.name,
            task=EXTRACT_TASK,
            system=prompt.render(chosen=decision.chosen, decision_ordinal=cited_ordinal),
            messages=(Message(role=MessageRole.USER, content=offering.render()),),
            prompt_id=prompt.id,
            prompt_sha=prompt.sha256,
            metadata={"doc_id": document.id, "decision_id": decision.id},
        )
        try:
            result = ask_for(
                self._provider, request, AssumptionAnswers, max_attempts=self._max_attempts
            )
        except (MalformedOutputError, ProviderRefusalError) as exc:
            _log.warning(
                "extraction_failed",
                agent=self.name,
                doc_id=document.id,
                decision_id=decision.id,
                reason=type(exc).__name__,
            )
            return None, exc.attempts if isinstance(exc, MalformedOutputError) else 1
        return result.value, result.attempts

    def _build(
        self,
        answer: AssumptionAnswer,
        gate: CitationGate,
        decision: Decision,
        writing: _Writing,
    ) -> tuple[ExtractedAssumption, ExtractionRejection | None] | ExtractionRejection:
        """Verify one assumption's citation, then build it and any estimate."""
        evidence = gate.check(answer.evidence_ordinal, answer.evidence_quote)
        if isinstance(evidence, Uncited):
            return _lost(evidence, RecordKind.ASSUMPTION)
        try:
            assumption = Assumption(
                id=AssumptionId(writing.allocate(RecordKind.ASSUMPTION)),
                statement=answer.statement or "",
                predicate=answer.predicate or "",
                expiry_condition=answer.expiry_condition or "",
                span_id=evidence.span.id,
                confidence=answer.confidence,
                created_by=self.name,
                created_at=writing.at,
            )
        except ValidationError as exc:
            return ExtractionRejection(
                refusal=Refusal.INCOHERENT_RECORD,
                detail=_first_problem(exc, "an assumption"),
                lost=RecordKind.ASSUMPTION,
                ordinal=answer.evidence_ordinal,
            )
        estimate, rejection = self._estimate_for(answer, gate, assumption, decision, writing)
        return (
            ExtractedAssumption(
                assumption=assumption,
                justification=Link.between(
                    LinkType.JUSTIFIED_BY,
                    assumption.id,
                    evidence.span.id,
                    rationale=f"stated in {evidence.span.id}",
                    confidence=answer.confidence,
                    created_by=self.name,
                    created_at=writing.at,
                    span_id=evidence.span.id,
                ),
                assumes=Link.between(
                    LinkType.ASSUMES,
                    decision.id,
                    assumption.id,
                    rationale=assumption.statement,
                    confidence=answer.confidence,
                    created_by=self.name,
                    created_at=writing.at,
                    span_id=evidence.span.id,
                ),
                quote=evidence.quote,
                quote_match=evidence.match,
                estimate=estimate,
            ),
            rejection,
        )

    def _estimate_for(
        self,
        answer: AssumptionAnswer,
        gate: CitationGate,
        assumption: Assumption,
        decision: Decision,
        writing: _Writing,
    ) -> tuple[ExtractedEstimate | None, ExtractionRejection | None]:
        """Build the estimate hiding in an assumption, or say why there is none.

        Returns `(None, None)` when the model said the assumption is not a
        quantified forward-looking claim. That is not a refusal: most
        assumptions are about the world rather than about effort, and counting
        them as losses would make the fusion recall meaningless.
        """
        if not answer.quantified:
            return None, None
        evidence = gate.check(answer.estimate_ordinal, answer.estimate_quote)
        if isinstance(evidence, Uncited):
            return None, _lost(evidence, RecordKind.ESTIMATE)
        if answer.estimate_unit is None:
            # No default, deliberately. An `Outcome` is comparable to its
            # `Estimate` only when the units match, and four weeks silently
            # recorded as four hours would not fail anywhere -- it would just
            # make one estimator look forty times worse than they are.
            return None, ExtractionRejection(
                refusal=Refusal.INCOHERENT_RECORD,
                detail="a quantity with no unit cannot be compared to an outcome",
                lost=RecordKind.ESTIMATE,
                ordinal=answer.estimate_ordinal,
            )
        try:
            estimate = Estimate(
                id=EstimateId(writing.allocate(RecordKind.ESTIMATE)),
                subject=answer.estimate_subject or assumption.statement,
                owner=answer.estimate_owner or NOT_STATED,
                work_class=_work_class(answer.estimate_work_class),
                active_quantity=answer.estimate_active_quantity or Decimal(0),
                blocked_quantity=answer.estimate_blocked_quantity or Decimal(0),
                unit=answer.estimate_unit,
                confidence=answer.confidence,
                conditions=(assumption.statement,),
                estimated_at=decision.decided_at,
                span_id=evidence.span.id,
                created_by=self.name,
                created_at=writing.at,
            )
        except ValidationError as exc:
            return None, ExtractionRejection(
                refusal=Refusal.INCOHERENT_RECORD,
                detail=_first_problem(exc, "an estimate"),
                lost=RecordKind.ESTIMATE,
                ordinal=answer.estimate_ordinal,
            )
        return (
            ExtractedEstimate(
                estimate=estimate,
                estimated_as=Link.between(
                    LinkType.ESTIMATED_AS,
                    assumption.id,
                    estimate.id,
                    rationale="a quantified forward-looking claim about effort",
                    confidence=answer.confidence,
                    created_by=self.name,
                    created_at=writing.at,
                    span_id=evidence.span.id,
                ),
                quote=evidence.quote,
                quote_match=evidence.match,
            ),
            None,
        )


def _lost(evidence: Uncited, kind: RecordKind) -> ExtractionRejection:
    """Carry a citation refusal out as the record it cost."""
    return ExtractionRejection(
        refusal=evidence.refusal,
        detail=evidence.detail,
        lost=kind,
        ordinal=evidence.ordinal,
    )


def _work_class(stated: str | None) -> str:
    """Normalise the model's answer, or say the work was not classified.

    Only spelling is repaired: `Data Migration` and `data migration` are the
    same class written by two models, and letting both through would halve a
    sample `BiasDetective` already refuses to answer below `n = 5`. Anything
    that is not a run of words is not repaired into one -- it is `unclassified`,
    which Half B can revise.
    """
    if stated is None:
        return UNCLASSIFIED
    kebab = "-".join(stated.lower().split())
    return kebab if _CLASS_RE.fullmatch(kebab) else UNCLASSIFIED


def _first_problem(exc: ValidationError, what: str) -> str:
    """Quote the record model's own complaint rather than restating the rule."""
    return (
        f"the answer is not {what} this store can hold: "
        f"{exc.error_count()} problems, first: {exc.errors()[0]['msg']}"
    )
