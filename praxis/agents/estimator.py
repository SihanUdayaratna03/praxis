"""EstimateExtractor: the estimates a document states, cited and verified.

The first agent of Half B, and the point at which this system stops being a
provenance tool and starts being a calibration one. Half A finds estimates too
-- `AssumptionExtractor` writes one wherever an assumption turns out to be a
quantified forward-looking claim, ADR 0016 -- but only ever *inside* an
assumption it was already paid to read. Most estimates in a corpus are not
inside an assumption. They are in a status update, a plan, a ticket, and nobody
called them estimates when they wrote them.

So this runs over spans rather than over decisions, on the scan tier, one call
per window: the same economics as `DecisionScout` and for the same reason. A
passage it skips is an estimate no later agent will ever calibrate against.

Three decisions worth stating, because each could have gone the easy way.

**It refuses actuals, loudly and by design.** "It actually took seven weeks" is
the single most confusable passage in this corpus -- a number, a unit, a subject,
and the wrong tense. An actual recorded as an estimate is not a missing row: it
is a row that makes an estimator look perfectly calibrated against their own
result, which is the most flattering wrong answer this system could produce.
The prompt says so in as many words and `OutcomeMatcher` is what those passages
are for.

**It never guesses a unit, and never converts one.** An `Outcome` is comparable
to its `Estimate` only when the units match, so four weeks quietly stored as
four hours would fail nowhere and make one estimator look forty times worse than
they are. A quantity with no unit is refused as `INCOHERENT_RECORD`, which is
the same call `AssumptionExtractor` makes and the same words, so the eval table
does not grow a second row for one defect.

**`work_class` is always `unclassified` here.** `praxis.agents.classifier` owns
that field and revises the record afterwards. Guessing it in this agent would
split one estimator's history into two classes with half the sample each, under
a `BiasDetective` that already refuses below `n = 5` -- and the guess would be
invisible, because a plausible class looks exactly like a right one.

Every citation goes through `praxis.agents.citation`, unchanged and unwrapped.
There is no second citation path in this phase and there was never going to be
one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from praxis.agents.citation import CitationGate, Cited, Uncited
from praxis.agents.errors import Refusal
from praxis.agents.extractor import NOT_STATED, UNCLASSIFIED, IdAllocator
from praxis.agents.offering import Offering, windows_of
from praxis.domain.enums import RecordKind, Unit
from praxis.domain.ids import EstimateId
from praxis.domain.records import Document, Estimate, Span
from praxis.ingest.verifier import ClaimMatch, VerifierAgent
from praxis.llm.errors import MalformedOutputError, ProviderRefusalError
from praxis.llm.provider import LLMProvider
from praxis.llm.structured import REPAIR_ATTEMPTS, ask_for
from praxis.llm.types import LLMRequest, Message, MessageRole
from praxis.obs.logging import get_logger
from praxis.prompts.library import load

_log = get_logger(__name__)

ESTIMATOR_NAME: Final = "EstimateExtractor"
"""Spelled as `praxis.config.models` spells it, so the tier follows the name."""

ESTIMATE_TASK: Final = "extract_estimates"
"""Also the name of the prompt file this agent reads -- ADR 0014."""

DEFAULT_WINDOW_SPANS: Final = 8
"""Spans per call, matching `DecisionScout`.

The same number for the same reason rather than by coincidence: ADR 0015's
second assumption is that an offering stays small enough for its ordinals to be
unambiguous, and a window of eight whole spans is what Phase 4 measured that
against. Changing it here and not there would make two agents disagree about
what "a listing" means while both cite into one.
"""

NO_SPLIT: Final = Decimal(0)
"""What `blocked_quantity` is when a passage states one number.

Zero rather than `None`: `Estimate.blocked_quantity` defaults to zero and the
total is derived from the two parts, so a passage that predicts no waiting and a
passage that is silent about waiting are the same prediction. The place the
distinction would matter is an `Outcome`, and there it is `None` and enforced.
"""


class EstimateSighting(BaseModel):
    """One estimate the model says a passage states.

    Every field the answer may omit is nullable rather than absent, which is the
    asymmetry `praxis.llm.structured` documents: the structured dialect requires
    each declared property to be present, so "the document does not say" is a
    null and not a missing key.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    passage_ordinal: int | None
    """Named to end in `ordinal` on purpose: `praxis.llm.synthesis` fills an
    integer field whose name carries that hint from the bracketed labels the
    prompt really presented, so an offline answer cites a passage that exists."""

    quote: str | None
    subject: str | None
    owner: str | None
    active_quantity: Decimal | None
    blocked_quantity: Decimal | None
    unit: Unit | None
    confidence: float = Field(ge=0.0, le=1.0)


class EstimateSightings(BaseModel):
    """Every estimate the model found in one window.

    An empty tuple is a real answer and the common one -- most passages in a
    corpus predict nothing -- which is why there is no minimum length here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    estimates: tuple[EstimateSighting, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractedEstimate:
    """A verified estimate and the evidence it was read from.

    Attributes:
        estimate: The record, ready to store, citing its own span.
        quote: What the model quoted, kept for the report.
        quote_match: Exact, or the same text laid out differently.
    """

    estimate: Estimate
    quote: str
    quote_match: ClaimMatch


@dataclass(frozen=True, slots=True)
class EstimateRejection:
    """An estimate that did not survive, and precisely which defect it was.

    Attributes:
        refusal: From the vocabulary the eval harness groups by. Never a new
            member: a defect spelled differently by two agents is a row that
            splits in the table for no reason anyone could act on.
        detail: The evidence against it. A verdict without this is a verdict
            nobody can check.
        ordinal: What the model cited, where it cited anything at all.
    """

    refusal: Refusal
    detail: str
    ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class EstimateExtraction:
    """What one document's pass produced, and what it cost.

    Attributes:
        found: Verified estimates, in the order their windows were read.
        rejections: Sightings that could not be turned into a record, each
            naming its defect.
        calls: Model calls made, repairs included.
        windows: Windows put in front of the model.
        blind_windows: Windows the model refused or never answered usably.
            Reported apart from a rejection because "found nothing" and "was
            never usefully asked" are different rows in an eval table.
    """

    found: tuple[ExtractedEstimate, ...] = ()
    rejections: tuple[EstimateRejection, ...] = ()
    calls: int = 0
    windows: int = 0
    blind_windows: int = 0

    @property
    def estimates(self) -> tuple[Estimate, ...]:
        """The records themselves, for a caller that only means to write them."""
        return tuple(found.estimate for found in self.found)

    @property
    def blind(self) -> bool:
        """Whether any part of this document went unread."""
        return self.blind_windows > 0


class EstimateExtractor:
    """Pulls the estimates out of a document. Scan tier, one call per window."""

    name: Final = ESTIMATOR_NAME

    def __init__(
        self,
        provider: LLMProvider,
        *,
        window_spans: int = DEFAULT_WINDOW_SPANS,
        max_attempts: int = REPAIR_ATTEMPTS,
    ) -> None:
        """Wire the agent to a provider it did not choose.

        Args:
            provider: The seam, from `provider_for`. The agent never names an
                implementation -- ADR 0005's first assumption.
            window_spans: Spans per call.
            max_attempts: Attempts per call, repairs included.

        Raises:
            ValueError: if the window is not positive, which would make a
                document silently produce no calls and no estimates.
        """
        if window_spans < 1:
            message = f"a window must hold at least one span, got {window_spans}"
            raise ValueError(message)
        self._provider = provider
        self._window_spans = window_spans
        self._max_attempts = max_attempts
        self._verifier = VerifierAgent()

    def extract(
        self,
        spans: tuple[Span, ...],
        document: Document,
        *,
        allocate: IdAllocator,
        at: datetime,
    ) -> EstimateExtraction:
        """Read one document and produce every estimate it states.

        Args:
            spans: The document's spans, in document order. All from one
                document; `offering_of` refuses a mixture.
            document: The document, for re-reading every citation against.
            allocate: How ids are obtained. Passed in rather than taken from a
                repository, so the agent stays ignorant of where records are
                written -- the seam `AssumptionExtractor` already takes.
            at: When this ran, for the records written. Timezone-aware,
                invariant 5.

        Returns:
            The verified estimates and the evidence of everything refused.

        Raises:
            ProviderError: for failures about the run rather than about this
                document -- transport, credentials, the cost ceiling.
            ValueError: if the spans do not all cite one document.
        """
        if not spans:
            return EstimateExtraction()

        found: list[ExtractedEstimate] = []
        rejections: list[EstimateRejection] = []
        calls = 0
        blind = 0
        windows = windows_of(spans, self._window_spans)
        for window in windows:
            sightings, attempts = self._sightings_in(window, document)
            calls += attempts
            if sightings is None:
                blind += 1
                continue
            gate = CitationGate(
                offering=window, document=document, verifier=self._verifier, subject="estimate"
            )
            for sighting in sightings.estimates:
                built = self._build(sighting, gate, allocate=allocate, at=at)
                if isinstance(built, ExtractedEstimate):
                    found.append(built)
                else:
                    rejections.append(built)

        return EstimateExtraction(
            found=tuple(found),
            rejections=tuple(rejections),
            calls=calls,
            windows=len(windows),
            blind_windows=blind,
        )

    def _sightings_in(
        self, window: Offering, document: Document
    ) -> tuple[EstimateSightings | None, int]:
        """Ask about one window, returning `None` if the answer was unusable."""
        prompt = load(ESTIMATE_TASK)
        request = LLMRequest(
            agent=self.name,
            task=ESTIMATE_TASK,
            system=prompt.render(),
            messages=(Message(role=MessageRole.USER, content=window.render()),),
            prompt_id=prompt.id,
            prompt_sha=prompt.sha256,
            metadata={"doc_id": document.id, "spans": str(len(window))},
        )
        try:
            result = ask_for(
                self._provider, request, EstimateSightings, max_attempts=self._max_attempts
            )
        except (MalformedOutputError, ProviderRefusalError) as exc:
            # A bad answer about this document, not a broken run.
            _log.warning(
                "estimate_scan_blind",
                agent=self.name,
                doc_id=document.id,
                reason=type(exc).__name__,
            )
            return None, exc.attempts if isinstance(exc, MalformedOutputError) else 1
        return result.value, result.attempts

    def _build(
        self,
        sighting: EstimateSighting,
        gate: CitationGate,
        *,
        allocate: IdAllocator,
        at: datetime,
    ) -> ExtractedEstimate | EstimateRejection:
        """Turn one sighting into a record, or name the defect that stopped it.

        The order is the citation first and the record second, and it is not
        arbitrary: an id is allocated only once the evidence has survived, so a
        refused sighting never burns a sequential `EST-` that would then be
        missing from the store with nothing explaining the gap.
        """
        evidence = gate.check(sighting.passage_ordinal, sighting.quote)
        if isinstance(evidence, Uncited):
            return EstimateRejection(
                refusal=evidence.refusal, detail=evidence.detail, ordinal=evidence.ordinal
            )
        if sighting.unit is None:
            # No default, deliberately -- the same refusal and the same words
            # `AssumptionExtractor` uses, because it is the same defect.
            return EstimateRejection(
                refusal=Refusal.INCOHERENT_RECORD,
                detail="a quantity with no unit cannot be compared to an outcome",
                ordinal=sighting.passage_ordinal,
            )
        return self._record(sighting, evidence, sighting.unit, allocate=allocate, at=at)

    def _record(
        self,
        sighting: EstimateSighting,
        evidence: Cited,
        unit: Unit,
        *,
        allocate: IdAllocator,
        at: datetime,
    ) -> ExtractedEstimate | EstimateRejection:
        """Build the record itself, letting the record model be the authority.

        `estimated_at` falls back to the run clock where the passage states no
        date. That is the same convention `DecisionStructurer` records for a
        decision with no date -- the earliest instant anyone can prove the claim
        existed by -- and it is stated here rather than inferred by whoever reads
        the column later.
        """
        try:
            estimate = Estimate(
                id=EstimateId(allocate(RecordKind.ESTIMATE)),
                subject=sighting.subject or evidence.quote,
                owner=sighting.owner or NOT_STATED,
                work_class=UNCLASSIFIED,
                active_quantity=sighting.active_quantity or NO_SPLIT,
                blocked_quantity=sighting.blocked_quantity or NO_SPLIT,
                unit=unit,
                confidence=sighting.confidence,
                estimated_at=at,
                span_id=evidence.span.id,
                created_by=self.name,
                created_at=at,
            )
        except ValidationError as exc:
            return EstimateRejection(
                refusal=Refusal.INCOHERENT_RECORD,
                detail=_first_problem(exc),
                ordinal=sighting.passage_ordinal,
            )
        return ExtractedEstimate(
            estimate=estimate, quote=evidence.quote, quote_match=evidence.match
        )


def _first_problem(exc: ValidationError) -> str:
    """Quote the record model's own complaint rather than restating the rule."""
    return (
        f"the answer is not an estimate this store can hold: "
        f"{exc.error_count()} problems, first: {exc.errors()[0]['msg']}"
    )
