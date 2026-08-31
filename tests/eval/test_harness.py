"""Grading a run of the real generated corpus, end to end and offline.

`tests/eval/test_matching.py` and `test_metrics.py` build their fixtures by
hand, each one posing a specific ambiguity. This file poses none: it generates
the corpus `praxis.corpus` really writes, ingests it through the real pipeline,
extracts with the real agents and grades the result. That is what stops the
hand-built fixtures drifting away from the shapes the generator emits.

Two claims are worth more than the rest.

**Two runs over one corpus with one seed produce identical numbers.** The
requirement Phase 4's handover named, and the one the Phase 10 ablation table
rests on. It is asserted over the whole rendered result rather than over one
metric, because the ways to lose it -- set iteration, a clock, a dictionary
built in arrival order -- would each move a different number.

**What is graded is what survived being written.** The run reports what the
agents produced; the store holds what the foreign keys accepted. A test that
graded the first would pass while the store stayed empty, so the tests about
what was found write to the store and pass a run that reports nothing.

The numbers themselves are not asserted. Offline, `praxis.llm.synthesis` draws
the cited ordinal and the quotation independently, so almost every extraction
is refused and the totals are a property of the mock rather than of the
pipeline (ADR 0016). Asserting a recall figure here would pin the mock.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from praxis.agents.errors import Refusal
from praxis.agents.results import DocumentExtraction, ExtractionRun, Refused, Stage
from praxis.config.settings import Settings
from praxis.corpus.generator import Controls, generate_corpus
from praxis.corpus.groundtruth import (
    DOCUMENTS_DIRNAME,
    CorpusGroundTruth,
    DocumentGroundTruth,
    GroundTruthItem,
    ItemKind,
    load_ground_truth,
)
from praxis.domain.enums import DecisionScope, DecisionStatus, Impact, RecordKind, SourceKind
from praxis.domain.ids import DecisionId, DocumentId
from praxis.domain.links import LinkType
from praxis.domain.records import Decision, Document, RejectedOption, Span
from praxis.eval.harness import GRADED_KINDS, EvalResult, claims_in, evaluate, grade
from praxis.eval.matching import SET_SEPARATOR
from praxis.eval.report import as_json
from praxis.ingest.pipeline import IngestionPipeline
from praxis.llm.mock import MockProvider
from praxis.llm.trace import MemoryTraceSink
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

AT = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)
SEED = 20260818

DOCUMENTS = 4
"""Four rather than the generator's default twelve. Every shape it writes is
covered by `tests/corpus/`; what these need is more than one document, so that
the content-hash join has something it could get wrong."""

ACTOR = "test"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One generated corpus for the module.

    Module-scoped because generating it is the slowest thing here and nothing
    below writes to it -- every store is per-test and in memory.
    """
    root = tmp_path_factory.mktemp("corpus")
    # revisions=0: this file grades Half A's extraction, and the revision
    # notes are ground truth for ContradictionDetector rather than for it.
    # Phase 10's controls are off here for the same reason, and are graded in
    # tests/eval/test_ablation.py where they are the subject.
    generate_corpus(
        root,
        documents=DOCUMENTS,
        revisions=0,
        controls=Controls(clean=0, adversarial=0, orphans=0),
        seed=SEED,
        generated_at=AT,
    )
    return root


@pytest.fixture
def truth(corpus: Path) -> CorpusGroundTruth:
    return load_ground_truth(corpus)


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


def a_provider() -> MockProvider:
    """The offline provider, with a sink of its own so traces stay per-test."""
    return MockProvider(sink=MemoryTraceSink(), settings=Settings())


def evaluated(corpus: Path) -> EvalResult:
    """A whole run, in a store that closes with it."""
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    try:
        return evaluate(repository, a_provider(), corpus, at=AT)
    finally:
        repository.close()


def ingest(store: Repository, corpus: Path) -> None:
    """The corpus's documents through the real ingestion pipeline."""
    IngestionPipeline(store, a_provider()).ingest_directory(corpus / DOCUMENTS_DIRNAME, at=AT)


def document_for(store: Repository, entry: DocumentGroundTruth) -> Document:
    """The stored document one answer-key entry describes.

    Joined on the content hash, which is the join the harness itself makes --
    `DocumentGroundTruth.content_sha256` is exactly `Document.content_hash`.
    """
    return next(
        document
        for document in store.list_all(Document)
        if document.content_hash == entry.content_sha256
    )


def first_item(
    truth: CorpusGroundTruth, kind: ItemKind
) -> tuple[DocumentGroundTruth, GroundTruthItem]:
    """The first real item of a kind, with the document holding it."""
    return next(
        (document, item)
        for document in truth.documents
        for item in document.items
        if item.kind is kind and not item.is_distractor
    )


def span_over(store: Repository, document: Document, item: GroundTruthItem) -> Span:
    """A real span cut to an answer-key item's own bytes, written to the store.

    Cut to the key's coordinates rather than the block grid's, so that a test
    about the join is not also a test about where the segmenter would have put
    a boundary. Returned rather than added when the store already holds it: a
    span's id is its own coordinates, so ingestion may have cut the same one.
    """
    span = Span.covering(document, item.start_byte, item.end_byte, created_by=ACTOR, created_at=AT)
    held = store.get(Span, span.id)
    if held is not None:
        return held
    return store.add(span, actor=ACTOR, reason="A span over a known answer.", at=AT)


def decision_from(item: GroundTruthItem, span: Span) -> Decision:
    """A decision answering one key item, in the key's own words.

    Built from `item.fields` rather than from a literal, so that a field the
    generator renames fails here instead of quietly scoring zero.
    """
    expected = {field.name: field.value for field in item.fields}
    return Decision(
        id=DecisionId("D-0001"),
        title="A decision the key knows about",
        chosen=expected["chosen"],
        rejected=tuple(
            RejectedOption(option=option, reason="the key does not grade this")
            for option in expected["rejected"].split(SET_SEPARATOR)
        ),
        decision_maker=expected["decision_maker"],
        decided_at=AT,
        scope=DecisionScope.TEAM,
        impact=Impact.HIGH,
        status=DecisionStatus.ACCEPTED,
        span_id=span.id,
        confidence=0.9,
        created_by=ACTOR,
        created_at=AT,
    )


def a_run(*refused: Refused) -> ExtractionRun:
    """A run reporting refusals and nothing else."""
    document = Document(
        id=DocumentId("DOC-0001"),
        source_uri="adr.md",
        source_kind=SourceKind.MARKDOWN,
        content="x",
        ingested_at=AT,
        created_by=ACTOR,
        created_at=AT,
    )
    return ExtractionRun(documents=(DocumentExtraction(document=document, refused=refused),))


def stored_answer(store: Repository, truth: CorpusGroundTruth, corpus: Path) -> GroundTruthItem:
    """Ingest the corpus and store one correct decision. Returns the item it answers."""
    ingest(store, corpus)
    entry, item = first_item(truth, ItemKind.DECISION)
    span = span_over(store, document_for(store, entry), item)
    store.add(decision_from(item, span), actor=ACTOR, reason="A known answer.", at=AT)
    return item


# -- the whole harness over the real corpus ------------------------------------


def test_evaluate_ingests_extracts_and_grades_a_generated_corpus(corpus, truth):
    result = evaluated(corpus)

    assert result.documents == len(truth.documents) == DOCUMENTS
    assert tuple(kind.kind for kind in result.kinds) == GRADED_KINDS


def test_every_graded_kind_accounts_for_all_of_the_corpus_s_own_items(corpus, truth):
    result = evaluated(corpus)

    for kind in result.kinds:
        expected = sum(
            1 for item in truth.items if item.kind is kind.kind and not item.is_distractor
        )
        assert kind.score.true_positives + kind.score.false_negatives == expected


def test_two_runs_over_one_corpus_produce_identical_numbers(corpus):
    first, second = evaluated(corpus), evaluated(corpus)

    assert as_json(first) == as_json(second)


def test_outcomes_are_graded_from_phase_6(corpus):
    """The line Phase 4 held open, closed. `OUTCOME` was kept out of the table
    while nothing could write one, because a permanent zero reads as a
    regression rather than as work that has not started. Something writes one
    now, so it is a row."""
    result = evaluated(corpus)

    assert ItemKind.OUTCOME in GRADED_KINDS
    assert result.for_kind(ItemKind.OUTCOME) is not None
    assert result.for_kind(ItemKind.DECISION) is not None


def test_the_estimation_half_is_graded_from_the_store(corpus):
    """Every number in the estimation half comes out of SQLite, like the rest.

    The one exception is the split of *why* an estimate went unmatched, which
    is a fact about the run because the outcome row is identical whichever
    stage lost the pairing -- and that identity is what makes re-running free.
    So the counts must agree with the store and the reasons must add up to the
    unresolved rows rather than to some other number.
    """
    result = evaluated(corpus)
    matching = result.estimation.matching

    assert matching.asked == matching.resolved + matching.unresolved
    assert sum(matching.by_reason.values()) == matching.unresolved


def test_the_fusion_denominator_is_the_edges_the_corpus_labels(corpus, truth):
    result = evaluated(corpus)

    labelled = sum(
        1 for item in truth.items for link in item.links if link.link_type is LinkType.ESTIMATED_AS
    )
    assert result.fusion_expected == labelled > 0


def test_the_fusion_numerator_counts_only_labelled_pairs(corpus):
    """A recall over 1 is not a recall.

    The numerator used to be every `estimated_as` edge in the store, which was
    invisible for six phases because no offline run ever wrote more edges than
    the key holds. Phase 10's coherently-citing mock writes plenty, and the
    ratio came out at 9.3333.
    """
    result = evaluated(corpus)

    assert result.fusion_found <= result.fusion_expected
    assert result.fusion_found <= result.fusion_written
    assert result.fusion_recall <= 1


def test_the_offline_provider_is_refused_more_often_than_it_is_believed(corpus):
    """ADR 0016's claim, asserted rather than left to the report's caveat.

    The mock draws a cited ordinal and a quotation independently, so the two
    agree only by chance and the gate stops most of what it produces. If that
    ever stopped being true the report's caveat would be wrong, and a caveat
    nobody re-reads is exactly the kind of statement a test should hold.
    """
    result = evaluated(corpus)

    assert result.run.calls > 0
    assert result.citations.refused > result.citations.offered


# -- what gets graded ----------------------------------------------------------


def test_an_empty_store_misses_every_item_and_scores_no_precision(store, truth):
    result = grade(store, truth, ExtractionRun())

    for kind in result.kinds:
        assert kind.score.true_positives == 0
        assert kind.score.false_negatives > 0
        assert kind.score.recall == Decimal("0.0000")
        assert kind.score.precision == Decimal("0.0000")


def test_a_record_the_run_never_reported_is_graded_from_the_store(store, truth, corpus):
    stored_answer(store, truth, corpus)

    decisions = grade(store, truth, ExtractionRun()).for_kind(ItemKind.DECISION)

    assert decisions is not None
    assert decisions.score.true_positives == 1


def test_a_decision_in_the_key_s_own_words_gets_every_field_right(store, truth, corpus):
    stored_answer(store, truth, corpus)

    decisions = grade(store, truth, ExtractionRun()).for_kind(ItemKind.DECISION)

    assert decisions is not None
    assert decisions.exact == 1
    assert decisions.score.field_accuracy == Decimal("1.0000")


def test_a_decision_over_the_wrong_passage_is_spurious_rather_than_a_hit(store, truth, corpus):
    ingest(store, corpus)
    entry, item = first_item(truth, ItemKind.DECISION)
    document = document_for(store, entry)
    elsewhere = next(
        other
        for other in store.list_all(Span)
        if other.doc_id == document.id and other.end_byte <= item.start_byte
    )
    store.add(decision_from(item, elsewhere), actor=ACTOR, reason="A misplaced answer.", at=AT)

    decisions = grade(store, truth, ExtractionRun()).for_kind(ItemKind.DECISION)

    assert decisions is not None
    assert decisions.score.true_positives == 0
    assert len(decisions.pairing.spurious) == 1


# -- claims_in -----------------------------------------------------------------


def test_an_empty_store_offers_no_claims(store):
    assert claims_in(store) == ()


def test_a_claim_is_placed_by_the_span_it_cites(store, truth, corpus):
    ingest(store, corpus)
    entry, item = first_item(truth, ItemKind.DECISION)
    span = span_over(store, document_for(store, entry), item)
    store.add(decision_from(item, span), actor=ACTOR, reason="A known answer.", at=AT)

    claim = next(claim for claim in claims_in(store) if claim.kind is ItemKind.DECISION)

    assert claim.content_hash == entry.content_sha256
    assert (claim.start_byte, claim.end_byte) == (span.start_byte, span.end_byte)


def test_a_claim_carries_the_field_names_the_answer_key_spells(store, truth, corpus):
    item = stored_answer(store, truth, corpus)

    claim = next(claim for claim in claims_in(store) if claim.kind is ItemKind.DECISION)

    assert {field.name for field in item.fields} <= set(claim.fields)


# -- citation integrity --------------------------------------------------------


def test_citation_integrity_counts_the_run_s_refusals_against_what_was_stored(store, truth):
    refused = Refused(
        stage=Stage.STRUCTURE,
        refusal=Refusal.FABRICATED_QUOTE,
        doc_id=DocumentId("DOC-0001"),
        detail="a quotation in none of the offered passages",
        lost=RecordKind.DECISION,
    )

    result = grade(store, truth, a_run(refused))

    assert result.citations.refused == 1
    assert result.citations.by_refusal == {Refusal.FABRICATED_QUOTE: 1}
    assert result.citations.by_stage == {Stage.STRUCTURE: 1}
    assert result.citations.integrity == Decimal("0.0000")


def test_a_run_that_lost_nothing_has_perfect_integrity(store, truth):
    result = grade(store, truth, ExtractionRun())

    assert result.citations.refused == 0
    assert result.citations.integrity == Decimal("1.0000")


# -- what each agent cost per document -----------------------------------------


def test_no_run_id_means_no_cost_to_report(store, truth):
    # The trace table is keyed by run. Summing every row would total every run
    # the store ever held, which is a number about the file rather than the run.
    assert grade(store, truth, ExtractionRun()).cost == {}


def test_cost_is_reported_per_agent_per_document(corpus):
    # Offline every cost is zero, so what this asserts is the division and the
    # keys -- that an agent which ran has a row, and that the denominator is the
    # corpus rather than the call count.
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    try:
        result = evaluate(repository, a_provider(), corpus, at=AT, run_id="RUN-0001")
    finally:
        repository.close()

    assert result.documents == DOCUMENTS
    assert set(result.cost) <= {
        "AssumptionExtractor",
        "AssumptionFormalizer",
        "AssumptionMonitor",
        "ContradictionDetector",
        "DecisionScout",
        "DecisionStructurer",
        "SegmenterAgent",
    }
    assert all(isinstance(spent, Decimal) for spent in result.cost.values())
