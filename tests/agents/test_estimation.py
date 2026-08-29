"""Half B end to end, from a store of spans to a store holding a calibration.

The unit tests hold each agent to its own contract. This holds the *sequence* to
the claims that span all three, and they are the ones the phase is judged on:

- an estimate that reaches the store cites a span that is in the store
- every estimate leaves the pass with exactly one outcome, resolved or not
- a second run over an unchanged store writes nothing and costs no call
- a stored outcome always carries its estimate's unit, so Phase 7 never converts
- the classifier repairs the `unclassified` rows Half A wrote, not only its own

Everything asserted about what was written is read back out of SQLite. The run's
result is the pipeline's opinion of itself; the store is what survived.

The provider is scripted where the tests are about *what* is written, because
the offline mock cites and quotes independently and therefore stores almost
nothing (ADR 0016). It is the real `MockProvider` where the test is about the
run surviving whatever it is told.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from praxis.agents.classifier import CLASSIFIER_NAME, UNCLASSIFIED
from praxis.agents.estimation import (
    ESTIMATION_ACTOR,
    EstimationPipeline,
    EstimationRun,
    estimate_store,
)
from praxis.agents.estimator import DEFAULT_WINDOW_SPANS, ESTIMATOR_NAME
from praxis.agents.matcher import MATCHER_NAME
from praxis.agents.offering import windows_of
from praxis.agents.reconciliation import candidates_for
from praxis.agents.results import Stage
from praxis.config.settings import Settings
from praxis.domain.enums import MatchQuality, RecordKind, Unit
from praxis.domain.ids import EstimateId
from praxis.domain.records import Document, Estimate, Outcome, Span
from praxis.domain.spans import verify_span
from praxis.ingest.adapters import MARKDOWN_ADAPTER
from praxis.ingest.pipeline import IngestionPipeline
from praxis.llm.errors import ProviderRefusalError
from praxis.llm.mock import MockProvider
from praxis.llm.trace import MemoryTraceSink
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

from tests.agents.conftest import Answering

AT = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)

STATUS_BODY = """\
# Discovery status — week 9

## What shipped

The product search index migration is live in production.

## How it went

Nadeesha put the search index migration at 4 weeks of hands-on work.

The search index migration actually took 7 weeks of hands-on work, Nadeesha
confirmed, with nothing lost waiting on anybody.

## Next up

Nothing decided about hosted search yet.
"""

ESTIMATE_QUOTE = "Nadeesha put the search index migration at 4 weeks of hands-on work"
ACTUAL_QUOTE = "The search index migration actually took 7 weeks of hands-on work"


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


@pytest.fixture
def ingested(store: Repository) -> tuple[Document, tuple[Span, ...]]:
    """One status update through the real pipeline, so the spans are real spans."""
    provider = MockProvider(sink=MemoryTraceSink(), settings=Settings())
    source = MARKDOWN_ADAPTER.normalise(STATUS_BODY.encode("utf-8"), source_uri="status.md")
    result = IngestionPipeline(store, provider).ingest_source(source, at=AT)
    return result.document, result.spans


def ordinal_of(spans: tuple[Span, ...], quote: str) -> int:
    """Which passage a quotation is really in, as the agent will be shown it."""
    return next(index for index, span in enumerate(spans) if quote in span.text)


def sighted(ordinal: int, quote: str = ESTIMATE_QUOTE) -> str:
    """What `EstimateExtractor` would say about a window holding the estimate."""
    return json.dumps(
        {
            "estimates": [
                {
                    "passage_ordinal": ordinal,
                    "quote": quote,
                    "subject": "the search index migration",
                    "owner": "Nadeesha",
                    "active_quantity": 4,
                    "blocked_quantity": 0,
                    "unit": "weeks",
                    "confidence": 0.8,
                }
            ]
        }
    )


NOTHING = json.dumps({"estimates": []})


def classified(work_class: str = "migration") -> str:
    """What `WorkClassifier` would say."""
    return json.dumps(
        {
            "work_class": work_class,
            "existing": False,
            "why": "moving a search index onto new infrastructure",
            "confidence": 0.8,
        }
    )


def resolved(ordinal: int, quote: str = ACTUAL_QUOTE, **fields: object) -> str:
    """What `OutcomeMatcher` would say when a passage reports the actual."""
    payload = {
        "resolved": True,
        "passage_ordinal": ordinal,
        "quote": quote,
        "active_quantity": 7,
        "blocked_quantity": 0,
        "unit": "weeks",
        "why": "the same migration, the same owner, in the past tense",
        "confidence": 0.9,
    }
    payload.update(fields)
    return json.dumps(payload)


UNRESOLVED = json.dumps({"resolved": False, "why": "no passage reports an actual"})


def candidate_ordinal(spans: tuple[Span, ...], quote: str) -> int:
    """Where a quote lands in the listing `OutcomeMatcher` really builds.

    Not the same as its position in the document: the matcher offers only the
    passages deterministic selection proposed, and those are renumbered from
    zero. Computing it here rather than writing a constant is what keeps these
    tests honest about which listing the ordinal belongs to.
    """
    probe = Estimate(
        id=EstimateId("EST-9999"),
        subject="the search index migration",
        owner="Nadeesha",
        work_class=UNCLASSIFIED,
        active_quantity=Decimal(4),
        blocked_quantity=Decimal(0),
        unit=Unit.WEEKS,
        confidence=0.8,
        estimated_at=AT,
        span_id=next(span.id for span in spans if ESTIMATE_QUOTE in span.text),
        created_by=ESTIMATOR_NAME,
        created_at=AT,
    )
    for index, span in enumerate(candidates_for(probe, spans)):
        if quote in span.text:
            return index
    message = f"{quote!r} is in no passage the matcher would be offered"
    raise AssertionError(message)


def extraction_answers(spans: tuple[Span, ...]) -> list[str]:
    """One answer per window, the real one in the window holding the estimate.

    Built from the document rather than written out: a hard-coded list would
    silently go out of step the moment the fixture gained a paragraph.
    """
    windows = windows_of(spans, DEFAULT_WINDOW_SPANS)
    target = next(
        index
        for index, window in enumerate(windows)
        if any(ESTIMATE_QUOTE in entry.span.text for entry in window.entries)
    )
    ordinal = next(
        entry.ordinal for entry in windows[target].entries if ESTIMATE_QUOTE in entry.span.text
    )
    return [sighted(ordinal) if i == target else NOTHING for i in range(len(windows))]


def scripted(spans: tuple[Span, ...], *, match: str | None = None) -> Answering:
    """A provider answering every stage of one pass over one document.

    The estimate stage is asked once per window and the later stages once per
    estimate, so the script follows the document's own shape.
    """
    answers = extraction_answers(spans)
    answers.append(classified())
    answers.append(match if match is not None else UNRESOLVED)
    return Answering(answers)


class TestWriting:
    """What reaches the store, read back out of it."""

    def test_writes_an_estimate_citing_a_span_the_store_holds(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        document, spans = ingested

        EstimationPipeline(store, scripted(spans)).run(at=AT)

        stored = store.list_all(Estimate)
        assert len(stored) == 1
        span = store.get(Span, stored[0].span_id)
        assert span is not None
        assert verify_span(span, document).ok

    def test_the_estimate_records_the_agent_that_produced_it(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        """`created_by` is the agent; the audit actor is the pass. Two questions."""
        _, spans = ingested

        EstimationPipeline(store, scripted(spans)).run(at=AT)

        estimate = store.list_all(Estimate)[0]
        actors = {event.actor for event in store.audit_for(estimate.id)}
        assert ESTIMATION_ACTOR in actors

    def test_classification_revises_rather_than_replaces(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        """Append-only, invariant 7: the unclassified version stays readable."""
        _, spans = ingested

        EstimationPipeline(store, scripted(spans)).run(at=AT)

        estimate = store.list_all(Estimate)[0]
        assert estimate.work_class == "migration"
        assert store.versions(estimate.id) == (1, 2)
        assert CLASSIFIER_NAME in {event.actor for event in store.audit_for(estimate.id)}

    def test_a_matched_outcome_is_written_against_its_estimate(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        _, spans = ingested
        provider = scripted(spans, match=resolved(candidate_ordinal(spans, ACTUAL_QUOTE)))

        run = EstimationPipeline(store, provider).run(at=AT)

        outcomes = store.list_all(Outcome)
        assert len(outcomes) == 1
        assert outcomes[0].estimate_id == store.list_all(Estimate)[0].id
        assert outcomes[0].active_quantity == Decimal(7)
        assert len(run.matched) == 1

    def test_the_band_on_the_stored_outcome_is_the_computed_one(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        """4 predicted against 7 actual is a ratio of 1.75, which is `partial`."""
        _, spans = ingested
        provider = scripted(spans, match=resolved(candidate_ordinal(spans, ACTUAL_QUOTE)))

        EstimationPipeline(store, provider).run(at=AT)

        assert store.list_all(Outcome)[0].match_quality is MatchQuality.PARTIAL

    def test_an_unmatched_estimate_still_leaves_a_row_in_the_store(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        """ADR 0022. A dropped estimate is invisible to every Phase 7 query."""
        _, spans = ingested

        run = EstimationPipeline(store, scripted(spans)).run(at=AT)

        outcomes = store.list_all(Outcome)
        assert len(outcomes) == 1
        assert outcomes[0].match_quality is MatchQuality.UNRESOLVED
        assert outcomes[0].active_quantity is None
        assert len(run.unmatched) == 1

    def test_every_estimate_leaves_exactly_one_outcome(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        _, spans = ingested

        EstimationPipeline(store, scripted(spans)).run(at=AT)

        estimates = {estimate.id for estimate in store.list_all(Estimate)}
        against = [outcome.estimate_id for outcome in store.list_all(Outcome)]
        assert sorted(against) == sorted(estimates)
        assert len(against) == len(set(against))

    def test_a_stored_outcome_always_carries_its_estimates_unit(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        """The invariant Phase 7 rests on: it subtracts columns, never converts."""
        _, spans = ingested
        provider = scripted(
            spans,
            match=resolved(candidate_ordinal(spans, ACTUAL_QUOTE), unit="days", active_quantity=35),
        )

        EstimationPipeline(store, provider).run(at=AT)

        estimate = store.list_all(Estimate)[0]
        outcome = store.list_all(Outcome)[0]
        assert outcome.unit is estimate.unit is Unit.WEEKS
        assert outcome.active_quantity == Decimal(7)


class TestRerunning:
    """A second pass over an unchanged store writes nothing and costs nothing."""

    def test_a_second_run_writes_no_new_records(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        _, spans = ingested
        EstimationPipeline(store, scripted(spans)).run(at=AT)
        before = (len(store.list_all(Estimate)), len(store.list_all(Outcome)))

        EstimationPipeline(store, Answering([])).run(at=AT)

        assert (len(store.list_all(Estimate)), len(store.list_all(Outcome))) == before

    def test_a_second_run_costs_no_model_call(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        _, spans = ingested
        EstimationPipeline(store, scripted(spans)).run(at=AT)
        provider = Answering([])

        second = EstimationPipeline(store, provider).run(at=AT)

        assert second.calls == 0
        assert provider.requests == []

    def test_a_second_run_says_why_each_stage_skipped(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        """Three different reasons, reported apart because they are different."""
        _, spans = ingested
        EstimationPipeline(store, scripted(spans)).run(at=AT)

        second = EstimationPipeline(store, Answering([])).run(at=AT)

        assert second.documents[0].already_read
        assert second.already_classified == 1
        assert second.already_matched == 1

    def test_an_estimate_the_classifier_failed_at_is_not_paid_for_twice(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        """Read off the audit trail. Otherwise it costs a call every run forever."""
        _, spans = ingested
        answers = extraction_answers(spans)
        answers += [classified(work_class="!!! not a class"), UNRESOLVED]

        first = EstimationPipeline(store, Answering(answers)).run(at=AT)
        assert first.classified[0].work_class == UNCLASSIFIED

        second = EstimationPipeline(store, Answering([])).run(at=AT)
        assert second.already_attempted == 1
        assert second.classifier_calls == 0

    def test_a_classifier_refusal_is_reported_and_leaves_the_estimate_alone(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        """A refusal is not an unclassified answer, and the two are reported apart.

        The estimate is left at version 1 rather than revised to say the same
        thing: an append-only store gains nothing from a version recording that
        nothing was decided, and the audit trail would then claim the classifier
        had attempted it -- which would stop the next run from trying again.
        """
        _, spans = ingested
        answers: list[object] = list(extraction_answers(spans))
        answers.append(ProviderRefusalError(CLASSIFIER_NAME))
        answers.append(UNRESOLVED)

        run = EstimationPipeline(store, Answering(answers)).run(at=AT)

        assert len(run.classification_refusals) == 1
        assert not run.classified
        estimate = store.list_all(Estimate)[0]
        assert estimate.work_class == UNCLASSIFIED
        assert store.versions(estimate.id) == (1,)


class TestHalfAsRecords:
    """Half B improves Half A's rows without Half A changing."""

    def test_classifies_an_unclassified_estimate_it_did_not_write(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        """`AssumptionExtractor` has been writing these since Phase 4."""
        _, spans = ingested
        cited = next(span for span in spans if ESTIMATE_QUOTE in span.text)
        store.add(
            Estimate(
                id=EstimateId("EST-0001"),
                subject="the search index migration",
                owner="Nadeesha",
                work_class=UNCLASSIFIED,
                active_quantity=Decimal(4),
                blocked_quantity=Decimal(0),
                unit=Unit.WEEKS,
                confidence=0.7,
                estimated_at=AT,
                span_id=cited.id,
                created_by="AssumptionExtractor",
                created_at=AT,
            ),
            actor="extraction",
            reason="a quantified forward-looking claim",
            at=AT,
        )

        answers = [NOTHING] * len(windows_of(spans, DEFAULT_WINDOW_SPANS))
        run = EstimationPipeline(store, Answering([*answers, classified(), UNRESOLVED])).run(at=AT)

        assert [found.work_class for found in run.classified] == ["migration"]
        assert store.require(Estimate, "EST-0001").work_class == "migration"

    def test_a_document_holding_only_half_as_estimate_is_still_read(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        """Skipping it would lose exactly the estimates this agent exists to find."""
        _, spans = ingested
        cited = next(span for span in spans if ESTIMATE_QUOTE in span.text)
        store.add(
            Estimate(
                id=EstimateId("EST-0001"),
                subject="something else entirely",
                owner="Priyanka",
                work_class="backend",
                active_quantity=Decimal(2),
                blocked_quantity=Decimal(0),
                unit=Unit.WEEKS,
                confidence=0.7,
                estimated_at=AT,
                span_id=cited.id,
                created_by="AssumptionExtractor",
                created_at=AT,
            ),
            actor="extraction",
            reason="a quantified forward-looking claim",
            at=AT,
        )

        run = EstimationPipeline(store, scripted(spans, match=UNRESOLVED)).run(at=AT)

        assert not run.documents[0].already_read
        assert run.estimates == 1


class TestRefusals:
    """Everything lost, in one sequence, under the stage that lost it."""

    def test_a_lost_estimate_is_reported_under_the_estimate_stage(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        _, _spans = ingested
        answers = [sighted(9_999)] + [NOTHING] * 8

        run = EstimationPipeline(store, Answering(answers)).run(at=AT)

        assert [lost.stage for lost in run.refused] == [Stage.ESTIMATE]
        assert run.refused[0].lost is RecordKind.ESTIMATE
        assert not store.list_all(Estimate)

    def test_the_stage_vocabulary_covers_all_three_half_b_agents(self) -> None:
        assert {Stage.ESTIMATE, Stage.CLASSIFY, Stage.MATCH} <= set(Stage)


class TestRunShape:
    """What the CLI and the eval harness read off a run."""

    def test_an_empty_run_reports_zeroes_rather_than_dividing_by_none(self) -> None:
        run = EstimationRun()

        assert run.estimates == 0
        assert run.outcomes == 0
        assert run.calls == 0
        assert run.match_rate == Decimal(0)
        assert run.refused == ()
        assert run.blind_windows == 0

    def test_the_match_rate_is_over_every_estimate_asked_about(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        _, spans = ingested
        provider = scripted(spans, match=resolved(candidate_ordinal(spans, ACTUAL_QUOTE)))

        run = EstimationPipeline(store, provider).run(at=AT)

        assert run.outcomes == 1
        assert run.match_rate == Decimal(1)

    def test_calls_totals_all_three_stages(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        _, spans = ingested

        run = EstimationPipeline(store, scripted(spans)).run(at=AT)

        stages = sum(result.calls for result in run.documents)
        assert run.calls == stages + run.classifier_calls + run.matcher_calls
        assert run.classifier_calls == 1
        assert run.matcher_calls == 1


class TestOffline:
    """The mock is the default and the whole pass has to survive it."""

    def test_runs_end_to_end_without_credentials(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        """Whatever the mock says, nothing unverified reaches the store."""
        document, _ = ingested
        sink = MemoryTraceSink()

        run = estimate_store(store, MockProvider(sink=sink, settings=Settings()), at=AT)

        for estimate in store.list_all(Estimate):
            span = store.get(Span, estimate.span_id)
            assert span is not None
            assert verify_span(span, document).ok
        assert run.calls == len(sink.traces)

    def test_the_trace_names_the_three_agents_and_not_the_pass(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        """The trace records who was told; the audit records who wrote."""
        sink = MemoryTraceSink()

        estimate_store(store, MockProvider(sink=sink, settings=Settings()), at=AT)

        assert {trace.agent for trace in sink.traces} <= {
            ESTIMATOR_NAME,
            CLASSIFIER_NAME,
            MATCHER_NAME,
        }
        assert ESTIMATION_ACTOR not in {trace.agent for trace in sink.traces}

    def test_two_runs_over_one_corpus_produce_the_same_numbers(
        self, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        """Determinism is what the ablation table rests on. ADR 0004."""
        results = []
        for _ in range(2):
            connection = connect(MEMORY)
            migrate(connection)
            repository = Repository(connection)
            provider = MockProvider(sink=MemoryTraceSink(), settings=Settings())
            source = MARKDOWN_ADAPTER.normalise(STATUS_BODY.encode("utf-8"), source_uri="status.md")
            IngestionPipeline(repository, provider).ingest_source(source, at=AT)
            run = estimate_store(
                repository, MockProvider(sink=MemoryTraceSink(), settings=Settings()), at=AT
            )
            results.append((run.estimates, run.outcomes, len(run.refused), run.calls))
            repository.close()

        assert results[0] == results[1]

    def test_the_clock_defaults_to_now_and_is_timezone_aware(
        self, store: Repository, ingested: tuple[Document, tuple[Span, ...]]
    ) -> None:
        estimate_store(store, MockProvider(sink=MemoryTraceSink(), settings=Settings()))

        for outcome in store.list_all(Outcome):
            assert outcome.created_at.tzinfo is not None
        assert AT.tzinfo is UTC
