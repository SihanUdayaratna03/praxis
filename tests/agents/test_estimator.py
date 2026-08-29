"""EstimateExtractor, against answers built to be wrong in specific ways.

The fixture is a status update rather than an ADR, and that is the point of the
agent: Half A finds an estimate only inside an assumption it was already paid to
read, and most estimates in a real corpus are nowhere near one. This document
states four quantities -- a plain estimate, one split into work and waiting, one
with no unit at all, and an *actual* -- because the four ways this agent can be
wrong are all in that list.

Ordinals are computed from the offering rather than written down as constants.
A window restarts its numbering at zero, so a hard-coded ordinal here would be
asserting the fixture's block layout by accident and would move the moment a
sentence in it changed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from praxis.agents.classifier import UNCLASSIFIED
from praxis.agents.errors import Refusal
from praxis.agents.estimator import (
    DEFAULT_WINDOW_SPANS,
    ESTIMATE_TASK,
    ESTIMATOR_NAME,
    NO_SPLIT,
    EstimateExtraction,
    EstimateExtractor,
)
from praxis.agents.extractor import NOT_STATED
from praxis.agents.offering import windows_of
from praxis.config.models import ModelRole, role_for_agent
from praxis.domain.enums import RecordKind, Unit
from praxis.domain.ids import format_sequential_id
from praxis.domain.records import Document, Span
from praxis.ingest.verifier import VerifierAgent
from praxis.llm.mock import MockProvider
from praxis.llm.trace import MemoryTraceSink
from praxis.prompts.library import load

from tests.agents.conftest import AT, Answering, Refusing, make_document, spans_of

STATUS_BODY = """\
# Discovery status — week 3

## What shipped

The product search index is live in production.

## Estimates and actuals

Nadeesha put the search index migration at 4 weeks of hands-on work, and that
is the number we planned against.

Priyanka sized the billing outbox at 3 weeks of build plus roughly 2 weeks
waiting on the payments vendor to turn the sandbox on.

Somebody put the mobile offline cache at "a couple of sprints", which is not a
number anybody can plan against.

It actually took 7 weeks of hands-on work.

## Next up

We might look at hosted search again if the vendor drops their price.
"""

SEARCH_QUOTE = "Nadeesha put the search index migration at 4 weeks of hands-on work"
BILLING_QUOTE = "Priyanka sized the billing outbox at 3 weeks of build"
SPRINTS_QUOTE = 'Somebody put the mobile offline cache at "a couple of sprints"'
ACTUAL_QUOTE = "It actually took 7 weeks of hands-on work."
INVENTED_QUOTE = "Nadeesha put the search index migration at 40 years of hands-on work"


@pytest.fixture
def status() -> Document:
    """A status update, through the real adapter, so its offsets are real."""
    return make_document(STATUS_BODY, doc_id="DOC-0007")


@pytest.fixture
def status_spans(status: Document) -> tuple[Span, ...]:
    return spans_of(status)


class Allocating:
    """An id allocator that counts, so a wasted id is visible.

    Ids are sequential and a store with a gap in them is a store nobody can
    explain, so "was an id spent on a sighting that was refused" is a property
    worth asserting rather than a detail.
    """

    def __init__(self) -> None:
        self.calls: list[RecordKind] = []

    def __call__(self, kind: RecordKind) -> str:
        self.calls.append(kind)
        return format_sequential_id(kind, len(self.calls))


def locate(spans: tuple[Span, ...], quote: str, *, window_spans: int = DEFAULT_WINDOW_SPANS) -> int:
    """The ordinal a quote sits at, in the window that holds it.

    Computed rather than written down: ordinals restart at zero per window, so
    the number depends on the fixture's block layout and asserting that layout
    by hand is asserting the wrong thing.
    """
    for window in windows_of(spans, window_spans):
        for entry in window.entries:
            if quote in entry.span.text:
                return entry.ordinal
    message = f"no span in the fixture contains {quote!r}"
    raise AssertionError(message)


def window_holding(
    spans: tuple[Span, ...], quote: str, *, window_spans: int = DEFAULT_WINDOW_SPANS
) -> int:
    """Which window a quote falls in, so a test can answer only for that one."""
    for index, window in enumerate(windows_of(spans, window_spans)):
        if any(quote in entry.span.text for entry in window.entries):
            return index
    message = f"no window in the fixture contains {quote!r}"
    raise AssertionError(message)


def sighting(**fields: object) -> str:
    """One estimate in the shape the agent asks for, with usable defaults."""
    entry = {
        "passage_ordinal": 0,
        "quote": "",
        "subject": "the search index migration",
        "owner": "Nadeesha",
        "active_quantity": 4,
        "blocked_quantity": 0,
        "unit": "weeks",
        "confidence": 0.8,
    }
    entry.update(fields)
    return json.dumps({"estimates": [entry]})


def answers_for(spans: tuple[Span, ...], quote: str, payload: str) -> list[str]:
    """One real answer in the window holding `quote`, and silence elsewhere.

    The agent calls once per window, so a test that supplies a single answer
    would have it consumed by whichever window came first.
    """
    count = len(windows_of(spans, DEFAULT_WINDOW_SPANS))
    empty = json.dumps({"estimates": []})
    target = window_holding(spans, quote)
    return [payload if index == target else empty for index in range(count)]


def extract(
    provider: Answering, spans: tuple[Span, ...], document: Document, allocator: Allocating
) -> EstimateExtraction:
    """Run the agent the way the pipeline will."""
    return EstimateExtractor(provider).extract(spans, document, allocate=allocator, at=AT)


class TestRouting:
    """The agent's name is what buys it a model, so the name is the contract."""

    def test_is_routed_to_the_scan_tier(self) -> None:
        assert role_for_agent(ESTIMATOR_NAME) is ModelRole.SCAN

    def test_reads_a_prompt_that_exists(self) -> None:
        prompt = load(ESTIMATE_TASK)
        assert prompt.id.startswith(ESTIMATE_TASK)
        assert prompt.sha256

    def test_the_prompt_names_an_actual_as_the_thing_not_to_report(self) -> None:
        """The confusable case is a prompt property, so it is asserted as one."""
        body = load(ESTIMATE_TASK).render().lower()
        assert "actual" in body
        assert "past tense" in body


class TestExtraction:
    """The happy path, and every field it is responsible for filling."""

    def test_finds_an_estimate_and_cites_the_passage_it_is_in(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        allocator = Allocating()
        payload = sighting(passage_ordinal=locate(status_spans, SEARCH_QUOTE), quote=SEARCH_QUOTE)
        provider = Answering(answers_for(status_spans, SEARCH_QUOTE, payload))

        result = extract(provider, status_spans, status, allocator)

        assert len(result.found) == 1
        estimate = result.found[0].estimate
        assert estimate.owner == "Nadeesha"
        assert estimate.active_quantity == Decimal(4)
        assert estimate.unit is Unit.WEEKS
        assert SEARCH_QUOTE in next(
            span.text for span in status_spans if span.id == estimate.span_id
        )

    def test_the_quote_really_is_in_the_span_the_record_cites(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """Invariant 6, checked by the same deterministic gate that enforces it."""
        allocator = Allocating()
        payload = sighting(passage_ordinal=locate(status_spans, SEARCH_QUOTE), quote=SEARCH_QUOTE)
        provider = Answering(answers_for(status_spans, SEARCH_QUOTE, payload))

        result = extract(provider, status_spans, status, allocator)

        span = next(s for s in status_spans if s.id == result.found[0].estimate.span_id)
        assert VerifierAgent().verify_claim(result.found[0].quote, span, status).ok

    def test_work_class_is_unclassified_and_never_guessed(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """`WorkClassifier` owns the field. A plausible guess is the expensive error."""
        allocator = Allocating()
        payload = sighting(passage_ordinal=locate(status_spans, SEARCH_QUOTE), quote=SEARCH_QUOTE)
        provider = Answering(answers_for(status_spans, SEARCH_QUOTE, payload))

        result = extract(provider, status_spans, status, allocator)

        assert result.found[0].estimate.work_class == UNCLASSIFIED

    def test_a_split_quantity_is_kept_split(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        allocator = Allocating()
        payload = sighting(
            passage_ordinal=locate(status_spans, BILLING_QUOTE),
            quote=BILLING_QUOTE,
            subject="the billing outbox",
            owner="Priyanka",
            active_quantity=3,
            blocked_quantity=2,
        )
        provider = Answering(answers_for(status_spans, BILLING_QUOTE, payload))

        result = extract(provider, status_spans, status, allocator)

        estimate = result.found[0].estimate
        assert estimate.active_quantity == Decimal(3)
        assert estimate.blocked_quantity == Decimal(2)
        assert estimate.total_quantity == Decimal(5)

    def test_a_missing_block_is_zero_rather_than_absent(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        allocator = Allocating()
        payload = sighting(
            passage_ordinal=locate(status_spans, SEARCH_QUOTE),
            quote=SEARCH_QUOTE,
            blocked_quantity=None,
        )
        provider = Answering(answers_for(status_spans, SEARCH_QUOTE, payload))

        result = extract(provider, status_spans, status, allocator)

        assert result.found[0].estimate.blocked_quantity == NO_SPLIT

    def test_an_unnamed_owner_is_recorded_as_such_never_invented(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """An invented name puts one person's miss in another person's history."""
        allocator = Allocating()
        payload = sighting(
            passage_ordinal=locate(status_spans, SEARCH_QUOTE), quote=SEARCH_QUOTE, owner=None
        )
        provider = Answering(answers_for(status_spans, SEARCH_QUOTE, payload))

        result = extract(provider, status_spans, status, allocator)

        assert result.found[0].estimate.owner == NOT_STATED

    def test_a_missing_subject_falls_back_to_the_verified_quote(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        allocator = Allocating()
        payload = sighting(
            passage_ordinal=locate(status_spans, SEARCH_QUOTE), quote=SEARCH_QUOTE, subject=None
        )
        provider = Answering(answers_for(status_spans, SEARCH_QUOTE, payload))

        result = extract(provider, status_spans, status, allocator)

        assert result.found[0].estimate.subject == SEARCH_QUOTE

    def test_estimated_at_falls_back_to_the_run_clock(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        allocator = Allocating()
        payload = sighting(passage_ordinal=locate(status_spans, SEARCH_QUOTE), quote=SEARCH_QUOTE)
        provider = Answering(answers_for(status_spans, SEARCH_QUOTE, payload))

        result = extract(provider, status_spans, status, allocator)

        assert result.found[0].estimate.estimated_at == AT
        assert result.found[0].estimate.estimated_at.tzinfo is not None

    def test_an_actual_cited_as_an_estimate_is_not_caught_by_this_agent(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """The guard against an actual lives in the prompt, and this says so.

        Recorded as a test rather than left implicit because it is the single
        most confusable passage in the corpus and a reader would otherwise
        assume the code checks the tense. It does not, and it cannot: "took"
        and "will take" are the same shape to a citation gate. `OutcomeMatcher`
        is what those passages are for, and the eval table is what measures how
        often the prompt holds.
        """
        allocator = Allocating()
        payload = sighting(
            passage_ordinal=locate(status_spans, ACTUAL_QUOTE),
            quote=ACTUAL_QUOTE,
            active_quantity=7,
        )
        provider = Answering(answers_for(status_spans, ACTUAL_QUOTE, payload))

        result = extract(provider, status_spans, status, allocator)

        assert len(result.found) == 1


class TestRefusals:
    """The rest of the file. An agent's happy path is one function."""

    def test_a_quantity_with_no_unit_is_refused_rather_than_defaulted(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """Four weeks stored as four hours fails nowhere and is wrong everywhere."""
        allocator = Allocating()
        payload = sighting(
            passage_ordinal=locate(status_spans, SPRINTS_QUOTE),
            quote=SPRINTS_QUOTE,
            subject="the mobile offline cache",
            owner=None,
            active_quantity=2,
            unit=None,
        )
        provider = Answering(answers_for(status_spans, SPRINTS_QUOTE, payload))

        result = extract(provider, status_spans, status, allocator)

        assert not result.found
        assert [rejected.refusal for rejected in result.rejections] == [Refusal.INCOHERENT_RECORD]
        assert "cannot be compared to an outcome" in result.rejections[0].detail

    def test_an_estimate_of_nothing_is_refused_by_the_record_model(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """`Estimate` says an estimate of zero predicts nothing. It is the authority."""
        allocator = Allocating()
        payload = sighting(
            passage_ordinal=locate(status_spans, SEARCH_QUOTE),
            quote=SEARCH_QUOTE,
            active_quantity=0,
            blocked_quantity=0,
        )
        provider = Answering(answers_for(status_spans, SEARCH_QUOTE, payload))

        result = extract(provider, status_spans, status, allocator)

        assert not result.found
        assert result.rejections[0].refusal is Refusal.INCOHERENT_RECORD
        assert "predicts nothing" in result.rejections[0].detail

    def test_an_ordinal_that_was_never_offered_is_refused_as_such(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        allocator = Allocating()
        payload = sighting(passage_ordinal=9_999, quote=SEARCH_QUOTE)
        provider = Answering(answers_for(status_spans, SEARCH_QUOTE, payload))

        result = extract(provider, status_spans, status, allocator)

        assert not result.found
        assert result.rejections[0].refusal is Refusal.UNOFFERED_SPAN
        assert result.rejections[0].ordinal == 9_999

    def test_a_quotation_in_no_offered_passage_is_a_fabrication(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        allocator = Allocating()
        payload = sighting(passage_ordinal=locate(status_spans, SEARCH_QUOTE), quote=INVENTED_QUOTE)
        provider = Answering(answers_for(status_spans, SEARCH_QUOTE, payload))

        result = extract(provider, status_spans, status, allocator)

        assert not result.found
        assert result.rejections[0].refusal is Refusal.FABRICATED_QUOTE

    def test_a_quotation_from_another_offered_passage_is_a_mis_attribution(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """Read the material, pointed at the wrong part of it. A different defect."""
        allocator = Allocating()
        payload = sighting(passage_ordinal=locate(status_spans, SEARCH_QUOTE), quote=BILLING_QUOTE)
        provider = Answering(answers_for(status_spans, SEARCH_QUOTE, payload))

        result = extract(provider, status_spans, status, allocator)

        assert not result.found
        assert result.rejections[0].refusal is Refusal.MIS_ATTRIBUTED_QUOTE

    def test_a_refused_sighting_never_spends_an_id(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """Sequential ids with an unexplained gap are a store nobody can read."""
        allocator = Allocating()
        payload = sighting(passage_ordinal=9_999, quote=SEARCH_QUOTE)
        provider = Answering(answers_for(status_spans, SEARCH_QUOTE, payload))

        extract(provider, status_spans, status, allocator)

        assert allocator.calls == []

    def test_a_verified_sighting_spends_exactly_one(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        allocator = Allocating()
        payload = sighting(passage_ordinal=locate(status_spans, SEARCH_QUOTE), quote=SEARCH_QUOTE)
        provider = Answering(answers_for(status_spans, SEARCH_QUOTE, payload))

        extract(provider, status_spans, status, allocator)

        assert allocator.calls == [RecordKind.ESTIMATE]


class TestDegradation:
    """A bad answer about one document is not a broken run."""

    def test_a_refusing_model_makes_the_window_blind_rather_than_raising(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        allocator = Allocating()
        provider = Refusing([])

        result = extract(provider, status_spans, status, allocator)

        assert result.blind
        assert result.blind_windows == result.windows
        assert not result.found
        assert not result.rejections

    def test_an_unusable_answer_is_blind_and_still_counts_its_attempts(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """A repair budget spent is a cost, and a cost nobody counted is a lie."""
        allocator = Allocating()
        count = len(windows_of(status_spans, DEFAULT_WINDOW_SPANS))
        provider = Answering(["not json at all"] * (count * 8))

        result = extract(provider, status_spans, status, allocator)

        assert result.blind_windows == result.windows
        assert result.calls > result.windows

    def test_one_bad_window_does_not_cost_the_others(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        allocator = Allocating()
        target = window_holding(status_spans, SEARCH_QUOTE)
        count = len(windows_of(status_spans, DEFAULT_WINDOW_SPANS))
        if count < 2:
            pytest.skip("the fixture fits in one window, so there is no other to lose")
        payload = sighting(passage_ordinal=locate(status_spans, SEARCH_QUOTE), quote=SEARCH_QUOTE)
        provider = Answering(
            [payload if index == target else "{ broken" for index in range(count * 8)]
        )

        result = extract(provider, status_spans, status, allocator)

        assert len(result.found) == 1
        assert result.blind_windows == count - 1

    def test_a_document_with_no_spans_costs_nothing(self, status: Document) -> None:
        allocator = Allocating()
        provider = Answering([])

        result = EstimateExtractor(provider).extract((), status, allocate=allocator, at=AT)

        assert result == EstimateExtraction()
        assert result.calls == 0

    def test_a_window_of_zero_is_a_bug_and_is_raised(self) -> None:
        with pytest.raises(ValueError, match="at least one span"):
            EstimateExtractor(Answering([]), window_spans=0)


class TestWindows:
    """One call per window, and ordinals that mean what the window says."""

    def test_every_window_is_asked_exactly_once_when_answers_are_usable(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        allocator = Allocating()
        count = len(windows_of(status_spans, DEFAULT_WINDOW_SPANS))
        provider = Answering([json.dumps({"estimates": []})] * count)

        result = extract(provider, status_spans, status, allocator)

        assert result.windows == count
        assert result.calls == count
        assert len(provider.requests) == count

    def test_the_offering_the_model_sees_is_the_one_it_cites_into(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """A window numbers from zero, so ordinal 0 is this window's first span."""
        allocator = Allocating()
        windows = windows_of(status_spans, DEFAULT_WINDOW_SPANS)
        first = windows[0]
        quote = first.entries[0].span.text.strip().splitlines()[0]
        payload = sighting(passage_ordinal=0, quote=quote, active_quantity=1)
        provider = Answering([payload] + [json.dumps({"estimates": []})] * (len(windows) - 1))

        result = extract(provider, status_spans, status, allocator)

        assert result.found[0].estimate.span_id == first.entries[0].span.id

    def test_each_request_carries_the_agent_and_the_prompt_behind_it(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        allocator = Allocating()
        count = len(windows_of(status_spans, DEFAULT_WINDOW_SPANS))
        provider = Answering([json.dumps({"estimates": []})] * count)

        extract(provider, status_spans, status, allocator)

        request = provider.requests[0]
        assert request.agent == ESTIMATOR_NAME
        assert request.task == ESTIMATE_TASK
        assert request.prompt_id.startswith(ESTIMATE_TASK)
        assert request.metadata["doc_id"] == status.id


class TestOffline:
    """The mock is the default and the whole pipeline has to survive it."""

    def test_runs_against_the_mock_without_credentials_and_writes_nothing_unverified(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """The refusal rate offline is a finding, not a failure. This asserts safety."""
        allocator = Allocating()
        sink = MemoryTraceSink()
        provider = MockProvider(sink=sink)
        verifier = VerifierAgent()

        result = EstimateExtractor(provider).extract(
            status_spans, status, allocate=allocator, at=AT
        )

        for found in result.found:
            span = next(s for s in status_spans if s.id == found.estimate.span_id)
            assert verifier.verify_claim(found.quote, span, status).ok
        assert result.calls == len(sink.traces)

    def test_the_trace_records_one_row_per_attempt(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        allocator = Allocating()
        sink = MemoryTraceSink()
        provider = MockProvider(sink=sink)

        result = EstimateExtractor(provider).extract(
            status_spans, status, allocate=allocator, at=AT
        )

        assert len(sink.traces) == result.calls
        assert {trace.agent for trace in sink.traces} == {ESTIMATOR_NAME}


class TestResultShape:
    """What the pipeline reads off a run."""

    def test_estimates_is_the_records_alone(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        allocator = Allocating()
        payload = sighting(passage_ordinal=locate(status_spans, SEARCH_QUOTE), quote=SEARCH_QUOTE)
        provider = Answering(answers_for(status_spans, SEARCH_QUOTE, payload))

        result = extract(provider, status_spans, status, allocator)

        assert result.estimates == (result.found[0].estimate,)

    def test_an_empty_extraction_is_not_blind(self) -> None:
        """Nothing found and nothing asked are different, and the flag says which."""
        assert not EstimateExtraction().blind

    def test_the_clock_is_timezone_aware_by_construction(self) -> None:
        assert AT.tzinfo is UTC
        assert datetime(2026, 1, 1, tzinfo=UTC).tzinfo is not None
