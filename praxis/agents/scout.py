"""DecisionScout: which passages record a decision, cheaply and generously.

The first agent of Half A, and the only one whose job is to be *wrong in one
direction*. It runs on the scan tier over every span in the corpus, which makes
it the highest-volume call in the pipeline and the one whose cost per document
is a reported metric. What it buys is recall: a passage it skips is a decision
no later agent will ever see, while a passage it marks wrongly costs one
structurer call and is discarded. Those are not symmetric errors and the prompt
says so in as many words.

Three things it deliberately does not do.

**It does not return `Decision` records.** A candidate is a span and a reason to
look harder at it. Building the record is `DecisionStructurer`'s call on the
extract tier, because filling a schema and noticing that something happened are
different questions and pricing them the same wastes the cheap tier.

**It does not quote.** It answers with ordinals and nothing else, which is why
its citations cannot be wrong in the way every later agent's can -- there is no
quotation to mis-attribute. A candidate is either a passage that was offered or
it is refused. That is ADR 0015 at its strongest, and it is the reason this
agent's citation accuracy is not an interesting number.

**It does not deduplicate across windows.** A span appears in exactly one
window, so it cannot be marked twice; within a window a repeated ordinal is
dropped, because two candidates over one span are one candidate and the
structurer would otherwise be paid to answer the same question twice.

A window that produces no usable answer is treated as a bad answer about *this
document* -- the same rule `SegmenterAgent` follows -- and yields no candidates
rather than ending the run. There is no floor to degrade to here and that is
correct: the floor for "which passages hold a decision" would be "all of them",
which is not a cheaper answer, it is a more expensive one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from praxis.agents.errors import Refusal
from praxis.agents.offering import Offering, Rejection, windows_of
from praxis.domain.records import Span
from praxis.llm.errors import MalformedOutputError, ProviderRefusalError
from praxis.llm.provider import LLMProvider
from praxis.llm.structured import REPAIR_ATTEMPTS, ask_for
from praxis.llm.types import LLMRequest, Message, MessageRole
from praxis.obs.logging import get_logger
from praxis.prompts.library import load

_log = get_logger(__name__)

SCOUT_NAME: Final = "DecisionScout"
"""Spelled as the routing table spells it, so the model tier follows the name."""

SCAN_TASK: Final = "scan_for_decisions"
"""Also the name of the prompt file this agent reads -- ADR 0014."""

DEFAULT_WINDOW_SPANS: Final = 8
"""How many spans are put in front of the model at once.

Smaller than the segmenter's forty-block window, and for a different reason: the
segmenter reads short blocks, this reads whole spans, and ADR 0015's second
assumption is that a listing stays small enough for its ordinals to be
unambiguous. Eight also keeps one document to a couple of calls on the cheapest
tier, which is what the cost-per-document column is about.
"""


class DecisionSighting(BaseModel):
    """One passage the model says records a decision.

    `why` is not stored. It is asked for because a model that emits only a
    number and a score can emit them without having read anything, and naming
    what makes the passage a decision is the cheapest way to make the answer
    depend on the text. It stays visible in the trace.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    passage_ordinal: int
    """Named to end in `ordinal` on purpose: `praxis.llm.synthesis` fills an
    integer field whose name carries that hint from the bracketed labels the
    prompt really presented, so an offline answer cites a passage that exists."""

    label: str
    why: str
    confidence: float = Field(ge=0.0, le=1.0)


class DecisionSightings(BaseModel):
    """Every passage the model marked in one window.

    An empty list is a real answer and the common one -- most passages in a
    corpus hold no decision -- which is why this has no minimum length. The
    segmenter's plan does, and the difference is not an inconsistency: a window
    of blocks always belongs to *some* grouping, and a window of spans need not
    contain a decision.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sightings: tuple[DecisionSighting, ...] = ()


@dataclass(frozen=True, slots=True)
class Candidate:
    """A span worth asking the structurer about.

    Attributes:
        span: The passage. Resolved from an ordinal, so it is a span that was
            really offered.
        label: What the scout thinks was decided, in a few words. Carried for
            the trace and the report, never stored.
        why: The scout's one-sentence case.
        confidence: How sure the scout was. Not a filter here -- the whole point
            of this agent is that low confidence is still worth passing on.
    """

    span: Span
    label: str
    why: str
    confidence: float


@dataclass(frozen=True, slots=True)
class CandidateRejection:
    """A sighting that could not be turned into a candidate, and why."""

    ordinal: int
    refusal: Refusal
    detail: str


@dataclass(frozen=True, slots=True)
class ScoutReport:
    """What one document's scan produced, and what it cost.

    Attributes:
        candidates: Passages worth structuring, in document order.
        rejections: Sightings that named a passage the model was not shown.
        calls: Model calls made, repairs included.
        windows: Windows put in front of the model.
        blind_windows: Windows the model refused or never answered usably. Not
            "degraded": there is no floor to fall back to, so these are windows
            whose decisions, if any, were not found.
    """

    candidates: tuple[Candidate, ...] = ()
    rejections: tuple[CandidateRejection, ...] = ()
    calls: int = 0
    windows: int = 0
    blind_windows: int = 0

    @property
    def blind(self) -> bool:
        """Whether any part of this document went unread."""
        return self.blind_windows > 0


class DecisionScout:
    """Marks the passages that record a decision. Cheap, generous, no quotations."""

    name: Final = SCOUT_NAME

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
                document silently produce no calls and no candidates.
        """
        if window_spans < 1:
            message = f"a window must hold at least one span, got {window_spans}"
            raise ValueError(message)
        self._provider = provider
        self._window_spans = window_spans
        self._max_attempts = max_attempts

    def scan(self, spans: tuple[Span, ...]) -> ScoutReport:
        """Mark the passages in one document that record a decision.

        Args:
            spans: The document's spans, in document order. All from one
                document; `offering_of` refuses a mixture.

        Returns:
            The candidates and the evidence of how they were arrived at.

        Raises:
            ProviderError: for failures about the run rather than about this
                document -- transport, credentials, the cost ceiling.
            ValueError: if the spans do not all cite one document.
        """
        if not spans:
            return ScoutReport()

        candidates: list[Candidate] = []
        rejections: list[CandidateRejection] = []
        calls = 0
        blind = 0
        windows = windows_of(spans, self._window_spans)
        for window in windows:
            sightings, attempts = self._sightings_in(window)
            calls += attempts
            if sightings is None:
                blind += 1
                continue
            found, refused = _resolved(sightings, window)
            candidates.extend(found)
            rejections.extend(refused)

        return ScoutReport(
            candidates=tuple(candidates),
            rejections=tuple(rejections),
            calls=calls,
            windows=len(windows),
            blind_windows=blind,
        )

    def _sightings_in(self, window: Offering) -> tuple[DecisionSightings | None, int]:
        """Ask about one window, returning `None` if the answer was unusable."""
        prompt = load(SCAN_TASK)
        request = LLMRequest(
            agent=self.name,
            task=SCAN_TASK,
            system=prompt.render(),
            messages=(Message(role=MessageRole.USER, content=window.render()),),
            prompt_id=prompt.id,
            prompt_sha=prompt.sha256,
            metadata={"doc_id": window.doc_id, "spans": str(len(window))},
        )
        try:
            result = ask_for(
                self._provider, request, DecisionSightings, max_attempts=self._max_attempts
            )
        except (MalformedOutputError, ProviderRefusalError) as exc:
            # A bad answer about this document, not a broken run.
            _log.warning(
                "scan_blind",
                agent=self.name,
                doc_id=window.doc_id,
                reason=type(exc).__name__,
            )
            return None, exc.attempts if isinstance(exc, MalformedOutputError) else 1
        return result.value, result.attempts


def _resolved(
    sightings: DecisionSightings, window: Offering
) -> tuple[list[Candidate], list[CandidateRejection]]:
    """Turn one window's sightings into candidates, dropping the unresolvable.

    Sorted by ordinal so that two runs over one document produce candidates in
    the same order whatever order the model listed them in -- the same stability
    argument `SegmenterAgent._honourable` makes, and for the same reason: the
    structurer is called once per candidate and a different order is a different
    sequence of prompt hashes.
    """
    found: list[Candidate] = []
    refused: list[CandidateRejection] = []
    seen: set[int] = set()
    for sighting in sorted(sightings.sightings, key=lambda s: s.passage_ordinal):
        if sighting.passage_ordinal in seen:
            continue
        seen.add(sighting.passage_ordinal)
        resolved = window.resolve(sighting.passage_ordinal)
        if isinstance(resolved, Rejection):
            refused.append(
                CandidateRejection(
                    ordinal=resolved.ordinal,
                    refusal=Refusal.UNOFFERED_SPAN,
                    detail=resolved.reason,
                )
            )
            continue
        found.append(
            Candidate(
                span=resolved,
                label=sighting.label,
                why=sighting.why,
                confidence=sighting.confidence,
            )
        )
    return found, refused
