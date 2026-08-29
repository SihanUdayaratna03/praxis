"""OutcomeMatcher: the estimate, what actually happened, and the gap between.

The agent that closes the loop. Until something writes an `Outcome`, an
`Estimate` is a prediction nobody ever checked, `measured_in` binds no
identifier, and the two zeros the Phase 5 report had to explain stay zero.

Three stages, and only the middle one is a model -- the shape
`ContradictionDetector` established and for the same reason. Deterministic
selection proposes the passages worth reading, the model decides which of them
reports the actual for *this* estimate, and arithmetic does everything after
that. Which stage decided is carried out in the result, so a low match rate can
be traced to the stage that lost the pair rather than blamed on the model.

Four decisions in here, and each could have gone the easy way.

**`match_quality` is computed, never asked for.** Invariant 3 applied to a field
a model would happily fill in: "was this estimate close" is arithmetic on two
numbers, and a model's opinion of it would be irreproducible, unfalsifiable, and
indistinguishable in the table from a number. The prompt says in as many words
that the judgement is not being requested. See ADR 0021.

**An unmatched estimate becomes an `unresolved` `Outcome`, not a silence.**
`Outcome` was designed for this in Phase 1 -- "an unresolved outcome exists so
that estimates which never resolved stay visible in the calibration data instead
of being dropped, which is how a curve ends up flattering its estimator." It is
also what makes Phase 7's query a single join with no absences to account for.
See ADR 0022.

**Units are reconciled once, at write time.** `praxis.monitor.facts` records
the debt in a comment -- a mismatch is left unbound "until then" -- and this is
then. Every stored outcome carries its estimate's unit, so nothing downstream
ever converts, and the conversion that happened is written into `notes` rather
than inferred later. Across families -- points against hours, usd against weeks
-- there is no honest rate and the pairing is refused.

The arithmetic itself is not in this file. Candidate selection, unit conversion
and the band all live in `praxis.agents.reconciliation`, which imports no
provider and therefore cannot ask a model anything. That is invariant 3 made
structural rather than remembered: the module holding `quality_for` has no way
to reach an LLM, so the band cannot quietly become a judgement.

**Candidates come from the estimate's own document.** ADR 0015 makes an offering
one document's spans, so that an agent cannot assemble one claim out of two
sources without saying so, and that invariant is worth more than the outcomes it
costs. Cross-document resolution is `FusionBridge`'s, exactly as ADR 0016 says
of the cross-document half of the fusion edge, and it is recorded in
`BACKLOG.md` rather than half-built here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from praxis.agents.citation import CitationGate, Cited, Uncited
from praxis.agents.errors import Refusal
from praxis.agents.extractor import IdAllocator
from praxis.agents.offering import Offering, offering_of
from praxis.agents.reconciliation import (
    DEFAULT_MAX_CANDIDATES,
    WORKING_DAY_HOURS,
    WORKING_WEEK_DAYS,
    candidates_for,
    converted,
    quality_for,
)
from praxis.domain.enums import MatchQuality, RecordKind, Unit
from praxis.domain.ids import OutcomeId
from praxis.domain.records import Document, Estimate, Outcome, Span
from praxis.ingest.verifier import ClaimMatch, VerifierAgent
from praxis.llm.errors import MalformedOutputError, ProviderRefusalError
from praxis.llm.provider import LLMProvider
from praxis.llm.structured import REPAIR_ATTEMPTS, ask_for
from praxis.llm.types import LLMRequest, Message, MessageRole
from praxis.obs.logging import get_logger
from praxis.prompts.library import load

_log = get_logger(__name__)

MATCHER_NAME: Final = "OutcomeMatcher"
"""Spelled as `praxis.config.models` spells it, so the tier follows the name."""

MATCH_TASK: Final = "match_outcome"
"""Also the name of the prompt file this agent reads -- ADR 0014."""


class Unmatched(StrEnum):
    """Why an estimate has no resolved outcome, and which stage decided.

    Reported apart rather than merged into one count, because a low match rate
    means four different things depending on which of these it was, and only
    two of them are about the model at all.
    """

    NO_CANDIDATES = "no_candidates"
    """Deterministic selection put nothing in front of the model. No call was
    made and nothing was spent -- the pair was lost before the model existed."""

    MODEL_FOUND_NONE = "model_found_none"
    """The model read the passages and said none of them resolves this. The
    ordinary case: most estimates in a real corpus are never resolved."""

    NO_USABLE_ANSWER = "no_usable_answer"
    """The model refused or never satisfied the schema. A bad answer about this
    estimate, not a broken run."""

    UNCITED = "uncited"
    """The model named a passage or a quotation that did not survive the
    citation gate. The claim existed and the evidence did not."""

    INCOMPARABLE_UNITS = "incomparable_units"
    """The actual was stated in a unit that cannot honestly be converted into the
    estimate's. Refused rather than converted at an invented rate."""

    INCOHERENT_RECORD = "incoherent_record"
    """Well cited, and not a record this store can hold. The record model is the
    authority and it refused."""


class OutcomeAnswer(BaseModel):
    """What the model says about one estimate and one document's passages.

    Every field the answer may omit is nullable rather than absent, which is the
    asymmetry `praxis.llm.structured` documents: the structured dialect requires
    each declared property to be present, so "there is nothing here" is a null
    and not a missing key.

    There is deliberately no field for how good the estimate was. That is
    arithmetic and it is computed from the two quantities.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolved: bool = False
    passage_ordinal: int | None = None
    quote: str | None = None
    active_quantity: Decimal | None = None
    blocked_quantity: Decimal | None = None
    unit: Unit | None = None
    why: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


@dataclass(frozen=True, slots=True)
class MatchedOutcome:
    """An estimate resolved, and everything a person would check it against.

    Attributes:
        outcome: The record, ready to store, in the *estimate's* unit.
        estimate: The estimate it resolves, unchanged.
        quote: What the model quoted, kept for the report.
        quote_match: Exact, or the same text laid out differently.
        ratio: The larger active quantity over the smaller, which is what the
            band was computed from. `None` when one side is zero -- the ratio
            does not exist there and reporting one would be inventing it.
        converted_from: The unit the document stated, when it was not the
            estimate's. `None` when no conversion happened, so "was this number
            touched" is a field rather than an inference.
        calls: Model calls made, repairs included.
    """

    outcome: Outcome
    estimate: Estimate
    quote: str
    quote_match: ClaimMatch
    ratio: Decimal | None
    converted_from: Unit | None = None
    calls: int = 0

    @property
    def quality(self) -> MatchQuality:
        """How well the actual answered the estimate. Arithmetic, from the record."""
        return self.outcome.match_quality


@dataclass(frozen=True, slots=True)
class UnmatchedEstimate:
    """An estimate nothing resolved, flagged rather than dropped.

    Attributes:
        outcome: An `unresolved` `Outcome`. Carries no quantity and no
            resolution time -- the schema enforces that -- and exists so the
            estimate stays visible in the calibration data. See ADR 0022.
        estimate: The estimate it stands against.
        reason: Which stage lost it.
        detail: The evidence, for a person reading the row.
        refusal: The citation vocabulary's own name for it, where the loss was a
            citation failure. `None` otherwise, because most of these are not
            refusals at all -- "nothing here resolves this" is a correct answer.
        calls: Model calls made, if any.
    """

    outcome: Outcome
    estimate: Estimate
    reason: Unmatched
    detail: str
    refusal: Refusal | None = None
    calls: int = 0


class OutcomeMatcher:
    """Pairs an estimate with what actually happened. Extract tier, one call."""

    name: Final = MATCHER_NAME

    def __init__(
        self,
        provider: LLMProvider,
        *,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        max_attempts: int = REPAIR_ATTEMPTS,
    ) -> None:
        """Wire the agent to a provider it did not choose.

        Args:
            provider: The seam, from `provider_for`. The agent never names an
                implementation -- ADR 0005's first assumption.
            max_candidates: Passages per call. ADR 0015's ceiling.
            max_attempts: Attempts per call, repairs included.

        Raises:
            ValueError: if the listing would hold nothing, which would refuse
                every estimate for a reason about configuration.
        """
        if max_candidates < 1:
            message = f"a listing must hold at least one passage, got {max_candidates}"
            raise ValueError(message)
        self._provider = provider
        self._max_candidates = max_candidates
        self._max_attempts = max_attempts
        self._verifier = VerifierAgent()

    def match(
        self,
        estimate: Estimate,
        spans: Sequence[Span],
        document: Document,
        *,
        allocate: IdAllocator,
        at: datetime,
    ) -> MatchedOutcome | UnmatchedEstimate:
        """Find what actually happened to one estimate, or record that nothing did.

        Args:
            estimate: The prediction to resolve.
            spans: The spans of the document the estimate was read from.
            document: That document, for re-reading the citation against.
            allocate: How ids are obtained, so the agent holds no repository.
            at: When this ran. Timezone-aware, invariant 5.

        Returns:
            A resolved outcome, or an unresolved one naming what lost it.
            Never `None`: an estimate always leaves this agent with a row.

        Raises:
            ProviderError: for failures about the run rather than this estimate.
        """
        candidates = candidates_for(estimate, spans, limit=self._max_candidates)
        if not candidates:
            return self._unmatched(
                estimate,
                Unmatched.NO_CANDIDATES,
                "no passage in the document shares any vocabulary with the estimate",
                allocate=allocate,
                at=at,
            )
        offering = offering_of(candidates)
        answer, calls = self._answer_for(estimate, offering)
        if answer is None:
            return self._unmatched(
                estimate,
                Unmatched.NO_USABLE_ANSWER,
                "the model refused or never satisfied the schema",
                allocate=allocate,
                at=at,
                calls=calls,
            )
        if not answer.resolved:
            return self._unmatched(
                estimate,
                Unmatched.MODEL_FOUND_NONE,
                answer.why or "the model read the passages and found no actual for this estimate",
                allocate=allocate,
                at=at,
                calls=calls,
            )
        return self._resolved(
            estimate, answer, offering, document, allocate=allocate, at=at, calls=calls
        )

    def _resolved(  # noqa: PLR0913 -- every argument is a distinct input to one build
        self,
        estimate: Estimate,
        answer: OutcomeAnswer,
        offering: Offering,
        document: Document,
        *,
        allocate: IdAllocator,
        at: datetime,
        calls: int,
    ) -> MatchedOutcome | UnmatchedEstimate:
        """Turn a positive answer into a record, or say which check stopped it."""
        gate = CitationGate(
            offering=offering, document=document, verifier=self._verifier, subject="outcome"
        )
        evidence = gate.check(answer.passage_ordinal, answer.quote)
        if isinstance(evidence, Uncited):
            return self._unmatched(
                estimate,
                Unmatched.UNCITED,
                evidence.detail,
                allocate=allocate,
                at=at,
                calls=calls,
                refusal=evidence.refusal,
            )
        stated = answer.unit or estimate.unit
        active = converted(answer.active_quantity or Decimal(0), stated, estimate.unit)
        blocked = converted(answer.blocked_quantity or Decimal(0), stated, estimate.unit)
        if active is None or blocked is None:
            return self._unmatched(
                estimate,
                Unmatched.INCOMPARABLE_UNITS,
                f"an actual in {stated.value} cannot be compared to an estimate "
                f"in {estimate.unit.value} at any rate this agent could defend",
                allocate=allocate,
                at=at,
                calls=calls,
            )
        return self._record(
            estimate,
            answer,
            evidence,
            (active, blocked, stated),
            allocate=allocate,
            at=at,
            calls=calls,
        )

    def _record(  # noqa: PLR0913 -- every argument is a distinct input to one build
        self,
        estimate: Estimate,
        answer: OutcomeAnswer,
        evidence: Cited,
        measured: tuple[Decimal, Decimal, Unit],
        *,
        allocate: IdAllocator,
        at: datetime,
        calls: int,
    ) -> MatchedOutcome | UnmatchedEstimate:
        """Build the outcome, with the band computed rather than asked for."""
        active, blocked, stated = measured
        quality, ratio = quality_for(estimate.active_quantity, active)
        try:
            outcome = Outcome(
                id=OutcomeId(allocate(RecordKind.OUTCOME)),
                estimate_id=estimate.id,
                active_quantity=active,
                blocked_quantity=blocked,
                unit=estimate.unit,
                match_quality=quality,
                resolved_at=at,
                notes=_notes(answer, stated, estimate.unit),
                span_id=evidence.span.id,
                created_by=self.name,
                created_at=at,
            )
        except ValidationError as exc:
            return self._unmatched(
                estimate,
                Unmatched.INCOHERENT_RECORD,
                _first_problem(exc),
                allocate=allocate,
                at=at,
                calls=calls,
            )
        return MatchedOutcome(
            outcome=outcome,
            estimate=estimate,
            quote=evidence.quote,
            quote_match=evidence.match,
            ratio=ratio,
            converted_from=stated if stated is not estimate.unit else None,
            calls=calls,
        )

    def _unmatched(  # noqa: PLR0913 -- the row records who lost it and how
        self,
        estimate: Estimate,
        reason: Unmatched,
        detail: str,
        *,
        allocate: IdAllocator,
        at: datetime,
        calls: int = 0,
        refusal: Refusal | None = None,
    ) -> UnmatchedEstimate:
        """Write the estimate down as unresolved rather than letting it vanish.

        The row is the finding. An estimate with no outcome at all is invisible
        to every query Phase 7 will run, and invisible unresolved estimates are
        exactly how a calibration curve ends up flattering its estimator.
        """
        _log.info(
            "outcome_unmatched",
            agent=self.name,
            estimate_id=estimate.id,
            reason=reason.value,
        )
        return UnmatchedEstimate(
            outcome=Outcome(
                id=OutcomeId(allocate(RecordKind.OUTCOME)),
                estimate_id=estimate.id,
                unit=estimate.unit,
                match_quality=MatchQuality.UNRESOLVED,
                notes=f"{reason.value}: {detail}",
                created_by=self.name,
                created_at=at,
            ),
            estimate=estimate,
            reason=reason,
            detail=detail,
            refusal=refusal,
            calls=calls,
        )

    def _answer_for(
        self, estimate: Estimate, offering: Offering
    ) -> tuple[OutcomeAnswer | None, int]:
        """Ask about one estimate, returning `None` if the answer was unusable."""
        prompt = load(MATCH_TASK)
        request = LLMRequest(
            agent=self.name,
            task=MATCH_TASK,
            system=prompt.render(),
            messages=(Message(role=MessageRole.USER, content=_asked(estimate, offering)),),
            prompt_id=prompt.id,
            prompt_sha=prompt.sha256,
            metadata={"estimate_id": estimate.id, "passages": str(len(offering))},
        )
        try:
            result = ask_for(
                self._provider, request, OutcomeAnswer, max_attempts=self._max_attempts
            )
        except (MalformedOutputError, ProviderRefusalError) as exc:
            # A bad answer about this estimate, not a broken run.
            _log.warning(
                "outcome_match_unusable",
                agent=self.name,
                estimate_id=estimate.id,
                reason=type(exc).__name__,
            )
            return None, exc.attempts if isinstance(exc, MalformedOutputError) else 1
        return result.value, result.attempts


def _asked(estimate: Estimate, offering: Offering) -> str:
    """The estimate to resolve, and the passages that might resolve it."""
    return (
        f"## The estimate\n\n"
        f"Subject: {estimate.subject}\n"
        f"Owner: {estimate.owner}\n"
        f"Work class: {estimate.work_class}\n"
        f"Predicted: {estimate.active_quantity} {estimate.unit.value} of hands-on work, "
        f"{estimate.blocked_quantity} blocked\n\n"
        f"## Passages\n\n{offering.render()}\n"
    )


def _notes(answer: OutcomeAnswer, stated: Unit, target: Unit) -> str:
    """What a person reading this row needs, including any conversion applied.

    A converted quantity that does not say it was converted is a number nobody
    can read back to the document, so the convention is written down at the
    moment it is used rather than looked up in a constant afterwards.
    """
    said = (answer.why or "").strip() or "the passage reports the actual for this estimate"
    if stated is target:
        return said
    return (
        f"{said}. Stated in {stated.value} and converted to {target.value} at "
        f"{WORKING_DAY_HOURS} hours a day and {WORKING_WEEK_DAYS} days a week."
    )


def _first_problem(exc: ValidationError) -> str:
    """Quote the record model's own complaint rather than restating the rule."""
    return (
        f"the answer is not an outcome this store can hold: "
        f"{exc.error_count()} problems, first: {exc.errors()[0]['msg']}"
    )
