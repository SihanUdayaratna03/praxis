"""Half B over a store: extract, classify, match, in that order and for a reason.

The counterpart to `praxis.agents.extraction`, and deliberately the same shape:
a hand-rolled sequence with the stages in the only order that works, reported
rather than logged, degrading about one record rather than about the run.

**The order is the argument again.** Extraction runs first because there is
nothing to classify or match until an `Estimate` exists. Classification runs
second because `work_class` is on the estimate and an outcome does not carry
one -- so classifying before matching means the matched pair is already on the
axis Phase 7 groups by, and classifying after would leave a window in which the
store holds resolved outcomes nobody can group. Matching runs last because it is
the only stage on the extract tier and the only one whose input is every
estimate the store holds rather than every document.

**A second pass costs nothing, and the three reasons are different.** A document
this agent already read is skipped, because a re-read would allocate a second
`EST-` for one passage. An estimate that already carries a class is skipped
because there is nothing to decide, and one this agent has already failed at is
skipped off the *audit trail*, because otherwise every unclassifiable estimate
would cost a call on every run forever. An estimate that already has an
`Outcome` -- resolved or not -- is skipped, because the unresolved row is itself
the record that the question was asked and answered. None of the three needs a
column; all three are questions the store can already answer, which is the same
argument `praxis.monitor.run` makes about writing only on a change.

**It classifies estimates it did not write.** `AssumptionExtractor` has been
writing `unclassified` estimates since Phase 4, and they are read here along
with this phase's own. Half B improving Half A's records without Half A changing
is the point of the field having been left revisable rather than guessed.

**One bad document does not stop a run**, and neither does one bad estimate.
Same rule as Half A, same difference: a failure about the *run* -- the cost
ceiling, a store that will not write -- is not caught here and stops everything.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from praxis.agents.classifier import (
    CLASSIFIER_NAME,
    Classification,
    ClassificationRefusal,
    WorkClassifier,
    is_classified,
)
from praxis.agents.estimator import ESTIMATOR_NAME, EstimateExtractor, ExtractedEstimate
from praxis.agents.extraction import StoreAllocator, spans_by_document
from praxis.agents.matcher import MatchedOutcome, OutcomeMatcher, UnmatchedEstimate
from praxis.agents.results import Refused, Stage
from praxis.domain.enums import RecordKind
from praxis.domain.ids import DocumentId
from praxis.domain.records import Document, Estimate, Outcome, Span
from praxis.llm.provider import LLMProvider
from praxis.obs.logging import get_logger
from praxis.store.repository import Repository

_log = get_logger(__name__)

RATE_PLACES: Final = Decimal("0.0001")
"""How precisely a rate is reported. Four places, as `praxis.eval.metrics` uses,
so the run's own number and the graded one cannot disagree by rounding."""

ESTIMATION_ACTOR: Final = "estimation"
"""The actor on the audit rows this pass writes for a new record.

Not the agent that produced it: the audit trail records who *wrote* it, and what
wrote these is the pass running all three. Each record carries the agent that
produced it in `created_by`, so both questions have an answer and neither is
inferred from the other.
"""


@dataclass(frozen=True, slots=True)
class DocumentEstimates:
    """What one document's estimate pass produced.

    Attributes:
        document: The document read.
        found: Estimates written, each citing its own verified span.
        refused: Sightings lost, at the stage that lost them.
        calls: Model calls made, repairs included.
        blind_windows: Windows the model never answered usably. Estimates in
            those passages were not found rather than absent.
        already_read: Whether this pass had already read this document.
    """

    document: Document
    found: tuple[ExtractedEstimate, ...] = ()
    refused: tuple[Refused, ...] = ()
    calls: int = 0
    blind_windows: int = 0
    already_read: bool = False


@dataclass(frozen=True, slots=True)
class EstimationRun:
    """Everything one Half B pass over a store did.

    Attributes:
        documents: One result per document, in id order.
        classified: Estimates moved onto the calibration axis.
        classification_refusals: Estimates the classifier said nothing usable
            about. Distinct from one it answered `unclassified` for, which is a
            `Classification` that simply did not move.
        matched: Estimates resolved by an outcome.
        unmatched: Estimates nothing resolved -- each still carrying an
            `unresolved` `Outcome`, because a dropped estimate is invisible to
            every query Phase 7 will run.
        already_classified: Estimates skipped as already on the axis.
        already_attempted: Estimates skipped because this pass has failed to
            classify them before. Paying the tier for that again every run is
            the cost this check exists to refuse.
        already_matched: Estimates skipped because an outcome already stands
            against them, resolved or not.
        classifier_calls: Model calls the classification stage made.
        matcher_calls: Model calls the matching stage made.
    """

    documents: tuple[DocumentEstimates, ...] = ()
    classified: tuple[Classification, ...] = ()
    classification_refusals: tuple[ClassificationRefusal, ...] = ()
    matched: tuple[MatchedOutcome, ...] = ()
    unmatched: tuple[UnmatchedEstimate, ...] = ()
    already_classified: int = 0
    already_attempted: int = 0
    already_matched: int = 0
    classifier_calls: int = 0
    matcher_calls: int = 0

    @property
    def estimates(self) -> int:
        """Estimates written by this pass."""
        return sum(len(result.found) for result in self.documents)

    @property
    def outcomes(self) -> int:
        """Outcome rows written, resolved and unresolved together."""
        return len(self.matched) + len(self.unmatched)

    @property
    def refused(self) -> tuple[Refused, ...]:
        """Everything lost, across every document, in one sequence.

        One sequence rather than three, because the eval harness groups by
        `Refusal` and a defect reported under two names is a row that splits for
        no reason anyone could act on.
        """
        return tuple(lost for result in self.documents for lost in result.refused)

    @property
    def calls(self) -> int:
        """Every model call this pass made, across all three stages."""
        extraction = sum(result.calls for result in self.documents)
        return extraction + self.classifier_calls + self.matcher_calls

    @property
    def blind_windows(self) -> int:
        """Windows no usable answer came back about."""
        return sum(result.blind_windows for result in self.documents)

    @property
    def match_rate(self) -> Decimal:
        """Of the estimates this pass asked about, how many an outcome resolved.

        Reported as a rate because the absolute number says nothing without the
        denominator: two matches out of two is a different claim from two out of
        forty, and only one of them is good news.

        `Decimal` rather than `float`, matching `praxis.eval.metrics`: this
        number is printed beside the graded one and two rates that round
        differently would read as a disagreement between the run and its grader.

        Quantized in the empty case too, so that a store with nothing to match
        prints `0.0000` rather than `0`. One number with two renderings is a
        number a report cannot be pattern-matched against, and this one is read
        off a terminal.
        """
        asked = self.outcomes
        if not asked:
            return Decimal(0).quantize(RATE_PLACES)
        return (Decimal(len(self.matched)) / Decimal(asked)).quantize(RATE_PLACES)


class EstimationPipeline:
    """Runs a store from documents to estimates, classes and outcomes."""

    def __init__(
        self,
        repository: Repository,
        provider: LLMProvider,
        *,
        extractor: EstimateExtractor | None = None,
        classifier: WorkClassifier | None = None,
        matcher: OutcomeMatcher | None = None,
    ) -> None:
        """Wire the pipeline to a store and a provider it did not choose.

        Args:
            repository: An open, migrated store holding documents and spans.
            provider: The seam, from `provider_for`.
            extractor: Supplied only to vary its window size or attempt budget.
            classifier: Likewise.
            matcher: Likewise.
        """
        self._repository = repository
        self._extractor = extractor if extractor is not None else EstimateExtractor(provider)
        self._classifier = classifier if classifier is not None else WorkClassifier(provider)
        self._matcher = matcher if matcher is not None else OutcomeMatcher(provider)

    def run(self, *, at: datetime | None = None, run_id: str | None = None) -> EstimationRun:
        """Extract, classify and match over the whole store, in that order.

        Args:
            at: When this ran. Defaults to now, in UTC. Timezone-aware,
                invariant 5.
            run_id: Recorded on every audit row this writes.

        Returns:
            What was written, skipped and lost at each of the three stages.

        Raises:
            ProviderError: for failures about the run rather than one record.
            StoreError: if a write is refused.
        """
        moment = at if at is not None else datetime.now(UTC)
        documents = self._extract(at=moment, run_id=run_id)
        classified, refusals, skipped, calls = self._classify(at=moment, run_id=run_id)
        matched, unmatched, already, matcher_calls = self._match(at=moment, run_id=run_id)
        run = EstimationRun(
            documents=documents,
            classified=tuple(classified),
            classification_refusals=tuple(refusals),
            matched=tuple(matched),
            unmatched=tuple(unmatched),
            already_classified=skipped[0],
            already_attempted=skipped[1],
            already_matched=already,
            classifier_calls=calls,
            matcher_calls=matcher_calls,
        )
        _log.info(
            "estimation_run",
            documents=len(documents),
            estimates=run.estimates,
            classified=len(run.classified),
            matched=len(run.matched),
            unmatched=len(run.unmatched),
            refused=len(run.refused),
            calls=run.calls,
        )
        return run

    def _extract(self, *, at: datetime, run_id: str | None) -> tuple[DocumentEstimates, ...]:
        """Read every document this pass has not read, in id order.

        In id order rather than in whatever the store returns, because ids are
        allocated in ingestion order and two runs over one corpus have to
        produce the same numbers -- the property the ablation table rests on.
        """
        documents = sorted(self._repository.list_all(Document), key=lambda entry: entry.id)
        spans = spans_by_document(self._repository.list_all(Span))
        done = _documents_already_read(self._repository.list_all(Estimate), spans)
        return tuple(
            self._extract_document(
                document,
                spans.get(document.id, ()),
                at=at,
                run_id=run_id,
                already_read=document.id in done,
            )
            for document in documents
        )

    def _extract_document(
        self,
        document: Document,
        spans: Sequence[Span],
        *,
        at: datetime,
        run_id: str | None,
        already_read: bool,
    ) -> DocumentEstimates:
        """Read one document and write the estimates that survived their citation."""
        if already_read or not spans:
            return DocumentEstimates(document=document, already_read=already_read)
        found = self._extractor.extract(
            tuple(spans), document, allocate=StoreAllocator(self._repository), at=at
        )
        for extracted in found.found:
            self._repository.add(
                extracted.estimate,
                actor=ESTIMATION_ACTOR,
                reason=f"a prediction stated in {extracted.estimate.span_id}",
                at=at,
                run_id=run_id,
            )
        return DocumentEstimates(
            document=document,
            found=found.found,
            refused=tuple(
                Refused(
                    stage=Stage.ESTIMATE,
                    refusal=rejection.refusal,
                    doc_id=document.id,
                    detail=rejection.detail,
                    lost=RecordKind.ESTIMATE,
                    ordinal=rejection.ordinal,
                )
                for rejection in found.rejections
            ),
            calls=found.calls,
            blind_windows=found.blind_windows,
        )

    def _classify(
        self, *, at: datetime, run_id: str | None
    ) -> tuple[list[Classification], list[ClassificationRefusal], tuple[int, int], int]:
        """Put every unclassified estimate on the axis, once and only once."""
        classified: list[Classification] = []
        refusals: list[ClassificationRefusal] = []
        already = attempted = calls = 0
        known = _classes_in(self._repository)
        for estimate in sorted(self._repository.list_all(Estimate), key=lambda row: row.id):
            if is_classified(estimate):
                already += 1
                continue
            if _already_attempted(self._repository, estimate):
                attempted += 1
                continue
            evidence = self._repository.get(Span, estimate.span_id)
            if evidence is None:  # pragma: no cover -- a foreign key makes this unreachable
                continue
            result = self._classifier.classify(estimate, evidence, known, at=at)
            calls += result.calls
            if isinstance(result, ClassificationRefusal):
                refusals.append(result)
                continue
            classified.append(result)
            if result.classified:
                # Only a class the store did not hold widens the vocabulary the
                # next estimate is offered, so the listing converges within one
                # pass rather than after two.
                known = (*known, result.work_class) if result.proposed else known
            self._repository.revise(
                result.estimate,
                actor=CLASSIFIER_NAME,
                reason=result.reason,
                at=at,
                run_id=run_id,
            )
        return classified, refusals, (already, attempted), calls

    def _match(
        self, *, at: datetime, run_id: str | None
    ) -> tuple[list[MatchedOutcome], list[UnmatchedEstimate], int, int]:
        """Look for what actually happened to every estimate nothing answers yet."""
        matched: list[MatchedOutcome] = []
        unmatched: list[UnmatchedEstimate] = []
        already = calls = 0
        documents = {document.id: document for document in self._repository.list_all(Document)}
        spans = spans_by_document(self._repository.list_all(Span))
        resolved = {str(outcome.estimate_id) for outcome in self._repository.list_all(Outcome)}
        allocate = StoreAllocator(self._repository)
        for estimate in sorted(self._repository.list_all(Estimate), key=lambda row: row.id):
            if estimate.id in resolved:
                already += 1
                continue
            span = self._repository.get(Span, estimate.span_id)
            if span is None:  # pragma: no cover -- a foreign key makes this unreachable
                continue
            document = documents[span.doc_id]
            result = self._matcher.match(
                estimate, spans.get(span.doc_id, ()), document, allocate=allocate, at=at
            )
            calls += result.calls
            self._repository.add(
                result.outcome,
                actor=ESTIMATION_ACTOR,
                reason=_outcome_reason(result),
                at=at,
                run_id=run_id,
            )
            if isinstance(result, MatchedOutcome):
                matched.append(result)
            else:
                unmatched.append(result)
        return matched, unmatched, already, calls


def _outcome_reason(result: MatchedOutcome | UnmatchedEstimate) -> str:
    """Why this outcome row exists, for the person reading the audit trail."""
    if isinstance(result, MatchedOutcome):
        return f"{result.estimate.id} was resolved by {result.outcome.span_id}"
    return f"nothing in the corpus resolves {result.estimate.id}: {result.reason.value}"


def _classes_in(repository: Repository) -> tuple[str, ...]:
    """The work classes the store already holds, in a stable order.

    Sorted rather than in store order so that two runs over one corpus offer the
    model the same listing -- an offering whose order varies is a different
    prompt, and a different prompt hash in the trace table.
    """
    return tuple(
        sorted(
            {
                estimate.work_class
                for estimate in repository.list_all(Estimate)
                if is_classified(estimate)
            }
        )
    )


def _already_attempted(repository: Repository, estimate: Estimate) -> bool:
    """Whether the classifier has written a version of this estimate before.

    Read off the audit trail rather than off a flag, because the trail already
    records exactly this and a flag would be a second copy of it that could
    disagree. The store is append-only, so the record of the attempt cannot go
    missing -- the same argument `praxis.agents.formalization` makes.
    """
    return any(event.actor == CLASSIFIER_NAME for event in repository.audit_for(estimate.id))


def _documents_already_read(
    estimates: Sequence[Estimate], spans: Mapping[DocumentId, tuple[Span, ...]]
) -> frozenset[DocumentId]:
    """Documents this agent has already pulled estimates out of.

    Keyed on `created_by` rather than on "holds any estimate", because
    `AssumptionExtractor` writes estimates too and a document holding only one
    of those has never been read for the estimates that are not inside an
    assumption. Skipping it would lose exactly the estimates this agent exists
    to find.
    """
    where = {span.id: doc_id for doc_id, group in spans.items() for span in group}
    return frozenset(
        where[estimate.span_id]
        for estimate in estimates
        if estimate.created_by == ESTIMATOR_NAME and estimate.span_id in where
    )


def estimate_store(
    repository: Repository,
    provider: LLMProvider,
    *,
    at: datetime | None = None,
    run_id: str | None = None,
) -> EstimationRun:
    """Run Half B over a store. The entry point `praxis estimates` uses.

    Args:
        repository: The store to read and write.
        provider: The seam, from `provider_for`.
        at: When this ran. Defaults to now, in UTC.
        run_id: Recorded on every audit row this writes.

    Returns:
        What the three stages wrote, skipped and lost.
    """
    return EstimationPipeline(repository, provider).run(at=at, run_id=run_id)
