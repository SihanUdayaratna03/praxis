"""Looking for an estimate's actual in documents other than its own.

Deferred from Phase 6 to here by `BACKLOG.md`, and the entry says why: at that
point `FusionBridge` did not exist, and the cross-document question belonged
with the phase that owns it.

## The cost against ADR 0015's budget, checked before this was built

The worry recorded in `BACKLOG.md` and priced as this phase's riskiest line item
was that cross-document matching costs ADR 0015's invariant -- *one offering,
one document* -- which exists so that an agent cannot assemble one claim out of
two sources without saying which. `praxis.agents.offering.offering_of` enforces
it by raising, not by convention, and `Offering.document` is a single id.

**It does not cost that invariant, because the invariant is about one call and
not about one estimate.** This module never widens a listing. It runs the
*existing* single-document matcher once per candidate document, so every call
still shows one document's spans, every ordinal still resolves inside the
document it was offered from, and `VerifierAgent` still re-reads the quotation
against that one document. What changes is how many times the question is asked,
which is a token cost in the column the eval table already has -- the same trade
ADR 0015's own "Accepted costs" section accepts for re-rendering a listing per
window. Assumption 2 there, `spans_per_offering <= 12`, is untouched: the cap is
per call and this module adds calls rather than passages.

So the honest summary is that the item fits the budget by not spending it, and
the part that would have spent it -- one listing spanning two documents -- is
still refused, still by `offering_of`, still with a `ValueError`.

## First resolution wins, and nothing keeps looking

The home document is tried first, then other documents in a deterministic order,
and **the walk stops at the first document that resolves the estimate**.

That is a real decision and the alternative was considered: keep going, collect
every document that claims an actual, and refuse the pairing when two disagree.
It was rejected twice over. It multiplies the model spend on the common case --
where the home document answers, which is what `candidates_for` is arranged to
make likely -- to buy a contradiction this corpus cannot produce, since the
generator states an estimate and its actual in one document. And detecting that
two stored records disagree is `ContradictionDetector`'s job, over records the
store already holds, rather than a second contradiction engine inside a matcher.

Nothing here calls a model directly. The calls belong to `OutcomeMatcher` and
are billed there; this module chooses which documents that agent is pointed at,
which is deterministic selection of exactly the kind `candidates_for` already
does one level down.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from praxis.agents.blocking import word_keys
from praxis.agents.extractor import IdAllocator
from praxis.agents.matcher import MatchedOutcome, OutcomeMatcher, UnmatchedEstimate
from praxis.domain.ids import DocumentId
from praxis.domain.records import Document, Estimate, Span
from praxis.obs.logging import get_logger

_log = get_logger(__name__)

CROSSDOC_NAME: Final = "CrossDocumentMatcher"
"""Recorded in logs and reports. Deliberately absent from both
`praxis.config.models.NON_LLM_AGENTS` and the routing table: this is a driver
rather than an agent. It makes no call of its own and it is not deterministic
either, because the thing it drives is not -- so claiming either set would be a
false statement about where the model spend happens. `OutcomeMatcher` holds the
route and the cost."""

DEFAULT_MAX_DOCUMENTS: Final = 3
"""How many documents one estimate may be asked about, home included.

A budget rather than a discovered constant, and it is the whole cost control in
this module: without it a store of `D` documents holding `E` estimates would be
`D x E` model calls for a question that is usually answered by the first one.
Three because the home document is tried first and answers most of the time, so
this buys two retries for the case where an estimate is stated in a plan and its
actual in a retro -- the shape the corpus does not plant and the one this exists
for."""

MIN_SHARED_WORDS: Final = 2
"""Discriminating words a *document* needs before it is worth a call.

Higher than the per-span threshold's job, and doing the same work one level up:
selection before a model, so a document that shares nothing with the estimate is
never paid for."""


@dataclass(frozen=True, slots=True)
class Attempt:
    """One document asked about one estimate, and what came back.

    Attributes:
        document_id: Which document was read.
        home: Whether this is the document the estimate itself was extracted
            from. Kept so a report can say whether the answer needed to leave
            home at all, which is the only number that says whether this module
            earns its cost.
        result: What the matcher said. A `MatchedOutcome` only for the last
            attempt, since the walk stops there.
    """

    document_id: DocumentId
    home: bool
    result: MatchedOutcome | UnmatchedEstimate

    @property
    def resolved(self) -> bool:
        """Whether this document answered the estimate."""
        return isinstance(self.result, MatchedOutcome)


@dataclass(frozen=True, slots=True)
class CrossDocumentMatch:
    """An estimate, the documents it was looked for in, and the answer taken.

    Attributes:
        estimate: The prediction being resolved.
        result: The outcome to store -- the resolving attempt's, or the home
            document's unresolved row when nothing resolved. Never `None`: an
            estimate always leaves this module with a row, which is ADR 0022's
            rule and the reason Phase 7's query is a join with no absences.
        attempts: Every document read, in the order they were read.
        resolved_in: The document that answered, or `None`.
    """

    estimate: Estimate
    result: MatchedOutcome | UnmatchedEstimate
    attempts: tuple[Attempt, ...]
    resolved_in: DocumentId | None

    @property
    def resolved(self) -> bool:
        """Whether anything resolved this estimate."""
        return self.resolved_in is not None

    @property
    def left_home(self) -> bool:
        """Whether the answer came from a document other than the estimate's own.

        The number that says whether this module paid for itself. A store where
        this is never true is one where the Phase 6 behaviour was sufficient.
        """
        return self.resolved and len(self.attempts) > 1

    @property
    def calls(self) -> int:
        """Model calls made across every document read.

        Summed over attempts rather than taken from the winner, because the
        retries are the cost this module adds and reporting only the successful
        call would hide exactly the number a reader needs to judge the budget.
        """
        return sum(attempt.result.calls for attempt in self.attempts)


class CrossDocumentMatcher:
    """Drives `OutcomeMatcher` across more than one document, one call each."""

    name: Final = CROSSDOC_NAME

    def __init__(
        self, matcher: OutcomeMatcher, *, max_documents: int = DEFAULT_MAX_DOCUMENTS
    ) -> None:
        """Wrap a matcher rather than reimplement one.

        Args:
            matcher: The single-document agent. Injected rather than
                constructed, so this module never names a provider and the
                model spend stays attributed to the agent that makes it.
            max_documents: How many documents one estimate may cost, home
                included.

        Raises:
            ValueError: if the budget is below one, which would refuse every
                estimate for a reason about configuration rather than evidence.
        """
        if max_documents < 1:
            message = f"an estimate must be asked about at least one document, got {max_documents}"
            raise ValueError(message)
        self._matcher = matcher
        self._max_documents = max_documents

    def match(  # noqa: PLR0913 -- the store's four maps plus the two audit fields
        self,
        estimate: Estimate,
        spans: Mapping[DocumentId, Sequence[Span]],
        documents: Mapping[DocumentId, Document],
        *,
        home: DocumentId,
        allocate: IdAllocator,
        at: datetime,
    ) -> CrossDocumentMatch:
        """Look for this estimate's actual, starting at home and widening.

        Args:
            estimate: The prediction to resolve.
            spans: Every document's spans, keyed by document.
            documents: Those documents, for re-reading citations against.
            home: The document the estimate was extracted from, tried first.
            allocate: How ids are obtained.
            at: When this ran. Timezone-aware, invariant 5.

        Returns:
            The answer, and every document read on the way to it.
        """
        order = documents_for(
            estimate, spans, home=home, limit=self._max_documents, documents=documents
        )
        attempts: list[Attempt] = []
        for document_id in order:
            result = self._matcher.match(
                estimate,
                spans.get(document_id, ()),
                documents[document_id],
                allocate=allocate,
                at=at,
            )
            attempts.append(Attempt(document_id, document_id == home, result))
            if isinstance(result, MatchedOutcome):
                _log.info(
                    "outcome_matched_across_documents",
                    agent=self.name,
                    estimate_id=estimate.id,
                    resolved_in=document_id,
                    home=document_id == home,
                    documents_read=len(attempts),
                )
                return CrossDocumentMatch(estimate, result, tuple(attempts), document_id)
        return _exhausted(estimate, tuple(attempts))


def documents_for(  # noqa: PLR0913 -- selection needs every one of these to be deterministic
    estimate: Estimate,
    spans: Mapping[DocumentId, Sequence[Span]],
    *,
    home: DocumentId,
    documents: Mapping[DocumentId, Document],
    limit: int = DEFAULT_MAX_DOCUMENTS,
    min_shared_words: int = MIN_SHARED_WORDS,
) -> tuple[DocumentId, ...]:
    """Which documents are worth asking about this estimate, best first.

    The same discipline `candidates_for` applies to passages, one level up:
    deterministic selection before a model, so a document that could not
    possibly answer is never paid for.

    **Home is always first and is never filtered out.** It is where an estimate
    and its actual are stated together most often, and dropping it on a
    vocabulary score would lose the commonest case to a heuristic -- the same
    reason `candidates_for` always offers the estimate's own span.

    Args:
        estimate: The prediction whose actual is being looked for.
        spans: Every document's spans, keyed by document.
        home: The estimate's own document.
        documents: The documents themselves. Only their keys are read; the
            mapping is taken so a document with spans but no record is never
            returned as something the caller must then look up and fail on.
        limit: How many documents may be read in total, home included.
        min_shared_words: Discriminating words a document needs to qualify.

    Returns:
        Document ids, home first and the rest by descending overlap with ties
        broken by id -- so two runs over one store read the same documents in
        the same order.
    """
    wanted = word_keys(estimate.subject, estimate.owner)
    scored = [
        (len(wanted & word_keys(*(span.text for span in document_spans))), document_id)
        for document_id, document_spans in spans.items()
        if document_id != home and document_id in documents
    ]
    ranked = sorted(
        ((shared, document_id) for shared, document_id in scored if shared >= min_shared_words),
        key=lambda pair: (-pair[0], pair[1]),
    )
    ordered = [home] if home in documents else []
    ordered.extend(document_id for _, document_id in ranked)
    return tuple(ordered[:limit])


def _exhausted(estimate: Estimate, attempts: tuple[Attempt, ...]) -> CrossDocumentMatch:
    """Nothing resolved it anywhere. The home row is the one that gets stored.

    The home document's unresolved outcome is taken rather than the last one
    read, because its `notes` name the stage that lost the estimate in the
    document a reader would look in first. How far the search actually went is
    on `attempts`, so the effort is reported without the stored row claiming a
    document nobody would expect it to mention.
    """
    home = next((attempt for attempt in attempts if attempt.home), None)
    chosen = home if home is not None else attempts[0]
    return CrossDocumentMatch(estimate, chosen.result, attempts, None)
