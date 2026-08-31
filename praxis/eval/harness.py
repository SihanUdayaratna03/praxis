"""Running a corpus end to end and grading what came out.

The join everything else avoids having to know about: `praxis.corpus` writes
documents and an answer key, `praxis.ingest` turns the documents into spans,
`praxis.agents` turns the spans into records, and this reads the records back
out of the store, reduces them to `Claim`s and hands them to `matching`.

Two decisions worth stating.

**The claims are read back out of SQLite, not taken from the run's result.**
The run's result is a report of what the agents produced; the store is what
survived being written. Grading the first would grade the pipeline's opinion of
itself, and a record refused by a foreign key would score as a hit.

**A `Claim`'s coordinates come from the span it cites, not from the record.** A
record carries a `span_id` and nothing else about where it was read from, which
is the whole point of invariant 6 -- so the join to the answer key runs through
the span, and a record citing a span the store does not hold is not graded as
anything. It cannot exist: the store's foreign keys refuse it. It is worth
saying because that is the property doing the work here, rather than a check
this module performs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from praxis.agents.calibration import calibrate_store
from praxis.agents.detection import DetectionRun, detect_in_store
from praxis.agents.estimation import EstimationRun, estimate_store
from praxis.agents.extraction import ExtractionPipeline
from praxis.agents.formalization import formalize_store
from praxis.agents.fusion_pass import fuse_store
from praxis.agents.governance import govern_store
from praxis.agents.results import ExtractionRun
from praxis.config.settings import ProviderName
from praxis.corpus.groundtruth import (
    DOCUMENTS_DIRNAME,
    CorpusGroundTruth,
    ItemKind,
    load_ground_truth,
)
from praxis.domain.ids import DocumentId, SpanId
from praxis.domain.links import LinkType
from praxis.domain.records import (
    Assumption,
    Decision,
    Document,
    Estimate,
    Link,
    Outcome,
    Span,
)
from praxis.eval.calibration import CalibrationScore, grade_calibration
from praxis.eval.estimation import EstimationResult, grade_estimation
from praxis.eval.fusion import FusionScore, grade_fusion
from praxis.eval.governance import GovernanceScore, grade_governance
from praxis.eval.matching import (
    SET_SEPARATOR,
    Claim,
    Pairing,
    fusion_pairs,
    pair,
    supersedes_pairs,
)
from praxis.eval.memory import MemoryResult, grade_memory
from praxis.eval.metrics import (
    CitationIntegrity,
    Score,
    citation_integrity,
    cost_per_document,
    exact_matches,
    fusion_recall,
    score,
)
from praxis.ingest.pipeline import IngestionPipeline
from praxis.llm.provider import LLMProvider
from praxis.monitor.facts import FactsFile
from praxis.monitor.run import monitor_store
from praxis.obs.logging import get_logger
from praxis.store.repository import Repository
from praxis.store.traces import cost_by_agent

_log = get_logger(__name__)

GRADED_KINDS: tuple[ItemKind, ...] = (
    ItemKind.DECISION,
    ItemKind.ASSUMPTION,
    ItemKind.ESTIMATE,
    ItemKind.OUTCOME,
)
"""Every kind an extractor is graded on, both halves.

`OUTCOME` joined in Phase 6, which is what Phase 4's version of this line said
would happen: it was held out while nothing could write one, because a permanent
zero in the table reads as a regression rather than as work that has not started.

`ESTIMATE` is graded across both halves at once and that is deliberate.
`AssumptionExtractor` writes an estimate wherever an assumption turns out to be
a quantified claim, and `EstimateExtractor` writes the ones that are nowhere near
an assumption; the answer key does not care which agent found a passage, and
splitting the row by producing agent would measure the pipeline's internal
division rather than its recall."""


@dataclass(frozen=True, slots=True)
class KindResult:
    """One kind's pairing and the numbers over it."""

    pairing: Pairing
    score: Score
    exact: int

    @property
    def kind(self) -> ItemKind:
        """What was being extracted."""
        return self.score.kind


@dataclass(frozen=True, slots=True)
class EvalResult:
    """Everything one graded run knows.

    Attributes:
        run: What the extraction produced, refusals included.
        kinds: One result per graded kind, in `GRADED_KINDS` order.
        citations: How honest the citations were, and how they failed.
        fusion_found: `estimated_as` edges written that land on a pair the
            corpus labels. Joined through the assumption and estimate pairings,
            because the store's ids and the key's ids are allocated
            independently and nothing relates them but the passage both cite.
        fusion_written: Every `estimated_as` edge in the store, labelled or
            not. Reported beside the recall rather than as its numerator: an
            edge the key does not label is not thereby wrong, so counting all
            of them would give a ratio that can exceed 1 and is not a recall.
        fusion_expected: `estimated_as` edges the corpus labels.
        documents: How many documents were graded.
        memory: What the formalization, monitoring and detection passes
            concluded. Present whether or not they ran: a store nothing
            formalized reports a parse rate over the predicates it does hold,
            which is a true statement about that store.
        estimation: What Half B concluded -- whether the store's estimates are
            on the calibration axis, and which of them an actual answered.
            Present on the same terms and for the same reason.
        fusion: What the fusion layer concluded, and the two internal claims
            that can be checked without an answer key: that no refusal was
            bypassed and that every flip really moved a verdict.
        governance: What the adversarial and governance layer concluded --
            the three rates and the three internal claims. Its concede rate is
            a property of the provider that produced it, which is why the score
            carries `mock_provider` beside the number.
        calibration: What the calibration half concluded about itself -- whether
            the refusal threshold held, whether the pass-through fired exactly
            where it should, and what the backtest scored. Its headline numbers
            are usually zeros, and they are correct zeros: no group in a corpus
            this size reaches the threshold.
        cost: What each agent spent per document, from the trace table.
    """

    run: ExtractionRun
    kinds: tuple[KindResult, ...]
    citations: CitationIntegrity
    fusion_found: int
    fusion_expected: int
    documents: int
    fusion_written: int = 0
    """Defaulted, unlike its neighbours, so a caller building a result by hand
    to test the rendering does not have to supply a number it is not testing."""
    memory: MemoryResult = field(default_factory=MemoryResult)
    estimation: EstimationResult = field(default_factory=EstimationResult)
    calibration: CalibrationScore = field(default_factory=CalibrationScore)
    fusion: FusionScore = field(default_factory=FusionScore)
    governance: GovernanceScore = field(default_factory=GovernanceScore)
    cost: Mapping[str, Decimal] = field(default_factory=dict)

    @property
    def fusion_recall(self) -> Decimal:
        """Of the labelled fusion edges, how many were found."""
        return fusion_recall(self.fusion_found, self.fusion_expected)

    def for_kind(self, kind: ItemKind) -> KindResult | None:
        """One kind's result, or `None` if it was not graded."""
        return next((result for result in self.kinds if result.kind is kind), None)


def grade(  # noqa: PLR0913 -- one argument per source the table reads from
    repository: Repository,
    truth: CorpusGroundTruth,
    run: ExtractionRun,
    *,
    detection: DetectionRun | None = None,
    estimation: EstimationRun | None = None,
    run_id: str | None = None,
    mock_provider: bool = True,
) -> EvalResult:
    """Grade what a store holds against the corpus's answer key.

    Args:
        repository: The store the run wrote into.
        truth: The answer key beside the documents that were ingested.
        run: What the extraction reported, for the refusal counts. Nothing is
            graded from it -- the records come out of the store.
        detection: What the detection run reported, for the settlement split
            and what blocking proposed. Neither is written anywhere, which is
            why they are the two things taken from a run rather than a store.
        estimation: What the Half B pass reported, for the split of *why* an
            estimate went unmatched. The outcome row is identical whichever
            stage lost the pairing -- which is what makes re-running free -- so
            the cause is a fact about the run and about nothing else.
        run_id: The run whose trace rows the cost column totals. Without one
            there is no cost to report -- the trace table is keyed by run, and
            summing every row would total every run this store ever held.
        mock_provider: Whether the run used the mock. Carried into the
            governance score so a report can say whose number its concede rate
            is: against the mock a bare boolean comes back true seven times in
            ten, so the rate measures schema synthesis rather than reasoning.

    Returns:
        Every number the report prints.
    """
    claims = claims_in(repository)
    kinds = tuple(_graded(claims, truth, kind) for kind in GRADED_KINDS)
    documents = len(truth.documents)
    return EvalResult(
        run=run,
        kinds=kinds,
        citations=citation_integrity(run.refused, offered=len(claims)),
        fusion_found=len(_fusion_found(repository, kinds, truth)),
        fusion_written=sum(
            1 for link in repository.list_all(Link) if link.link_type is LinkType.ESTIMATED_AS
        ),
        fusion_expected=len(fusion_pairs(truth)),
        documents=documents,
        memory=grade_memory(
            repository, truth, _pairing_for(kinds, ItemKind.ASSUMPTION), detection=detection
        ),
        estimation=grade_estimation(
            repository, truth, _pairing_for(kinds, ItemKind.ESTIMATE), run=estimation
        ),
        # No answer key and no run argument. A calibration factor is not
        # something a document can state, so there is nothing in the corpus to
        # compare against -- what is checkable is internal consistency, and it
        # is recomputed from the store rather than taken from a pass's return.
        calibration=grade_calibration(repository),
        # Also no answer key, and for a sharper reason: whether a *calibrated*
        # number violates a predicate depends on accumulated history rather
        # than on anything a document says, so no corpus could state it. What
        # is checkable is that no refusal was bypassed and that every flip
        # really moved a verdict. `fusion_recall` above still grades the edges
        # the corpus *does* plant; this does not recompute it.
        fusion=grade_fusion(repository),
        # One answer key and three rates that have none. The corpus labels a
        # `supersedes` edge wherever a revision note says in words that an
        # earlier assumption no longer stands, so merge recall is real. Whether
        # a challenger *should* have conceded is not something a document can
        # state, so the concede rate is reported with the provider that produced
        # it attached -- against the mock it measures schema synthesis.
        governance=grade_governance(
            repository,
            merges_expected=len(supersedes_pairs(truth)),
            mock_provider=mock_provider,
        ),
        cost=_cost(repository, documents, run_id),
    )


def evaluate(
    repository: Repository,
    provider: LLMProvider,
    corpus: Path,
    *,
    at: datetime | None = None,
    run_id: str | None = None,
) -> EvalResult:
    """Ingest a corpus, extract from it, and grade the result.

    The whole harness in one call, and the entry point `praxis eval` uses. The
    store is passed in rather than opened here so that a caller can keep it and
    look at what was written, which is what makes a failing number debuggable.

    Args:
        repository: An open, migrated store to ingest and extract into.
        provider: The seam, from `provider_for`. Every agent uses this one.
        corpus: The corpus root -- the directory holding `documents/` and the
            answer key beside it.
        at: When this ran. Defaults to now, in UTC, per stage.
        run_id: Recorded on every audit row this writes.

    Returns:
        The graded result.
    """
    truth = load_ground_truth(corpus)
    ingestion = IngestionPipeline(repository, provider).ingest_directory(
        corpus / DOCUMENTS_DIRNAME, at=at
    )
    run = ExtractionPipeline(repository, provider).extract_store(at=at, run_id=run_id)
    estimation = estimate_store(repository, provider, at=at, run_id=run_id)
    memory = _remember(repository, provider, truth, at=at, run_id=run_id)
    # Last, and it could run anywhere after `estimate_store`: calibration reads
    # accumulated `Outcome` rows and feeds nothing downstream, which is exactly
    # what makes it a store pass rather than a stage. It costs no model call, so
    # its position cannot move the cost column either.
    calibrate_store(repository, at=at, run_id=run_id)
    # After calibration, and this one genuinely cannot move: the fusion pass
    # asks `BiasDetective` for a factor per group, so it has to run once the
    # outcomes calibration reads are in place. It costs no model call either.
    fuse_store(repository, at=at, run_id=run_id)
    # Last, and this one genuinely cannot move either: governance argues against
    # the findings every stage above it filed, so it has to run once they exist.
    # It is also the only stage after ingestion whose cost is a `reason`-tier
    # call per batch of findings rather than per document.
    governance = govern_store(repository, provider, at=at, run_id=run_id)
    _log.info(
        "eval_run",
        documents=len(ingestion.ingested),
        spans=ingestion.spans_written,
        decisions=run.decisions,
        assumptions=run.assumptions,
        estimates=run.estimates,
        outcomes=estimation.outcomes,
        calls=(ingestion.calls + run.calls + estimation.calls + memory.calls + governance.calls),
    )
    return grade(
        repository,
        truth,
        run,
        detection=memory.detection,
        estimation=estimation,
        run_id=run_id,
        mock_provider=provider.name is ProviderName.MOCK,
    )


@dataclass(frozen=True, slots=True)
class MemoryPasses:
    """What the three passes after extraction did, before anything is graded.

    Attributes:
        formalized: Assumptions compiled into checkable predicates.
        revised: Assumptions whose status the monitor changed.
        detection: The detection run, kept because the settlement split and
            what blocking proposed are the two facts no store holds.
        calls: Model calls the three passes made between them.
    """

    formalized: int
    revised: int
    detection: DetectionRun
    calls: int


def _remember(
    repository: Repository,
    provider: LLMProvider,
    truth: CorpusGroundTruth,
    *,
    at: datetime | None,
    run_id: str | None,
) -> MemoryPasses:
    """Compile, evaluate and compare, in that order and for that reason.

    Formalization runs first because the monitor can only reach a verdict about
    a predicate that parses, and detection runs last because the interval
    arithmetic reads the same compiled predicates and blocking buckets records
    by the quantities they constrain. Running them in any other order would
    measure the order rather than the agents.

    Half B runs *before* all three, in `evaluate`. `measured_in` binds a
    quantity only where an assumption's `estimated_as` edge reaches an estimate
    that an `Outcome` resolves, so a monitor run before anything writes an
    outcome has nothing to bind and reports a zero about ordering rather than
    about mechanism. This is the one place in the harness where moving a call
    changes a number that is not about the agent it belongs to.

    The monitor is given the corpus's own world -- the measurements and
    observations the answer key states -- because a verdict is only gradeable
    against the facts the key computed it from.
    """
    formalization = formalize_store(repository, provider, at=at, run_id=run_id)
    monitoring = monitor_store(
        repository,
        provider=provider,
        supplied=FactsFile(
            facts=truth.monitoring.facts,
            events=truth.monitoring.events,
            as_of=truth.monitoring.as_of,
        ),
        at=at,
        run_id=run_id,
    )
    detection = detect_in_store(repository, provider=provider, at=at, run_id=run_id)
    return MemoryPasses(
        formalized=len(formalization.checkable),
        revised=monitoring.revised,
        detection=detection,
        calls=formalization.calls + monitoring.calls + detection.result.calls,
    )


def claims_in(repository: Repository) -> tuple[Claim, ...]:
    """Reduce everything Half A stored to the view grading needs.

    Read back out of SQLite rather than taken from the run, so that what is
    graded is what survived being written.

    The field names are the answer key's, not the records': `praxis.corpus`
    writes what it expects under `ExpectedField.name`, and this is the one place
    the two vocabularies meet. Anywhere else would be two places.

    **An unresolved outcome cites no span and is not a claim.** It has no
    position in the key's coordinate system, so it cannot be paired with an item
    by byte overlap and grading it as a miss would count a correct refusal as a
    wrong answer. Those rows are the whole subject of `MatchScore` instead, where
    they are the numerator of a refusal rate rather than a false negative.
    """
    where = _Where(
        spans={span.id: span for span in repository.list_all(Span)},
        hashes={document.id: document.content_hash for document in repository.list_all(Document)},
    )
    return (
        *(
            where.claim(
                record.id,
                ItemKind.DECISION,
                record.span_id,
                {
                    "chosen": record.chosen,
                    "rejected": SET_SEPARATOR.join(option.option for option in record.rejected),
                    "decision_maker": record.decision_maker,
                    "title": record.title,
                },
            )
            for record in repository.list_all(Decision)
        ),
        *(
            where.claim(
                record.id,
                ItemKind.ASSUMPTION,
                record.span_id,
                {
                    "statement": record.statement,
                    "predicate": record.predicate,
                    "expiry_condition": record.expiry_condition,
                },
            )
            for record in repository.list_all(Assumption)
        ),
        *(
            where.claim(
                record.id,
                ItemKind.ESTIMATE,
                record.span_id,
                {
                    "active_quantity": str(record.active_quantity),
                    "unit": record.unit.value,
                    "owner": record.owner,
                    "work_class": record.work_class,
                },
            )
            for record in repository.list_all(Estimate)
        ),
        *(
            where.claim(
                record.id,
                ItemKind.OUTCOME,
                record.span_id,
                {
                    "active_quantity": str(record.active_quantity),
                    "blocked_quantity": str(record.blocked_quantity),
                    "unit": record.unit.value,
                },
            )
            for record in repository.list_all(Outcome)
            if record.span_id is not None
        ),
    )


@dataclass(frozen=True, slots=True)
class _Where:
    """Everything needed to place a record in the answer key's coordinates.

    A record carries a `span_id` and nothing else about where it was read from,
    which is invariant 6 working as intended -- so every claim's position is
    resolved through the span, and the document's content hash is what joins it
    to `DocumentGroundTruth.content_sha256`.
    """

    spans: Mapping[SpanId, Span]
    hashes: Mapping[DocumentId, str]

    def claim(
        self, record_id: str, kind: ItemKind, span_id: SpanId, fields: Mapping[str, str]
    ) -> Claim:
        """One claim, placed."""
        span = self.spans[span_id]
        return Claim(
            record_id=record_id,
            kind=kind,
            content_hash=self.hashes[span.doc_id],
            start_byte=span.start_byte,
            end_byte=span.end_byte,
            fields=fields,
        )


def _graded(claims: tuple[Claim, ...], truth: CorpusGroundTruth, kind: ItemKind) -> KindResult:
    """Pair and count one kind."""
    pairing = pair(claims, truth, kind=kind)
    return KindResult(
        pairing=pairing, score=score(pairing, kind), exact=exact_matches(pairing.matched)
    )


def _fusion_found(
    repository: Repository, kinds: tuple[KindResult, ...], truth: CorpusGroundTruth
) -> set[tuple[str, str]]:
    """The labelled `estimated_as` pairs an edge in the store really reaches.

    An edge names two record ids; the key names two item ids. The two pairings
    are the only thing that relates them, so an edge whose either end was never
    paired cannot be judged and is not counted here -- it is counted in
    `fusion_written` instead.
    """
    items = _item_ids(kinds, ItemKind.ASSUMPTION) | _item_ids(kinds, ItemKind.ESTIMATE)
    expected = set(fusion_pairs(truth))
    found = {
        (items[link.source_id], items[link.target_id])
        for link in repository.list_all(Link)
        if link.link_type is LinkType.ESTIMATED_AS
        and link.source_id in items
        and link.target_id in items
    }
    return found & expected


def _item_ids(kinds: tuple[KindResult, ...], kind: ItemKind) -> dict[str, str]:
    """Record id to answer-key item id, for one kind's matched pairs."""
    return {
        match.claim.record_id: match.item.item_id for match in _pairing_for(kinds, kind).matched
    }


def _pairing_for(kinds: tuple[KindResult, ...], kind: ItemKind) -> Pairing:
    """One kind's pairing, reused rather than recomputed.

    `praxis.eval.memory` joins stored records to answer-key items through the
    assumption pairing, and pairing twice would be two chances to pair one
    record differently -- the greedy walk is deterministic, but two callers
    agreeing by construction beats two callers agreeing by argument.
    """
    return next(result.pairing for result in kinds if result.kind is kind)


def _cost(repository: Repository, documents: int, run_id: str | None) -> dict[str, Decimal]:
    """What each agent spent per document, or nothing when there is no run to total."""
    if run_id is None:
        return {}
    return cost_per_document(cost_by_agent(repository.connection, run_id), documents)
