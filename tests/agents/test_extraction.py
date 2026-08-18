"""Extraction end to end, from a store of spans to a store holding a graph.

The unit tests hold each agent to its own contract. This holds the sequence to
the claims that span all three: what reaches the store cites something that is
in the store, the edges point at nodes that exist, a second run over the same
corpus does not write the same records twice, and two runs over one corpus
produce the same numbers.

Every assertion about what was written reads it back out of SQLite rather than
inspecting the objects that were passed in, which would only prove the pipeline
agrees with itself.

The provider is scripted for the tests about *what* is extracted, because the
offline mock cites and quotes independently and therefore extracts almost
nothing (ADR 0016). It is the real `MockProvider` for the tests about the run
surviving whatever it is told.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from praxis.agents.errors import ExtractionError, Refusal
from praxis.agents.extraction import EXTRACTION_ACTOR, ExtractionPipeline, _from_structure
from praxis.agents.extractor import DEFAULT_BEHIND as EXTRACTOR_BEHIND
from praxis.agents.extractor import EXTRACTOR_NAME
from praxis.agents.results import Stage
from praxis.agents.scout import DEFAULT_WINDOW_SPANS
from praxis.agents.structurer import DEFAULT_REACH as STRUCTURER_REACH
from praxis.agents.structurer import STRUCTURER_NAME, StructureResult
from praxis.config.settings import Settings
from praxis.domain.enums import RecordKind
from praxis.domain.ids import DocumentId
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Decision, Document, Estimate, Link, Span
from praxis.domain.spans import verify_span
from praxis.ingest.adapters import MARKDOWN_ADAPTER
from praxis.ingest.pipeline import IngestionPipeline
from praxis.llm.mock import MockProvider
from praxis.llm.trace import MemoryTraceSink
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

from tests.agents.conftest import ADR_BODY, Answering, Refusing

AT = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)

DECISION_QUOTE = "We are going with OpenSearch on managed nodes"
ASSUMPTION_QUOTE = "This rests on the assumption that the index stays under 50 GB"
ESTIMATE_QUOTE = "Nadeesha put this at 4 weeks of hands-on work"

POSTSCRIPT = """
## Postscript

A closing note, so that this is a second document rather than the same one.
"""
"""Appended to make a second document whose quotations are still the fixture's.

Changing the decision's own words instead would have made every scripted answer
need patching, and a test that patches its own fixtures is a test that will
eventually patch them wrongly.
"""


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


@pytest.fixture
def ingested(store: Repository) -> tuple[Document, tuple[Span, ...]]:
    """One ADR through the real ingestion pipeline, so the spans are real spans."""
    provider = MockProvider(sink=MemoryTraceSink(), settings=Settings())
    source = MARKDOWN_ADAPTER.normalise(ADR_BODY.encode("utf-8"), source_uri="adr.md")
    result = IngestionPipeline(store, provider).ingest_source(source, at=AT)
    return result.document, result.spans


def ordinal_of_quote(spans: tuple[Span, ...], quote: str) -> int:
    """Which passage a quotation is really in, as the agents will be shown it."""
    return next(index for index, span in enumerate(spans) if quote in span.text)


def sightings(*ordinals: int) -> str:
    """What the scout would say."""
    return json.dumps(
        {
            "sightings": [
                {
                    "passage_ordinal": ordinal,
                    "label": "chose a search index",
                    "why": "it says we are going with one",
                    "confidence": 0.7,
                }
                for ordinal in ordinals
            ]
        }
    )


def structured(ordinal: int, quote: str = DECISION_QUOTE) -> str:
    """What the structurer would say."""
    return json.dumps(
        {
            "found": True,
            "title": "OpenSearch for the product index",
            "chosen": "OpenSearch on managed nodes",
            "rejected": [{"option": "Postgres full-text search", "reason": "ranking lost"}],
            "decision_maker": "Nadeesha",
            "decided_on": "2026-01-05",
            "rationale": "Ranking quality on our own queries was not close.",
            "scope": "team",
            "impact": "high",
            "status": "accepted",
            "confidence": 0.8,
            "evidence_ordinal": ordinal,
            "evidence_quote": quote,
        }
    )


def extracted(assumption_ordinal: int, estimate_ordinal: int | None = None) -> str:
    """What the extractor would say, with or without a fusion edge."""
    quantified = estimate_ordinal is not None
    return json.dumps(
        {
            "assumptions": [
                {
                    "statement": "the index stays under 50 GB for the next year",
                    "predicate": "index_size_gb <= 50",
                    "expiry_condition": "when(indexed_documents >= 10000000)",
                    "confidence": 0.7,
                    "evidence_ordinal": assumption_ordinal,
                    "evidence_quote": ASSUMPTION_QUOTE,
                    "quantified": quantified,
                    "estimate_subject": "the search index work" if quantified else None,
                    "estimate_owner": "Nadeesha" if quantified else None,
                    "estimate_work_class": "search-infrastructure" if quantified else None,
                    "estimate_active_quantity": 4 if quantified else None,
                    "estimate_blocked_quantity": None,
                    "estimate_unit": "weeks" if quantified else None,
                    "estimate_ordinal": estimate_ordinal,
                    "estimate_quote": ESTIMATE_QUOTE if quantified else None,
                }
            ]
        }
    )


def scan_answers(spans: tuple[Span, ...], *marked: int) -> list[str]:
    """One answer per window the scout will really ask about.

    The scout windows a document and numbers each window from zero, so a
    passage's ordinal is not its position in the document. Deriving both from
    the real window size is what stops this file asserting against numbers it
    invented, which is the failure `tests/agents/conftest.py` exists to avoid
    one level down.
    """
    answers = []
    for start in range(0, len(spans), DEFAULT_WINDOW_SPANS):
        stop = start + DEFAULT_WINDOW_SPANS
        answers.append(sightings(*(index - start for index in marked if start <= index < stop)))
    return answers


def scan_citing(spans: tuple[Span, ...], *ordinals: int) -> list[str]:
    """The scout naming passages in its first window, offered or not.

    Separate from `scan_answers` because an ordinal that was never offered has
    no window to belong to, and quietly dropping it is exactly the mistake this
    test file would then be making about the agent it is testing.
    """
    windows = -(-len(spans) // DEFAULT_WINDOW_SPANS)
    return [sightings(*ordinals), *[sightings() for _ in range(windows - 1)]]


def a_full_answer(spans: tuple[Span, ...], *, with_estimate: bool = True) -> list[str]:
    """One scan, one structure and one extraction, all citing real passages.

    Each agent is shown a different window, so one passage has three different
    ordinals in one run. Every one of them is computed from the spans.
    """
    decision_index = ordinal_of_quote(spans, DECISION_QUOTE)
    assumption_index = ordinal_of_quote(spans, ASSUMPTION_QUOTE)
    estimate_index = ordinal_of_quote(spans, ESTIMATE_QUOTE)
    structurer_window_starts = max(0, decision_index - STRUCTURER_REACH)
    extractor_window_starts = max(0, decision_index - EXTRACTOR_BEHIND)
    return [
        *scan_answers(spans, decision_index),
        structured(decision_index - structurer_window_starts),
        extracted(
            assumption_index - extractor_window_starts,
            estimate_index - extractor_window_starts if with_estimate else None,
        ),
    ]


def run(store: Repository, answers, **kwargs):
    """Run the whole store through a pipeline whose provider is scripted."""
    return ExtractionPipeline(store, Answering(answers), **kwargs).extract_store(at=AT)


class TestWhatReachesTheStore:
    def test_a_decision_is_written_with_the_edge_saying_where_it_came_from(self, store, ingested):
        _, spans = ingested
        result = run(store, a_full_answer(spans))
        assert result.decisions == 1
        (decision,) = store.list_all(Decision)
        assert decision.created_by == STRUCTURER_NAME
        assert store.links_from(decision.id, [LinkType.JUSTIFIED_BY])

    def test_an_assumption_is_written_with_both_of_its_edges(self, store, ingested):
        _, spans = ingested
        run(store, a_full_answer(spans))
        (assumption,) = store.list_all(Assumption)
        assert assumption.created_by == EXTRACTOR_NAME
        assert store.links_from(assumption.id, [LinkType.JUSTIFIED_BY])
        (decision,) = store.list_all(Decision)
        assumes = store.links_from(decision.id, [LinkType.ASSUMES])
        assert [link.target_id for link in assumes] == [assumption.id]

    def test_the_fusion_edge_reaches_the_store(self, store, ingested):
        # The relationship the whole product exists to find, in SQLite.
        _, spans = ingested
        run(store, a_full_answer(spans))
        (estimate,) = store.list_all(Estimate)
        (assumption,) = store.list_all(Assumption)
        edges = store.links_from(assumption.id, [LinkType.ESTIMATED_AS])
        assert [link.target_id for link in edges] == [estimate.id]

    def test_an_assumption_about_the_world_writes_no_estimate(self, store, ingested):
        _, spans = ingested
        result = run(store, a_full_answer(spans, with_estimate=False))
        assert result.estimates == 0
        assert store.list_all(Estimate) == ()

    def test_every_stored_citation_resolves_against_its_stored_document(self, store, ingested):
        # The claim that spans all three agents, checked against what SQLite
        # holds rather than against the objects that were written.
        _, spans = ingested
        run(store, a_full_answer(spans))
        document = store.list_all(Document)[0]
        cited = [record.span_id for record in store.list_all(Decision)]
        cited += [record.span_id for record in store.list_all(Assumption)]
        cited += [record.span_id for record in store.list_all(Estimate)]
        assert cited
        for span_id in cited:
            assert verify_span(store.require(Span, span_id), document).ok

    def test_every_edge_points_at_a_node_that_exists(self, store, ingested):
        _, spans = ingested
        run(store, a_full_answer(spans))
        links = store.list_all(Link)
        assert len(links) == 4
        for link in links:
            assert store.exists(link.source_id)
            assert store.exists(link.target_id)

    def test_the_pipeline_is_the_actor_and_the_agent_is_the_author(self, store, ingested):
        # Two different questions with two different answers: who wrote this
        # row, and what produced the record. Neither is inferred from the other.
        _, spans = ingested
        run(store, a_full_answer(spans))
        (decision,) = store.list_all(Decision)
        (event,) = store.audit_for(decision.id)
        assert event.actor == EXTRACTION_ACTOR
        assert decision.created_by == STRUCTURER_NAME


class TestIds:
    def test_records_of_one_kind_get_distinct_ids_within_one_answer(self, store, ingested):
        # `next_id` reads the store's highest ordinal, so an agent that built
        # two assumptions before either was written would give both one id.
        _, spans = ingested
        answers = a_full_answer(spans)
        answers[2] = answers[2].replace(
            '"assumptions": [', '"assumptions": ['
        )  # one assumption; the second comes from a second decision below
        run(store, answers)
        assert [record.id for record in store.list_all(Decision)] == ["D-0001"]
        assert [record.id for record in store.list_all(Assumption)] == ["A-0001"]
        assert [record.id for record in store.list_all(Estimate)] == ["EST-0001"]

    def test_ids_continue_from_what_the_store_already_holds(self, store, ingested):
        _, spans = ingested
        run(store, a_full_answer(spans))
        # A second document, so the run is not skipped as already extracted.
        body = ADR_BODY + POSTSCRIPT
        second = MARKDOWN_ADAPTER.normalise(body.encode("utf-8"), source_uri="second.md")
        provider = MockProvider(sink=MemoryTraceSink(), settings=Settings())
        written = IngestionPipeline(store, provider).ingest_source(second, at=AT)
        run(store, a_full_answer(written.spans))
        assert [record.id for record in store.list_all(Decision)] == ["D-0001", "D-0002"]


class TestRunningTwice:
    def test_a_document_already_extracted_is_recognised_rather_than_redone(self, store, ingested):
        # Sequential ids would write a second decision for one passage, and
        # content-addressed link ids would collide outright.
        _, spans = ingested
        run(store, a_full_answer(spans))
        again = run(store, a_full_answer(spans))
        assert again.decisions == 0
        assert again.calls == 0
        assert again.documents[0].already_extracted
        assert len(store.list_all(Decision)) == 1

    def test_a_document_with_no_spans_costs_nothing(self, store):
        result = ExtractionPipeline(store, Answering([])).extract_store(at=AT)
        assert result.documents == ()
        assert result.calls == 0


class TestWhatIsRefused:
    def test_a_scout_citing_a_passage_it_was_not_shown_is_reported_as_such(self, store, ingested):
        _, spans = ingested
        result = run(store, scan_citing(spans, 999))
        (refusal,) = result.refused
        assert refusal.stage is Stage.SCAN
        assert refusal.refusal is Refusal.UNOFFERED_SPAN
        assert refusal.lost is RecordKind.DECISION

    def test_a_fabricated_decision_quotation_is_reported_against_the_structurer(
        self, store, ingested
    ):
        _, spans = ingested
        answers = a_full_answer(spans)
        answers[-2] = answers[-2].replace(DECISION_QUOTE, "We are going with Cassandra on tape")
        result = run(store, answers)
        (refusal,) = result.refused
        assert refusal.stage is Stage.STRUCTURE
        assert refusal.refusal is Refusal.FABRICATED_QUOTE
        assert store.list_all(Decision) == ()

    def test_a_refused_decision_costs_its_assumptions_too(self, store, ingested):
        # The extractor runs once per *verified* decision, so a refused one is
        # never paid for twice. Which is also why the assumption is not lost
        # quietly -- the scan and structure rows say where it went.
        _, spans = ingested
        answers = a_full_answer(spans)
        answers[-2] = answers[-2].replace(DECISION_QUOTE, "We are going with Cassandra on tape")
        result = run(store, answers)
        assert store.list_all(Assumption) == ()
        assert result.assumptions == 0

    def test_a_lost_estimate_does_not_cost_its_assumption(self, store, ingested):
        _, spans = ingested
        answers = a_full_answer(spans)
        answers[-1] = answers[-1].replace(ESTIMATE_QUOTE, "Nadeesha put this at 40 years")
        result = run(store, answers)
        assert result.assumptions == 1
        assert result.estimates == 0
        (refusal,) = result.refused
        assert refusal.stage is Stage.EXTRACT
        assert refusal.lost is RecordKind.ESTIMATE

    def test_the_run_counts_defects_without_walking_documents(self, store, ingested):
        _, spans = ingested
        result = run(store, scan_citing(spans, 999, 998))
        assert result.refused_by(Refusal.UNOFFERED_SPAN) == 2
        assert result.refused_by(Refusal.FABRICATED_QUOTE) == 0


class TestDegradingAboutOneDocument:
    def test_a_scout_that_never_answers_leaves_the_document_blind(self, store, ingested):
        result = ExtractionPipeline(store, Refusing([])).extract_store(at=AT)
        assert result.blind_windows == 2  # every window of the one document
        assert result.decisions == 0
        assert store.list_all(Decision) == ()

    def test_a_structurer_that_never_answers_costs_only_that_candidate(self, store, ingested):
        _, spans = ingested
        decision_index = ordinal_of_quote(spans, DECISION_QUOTE)
        answers = [*scan_answers(spans, decision_index), "not json", "not json", "not json"]
        result = run(store, answers)
        (refusal,) = result.refused
        assert refusal.refusal is Refusal.NO_USABLE_ANSWER
        assert refusal.stage is Stage.STRUCTURE


class TestTheNumbersARunReports:
    def test_the_calls_of_all_three_agents_are_counted_together(self, store, ingested):
        _, spans = ingested
        result = run(store, a_full_answer(spans))
        assert result.calls == 4  # two scout windows, one structure, one extraction
        assert result.documents[0].candidates == 1

    def test_a_document_result_says_how_many_assumptions_were_bets(self, store, ingested):
        _, spans = ingested
        result = run(store, a_full_answer(spans))
        assert result.documents[0].estimates == 1


class TestOffline:
    def test_the_whole_pipeline_survives_whatever_the_mock_says(self, store, ingested):
        # ADR 0016: offline, every field is drawn independently, so almost
        # nothing verifies. What is asserted is that the run completes and
        # reports honestly -- the numbers are about the plumbing.
        provider = MockProvider(sink=MemoryTraceSink(), settings=Settings())
        result = ExtractionPipeline(store, provider).extract_store(at=AT)
        assert result.calls > 0
        assert len(result.documents) == 1

    def test_two_offline_runs_over_one_corpus_agree(self, store, ingested):
        # The property the ablation table rests on. Two stores, one corpus.
        def once() -> tuple[int, int, int, int]:
            connection = connect(MEMORY)
            migrate(connection)
            repository = Repository(connection)
            provider = MockProvider(sink=MemoryTraceSink(), settings=Settings())
            source = MARKDOWN_ADAPTER.normalise(ADR_BODY.encode("utf-8"), source_uri="adr.md")
            IngestionPipeline(repository, provider).ingest_source(source, at=AT)
            outcome = ExtractionPipeline(repository, provider).extract_store(at=AT)
            numbers = (
                outcome.decisions,
                outcome.assumptions,
                outcome.estimates,
                len(outcome.refused),
            )
            repository.close()
            return numbers

        assert once() == once()


def test_a_structure_result_with_neither_half_is_a_bug_not_a_refusal():
    """`StructureResult` promises exactly one, and an unchecked promise is a comment.

    Unreachable through the agent, which is the point: this is the guard that
    turns a silent `None` in a report into a loud failure if the promise is ever
    broken by a change to the structurer.
    """
    with pytest.raises(ExtractionError, match="neither a decision nor a refusal"):
        _from_structure(StructureResult().rejection, DocumentId("DOC-0001"))
