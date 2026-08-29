"""OutcomeMatcher: the three stages, and the arithmetic that is not a model's.

The tests that matter most here are not the happy path. They are:

- **every path leaves a row.** An estimate that reaches this agent always leaves
  it with an `Outcome`, resolved or not, because an estimate with no outcome is
  invisible to every query Phase 7 will run and invisible unresolved estimates
  are how a calibration curve flatters its estimator.
- **the band is arithmetic.** `quality_for` is property-tested and checked
  against this project's own six hand-scored outcomes, where it reproduces five.
  The sixth is asserted as a *difference*, not fixed, because it was scored on
  failed conditions rather than on the ratio.
- **a unit is converted or the pairing is refused, never fudged.** Points into
  weeks has no honest rate and the test says so.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from praxis.agents.classifier import UNCLASSIFIED
from praxis.agents.errors import Refusal
from praxis.agents.matcher import (
    MATCH_TASK,
    MATCHER_NAME,
    MatchedOutcome,
    OutcomeMatcher,
    Unmatched,
    UnmatchedEstimate,
)
from praxis.agents.reconciliation import DEFAULT_MAX_CANDIDATES, candidates_for
from praxis.config.models import ModelRole, role_for_agent
from praxis.domain.enums import MatchQuality, RecordKind, Unit
from praxis.domain.ids import EstimateId, format_sequential_id
from praxis.domain.records import Document, Estimate, Span
from praxis.ingest.verifier import VerifierAgent
from praxis.llm.mock import MockProvider
from praxis.llm.trace import MemoryTraceSink
from praxis.prompts.library import load

from tests.agents.conftest import AT, Answering, Refusing, make_document, spans_of

STATUS_BODY = """\
# Discovery status — week 9

## What shipped

The product search index migration is live in production.

## How it went

Nadeesha put the search index migration at 4 weeks of hands-on work.

The search index migration actually took 7 weeks of hands-on work, Nadeesha
confirmed, with no time lost waiting on anybody.

Separately, the billing outbox rollout came in at 3 weeks against the 3 we
sized it at.

## Next up

Nothing decided about hosted search yet.
"""

ACTUAL_QUOTE = "The search index migration actually took 7 weeks of hands-on work"
WRONG_WORK_QUOTE = "the billing outbox rollout came in at 3 weeks"
INVENTED_QUOTE = "The search index migration actually took 700 weeks of hands-on work"

DOGFOOD = [
    ("OUT-0001", "2.5", "1.1", MatchQuality.PARTIAL),
    ("OUT-0003", "4.5", "5.6", MatchQuality.CLOSE),
    ("OUT-0004", "5.5", "4.2", MatchQuality.CLOSE),
    ("OUT-0005", "6.0", "2.75", MatchQuality.PARTIAL),
    ("OUT-0006", "6.5", "4.6", MatchQuality.CLOSE),
]
"""Five of this project's own outcomes, and the band a hand scored them at.

`OUT-0002` is deliberately absent and has a test of its own: it is the one the
arithmetic disagrees with, and folding it in here would hide that.
"""


@pytest.fixture
def status() -> Document:
    return make_document(STATUS_BODY, doc_id="DOC-0009")


@pytest.fixture
def status_spans(status: Document) -> tuple[Span, ...]:
    return spans_of(status)


def an_estimate(
    spans: tuple[Span, ...],
    *,
    active: str = "4",
    blocked: str = "0",
    unit: Unit = Unit.WEEKS,
    subject: str = "the search index migration",
) -> Estimate:
    """An estimate as the extractor leaves one, citing a passage that is really there."""
    cited = next(span for span in spans if "put the search index migration" in span.text)
    return Estimate(
        id=EstimateId("EST-0001"),
        subject=subject,
        owner="Nadeesha",
        work_class=UNCLASSIFIED,
        active_quantity=Decimal(active),
        blocked_quantity=Decimal(blocked),
        unit=unit,
        confidence=0.7,
        estimated_at=AT,
        span_id=cited.id,
        created_by="EstimateExtractor",
        created_at=AT,
    )


class Allocating:
    """An id allocator that counts, so "exactly one row per estimate" is checkable."""

    def __init__(self) -> None:
        self.calls: list[RecordKind] = []

    def __call__(self, kind: RecordKind) -> str:
        self.calls.append(kind)
        return format_sequential_id(kind, len(self.calls))


def ordinal_of(spans: tuple[Span, ...], estimate: Estimate, quote: str) -> int:
    """Where a quote lands in the listing this estimate would really be shown."""
    offered = candidates_for(estimate, spans)
    for index, span in enumerate(offered):
        if quote in span.text:
            return index
    message = f"{quote!r} is in no passage this estimate would be offered"
    raise AssertionError(message)


def answer(**fields: object) -> str:
    """One outcome answer in the shape the agent asks for."""
    payload = {
        "resolved": True,
        "passage_ordinal": 0,
        "quote": "",
        "active_quantity": 7,
        "blocked_quantity": 0,
        "unit": "weeks",
        "why": "the same migration, the same owner, reported in the past tense",
        "confidence": 0.9,
    }
    payload.update(fields)
    return json.dumps(payload)


class TestRouting:
    """The name buys the tier, and the prompt is part of the contract."""

    def test_is_routed_to_the_extract_tier(self) -> None:
        assert role_for_agent(MATCHER_NAME) is ModelRole.EXTRACT

    def test_reads_a_prompt_that_exists(self) -> None:
        prompt = load(MATCH_TASK)
        assert prompt.id.startswith(MATCH_TASK)
        assert prompt.sha256

    def test_the_prompt_declines_to_ask_for_the_judgement(self) -> None:
        """`match_quality` is arithmetic. The prompt says so, and this holds it there."""
        body = load(MATCH_TASK).render().lower()
        assert "asked how good the estimate was" in body
        assert "same work" in body

    def test_the_listing_ceiling_is_the_one_adr_0015_recorded(self) -> None:
        assert DEFAULT_MAX_CANDIDATES == 12


class TestMatching:
    """One estimate, one document, one call."""

    def test_resolves_an_estimate_and_computes_the_band(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        estimate = an_estimate(status_spans)
        allocator = Allocating()
        provider = Answering(
            [
                answer(
                    passage_ordinal=ordinal_of(status_spans, estimate, ACTUAL_QUOTE),
                    quote=ACTUAL_QUOTE,
                )
            ]
        )

        result = OutcomeMatcher(provider).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert isinstance(result, MatchedOutcome)
        assert result.outcome.active_quantity == Decimal(7)
        assert result.outcome.estimate_id == "EST-0001"
        # 7 against 4 is a ratio of 1.75, which is `partial`.
        assert result.quality is MatchQuality.PARTIAL
        assert result.ratio is not None

    def test_the_outcome_cites_a_span_that_really_contains_the_quote(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        estimate = an_estimate(status_spans)
        allocator = Allocating()
        provider = Answering(
            [
                answer(
                    passage_ordinal=ordinal_of(status_spans, estimate, ACTUAL_QUOTE),
                    quote=ACTUAL_QUOTE,
                )
            ]
        )

        result = OutcomeMatcher(provider).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert isinstance(result, MatchedOutcome)
        span = next(s for s in status_spans if s.id == result.outcome.span_id)
        assert VerifierAgent().verify_claim(result.quote, span, status).ok

    def test_the_outcome_is_stored_in_the_estimates_unit(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """So that Phase 7 subtracts two columns and never converts anything."""
        estimate = an_estimate(status_spans, active="20", unit=Unit.DAYS)
        allocator = Allocating()
        provider = Answering(
            [
                answer(
                    passage_ordinal=ordinal_of(status_spans, estimate, ACTUAL_QUOTE),
                    quote=ACTUAL_QUOTE,
                    active_quantity=7,
                    unit="weeks",
                )
            ]
        )

        result = OutcomeMatcher(provider).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert isinstance(result, MatchedOutcome)
        assert result.outcome.unit is Unit.DAYS
        assert result.outcome.active_quantity == Decimal(35)
        assert result.converted_from is Unit.WEEKS

    def test_a_conversion_says_so_in_the_notes(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """A converted number that does not say it was converted cannot be read back."""
        estimate = an_estimate(status_spans, active="20", unit=Unit.DAYS)
        allocator = Allocating()
        provider = Answering(
            [
                answer(
                    passage_ordinal=ordinal_of(status_spans, estimate, ACTUAL_QUOTE),
                    quote=ACTUAL_QUOTE,
                    unit="weeks",
                )
            ]
        )

        result = OutcomeMatcher(provider).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert isinstance(result, MatchedOutcome)
        assert "converted to days" in result.outcome.notes
        assert "8 hours a day" in result.outcome.notes

    def test_no_conversion_leaves_converted_from_unset(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        estimate = an_estimate(status_spans)
        allocator = Allocating()
        provider = Answering(
            [
                answer(
                    passage_ordinal=ordinal_of(status_spans, estimate, ACTUAL_QUOTE),
                    quote=ACTUAL_QUOTE,
                )
            ]
        )

        result = OutcomeMatcher(provider).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert isinstance(result, MatchedOutcome)
        assert result.converted_from is None
        assert "converted" not in result.outcome.notes


class TestUnmatched:
    """Flagged, never dropped. This is the half of the agent that earns its keep."""

    def test_an_unresolved_estimate_still_produces_an_outcome(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """ADR 0022: invisible unresolved estimates flatter their estimator."""
        estimate = an_estimate(status_spans)
        allocator = Allocating()
        provider = Answering([answer(resolved=False)])

        result = OutcomeMatcher(provider).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert isinstance(result, UnmatchedEstimate)
        assert result.reason is Unmatched.MODEL_FOUND_NONE
        assert result.outcome.match_quality is MatchQuality.UNRESOLVED
        assert result.outcome.estimate_id == "EST-0001"

    def test_an_unresolved_outcome_carries_no_quantity_and_no_resolution_time(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """The record model enforces it; this asserts the agent does not fight it."""
        estimate = an_estimate(status_spans)
        allocator = Allocating()
        provider = Answering([answer(resolved=False)])

        result = OutcomeMatcher(provider).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert isinstance(result, UnmatchedEstimate)
        assert result.outcome.active_quantity is None
        assert result.outcome.blocked_quantity is None
        assert result.outcome.resolved_at is None
        assert result.outcome.span_id is None

    def test_the_reason_is_written_into_the_row_a_person_reads(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        estimate = an_estimate(status_spans)
        allocator = Allocating()
        provider = Answering([answer(resolved=False, why="that actual is a different rollout")])

        result = OutcomeMatcher(provider).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert isinstance(result, UnmatchedEstimate)
        assert "different rollout" in result.outcome.notes
        assert result.outcome.notes.startswith(Unmatched.MODEL_FOUND_NONE.value)

    def test_nothing_to_read_costs_no_call_at_all(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        estimate = an_estimate(status_spans, subject="quantum refrigeration").model_copy(
            update={"owner": "Zzyzx", "span_id": "SPAN-000000000000dead"}
        )
        allocator = Allocating()
        provider = Answering([])

        result = OutcomeMatcher(provider).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert isinstance(result, UnmatchedEstimate)
        assert result.reason is Unmatched.NO_CANDIDATES
        assert result.calls == 0
        assert provider.requests == []

    def test_a_refusing_model_leaves_an_unresolved_row(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        estimate = an_estimate(status_spans)
        allocator = Allocating()

        result = OutcomeMatcher(Refusing([])).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert isinstance(result, UnmatchedEstimate)
        assert result.reason is Unmatched.NO_USABLE_ANSWER

    def test_an_unusable_answer_leaves_an_unresolved_row_and_counts_attempts(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        estimate = an_estimate(status_spans)
        allocator = Allocating()

        result = OutcomeMatcher(Answering(["not json"] * 8)).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert isinstance(result, UnmatchedEstimate)
        assert result.reason is Unmatched.NO_USABLE_ANSWER
        assert result.calls > 1

    def test_a_fabricated_quotation_loses_the_pairing_and_names_the_defect(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        estimate = an_estimate(status_spans)
        allocator = Allocating()
        provider = Answering(
            [
                answer(
                    passage_ordinal=ordinal_of(status_spans, estimate, ACTUAL_QUOTE),
                    quote=INVENTED_QUOTE,
                )
            ]
        )

        result = OutcomeMatcher(provider).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert isinstance(result, UnmatchedEstimate)
        assert result.reason is Unmatched.UNCITED
        assert result.refusal is Refusal.FABRICATED_QUOTE

    def test_an_unoffered_passage_loses_the_pairing(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        estimate = an_estimate(status_spans)
        allocator = Allocating()
        provider = Answering([answer(passage_ordinal=9_999, quote=ACTUAL_QUOTE)])

        result = OutcomeMatcher(provider).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert isinstance(result, UnmatchedEstimate)
        assert result.reason is Unmatched.UNCITED
        assert result.refusal is Refusal.UNOFFERED_SPAN

    def test_an_actual_in_an_incomparable_unit_is_refused_rather_than_converted(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """Points into weeks has no rate this agent could defend, so it does not invent one."""
        estimate = an_estimate(status_spans)
        allocator = Allocating()
        provider = Answering(
            [
                answer(
                    passage_ordinal=ordinal_of(status_spans, estimate, ACTUAL_QUOTE),
                    quote=ACTUAL_QUOTE,
                    unit="points",
                )
            ]
        )

        result = OutcomeMatcher(provider).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert isinstance(result, UnmatchedEstimate)
        assert result.reason is Unmatched.INCOMPARABLE_UNITS
        assert "points" in result.detail
        assert result.outcome.match_quality is MatchQuality.UNRESOLVED

    def test_an_answer_with_no_unit_is_read_as_the_estimates_own(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """Unlike an estimate: here the estimate's unit is a stated fact to fall back on."""
        estimate = an_estimate(status_spans)
        allocator = Allocating()
        provider = Answering(
            [
                answer(
                    passage_ordinal=ordinal_of(status_spans, estimate, ACTUAL_QUOTE),
                    quote=ACTUAL_QUOTE,
                    unit=None,
                )
            ]
        )

        result = OutcomeMatcher(provider).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert isinstance(result, MatchedOutcome)
        assert result.outcome.unit is Unit.WEEKS
        assert result.converted_from is None

    def test_a_negative_actual_is_refused_by_the_record_model(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        """The record model is the authority on what it will hold, here as everywhere.

        `Quantity` is bounded at zero, and this agent does not restate that rule
        in its own answer schema -- it lets the write fail and reports the
        record's own complaint. One place decides what a quantity is.
        """
        estimate = an_estimate(status_spans)
        allocator = Allocating()
        provider = Answering(
            [
                answer(
                    passage_ordinal=ordinal_of(status_spans, estimate, ACTUAL_QUOTE),
                    quote=ACTUAL_QUOTE,
                    active_quantity=-3,
                )
            ]
        )

        result = OutcomeMatcher(provider).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert isinstance(result, UnmatchedEstimate)
        assert result.reason is Unmatched.INCOHERENT_RECORD
        assert "not an outcome this store can hold" in result.detail
        assert result.outcome.match_quality is MatchQuality.UNRESOLVED


class TestEveryPathLeavesARow:
    """The property the whole design rests on."""

    @pytest.mark.parametrize(
        "reply",
        [
            "not json",
            json.dumps({"resolved": False}),
            json.dumps({"resolved": True, "passage_ordinal": 9_999, "quote": "nowhere"}),
            json.dumps(
                {
                    "resolved": True,
                    "passage_ordinal": 0,
                    "quote": ACTUAL_QUOTE,
                    "active_quantity": 7,
                    "unit": "usd",
                }
            ),
        ],
    )
    def test_one_estimate_always_leaves_exactly_one_outcome(
        self, status: Document, status_spans: tuple[Span, ...], reply: str
    ) -> None:
        estimate = an_estimate(status_spans)
        allocator = Allocating()

        result = OutcomeMatcher(Answering([reply] * 8)).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        assert allocator.calls == [RecordKind.OUTCOME]
        assert result.outcome.estimate_id == estimate.id

    def test_a_listing_of_zero_is_a_bug_and_is_raised(self) -> None:
        with pytest.raises(ValueError, match="at least one passage"):
            OutcomeMatcher(Answering([]), max_candidates=0)


class TestOffline:
    """The mock is the default and this has to survive it without credentials."""

    def test_runs_against_the_mock_and_never_writes_an_unverified_citation(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        estimate = an_estimate(status_spans)
        allocator = Allocating()
        sink = MemoryTraceSink()

        result = OutcomeMatcher(MockProvider(sink=sink)).match(
            estimate, status_spans, status, allocate=allocator, at=AT
        )

        if isinstance(result, MatchedOutcome):
            span = next(s for s in status_spans if s.id == result.outcome.span_id)
            assert VerifierAgent().verify_claim(result.quote, span, status).ok
        assert len(sink.traces) == result.calls
        assert {trace.agent for trace in sink.traces} == {MATCHER_NAME}

    def test_the_request_names_the_agent_the_prompt_and_the_record(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        estimate = an_estimate(status_spans)
        provider = Answering([answer(resolved=False)])

        OutcomeMatcher(provider).match(estimate, status_spans, status, allocate=Allocating(), at=AT)

        request = provider.requests[0]
        assert request.agent == MATCHER_NAME
        assert request.task == MATCH_TASK
        assert request.prompt_id.startswith(MATCH_TASK)
        assert request.metadata["estimate_id"] == "EST-0001"

    def test_the_estimate_is_described_to_the_model_in_its_own_units(
        self, status: Document, status_spans: tuple[Span, ...]
    ) -> None:
        estimate = an_estimate(status_spans)
        provider = Answering([answer(resolved=False)])

        OutcomeMatcher(provider).match(estimate, status_spans, status, allocate=Allocating(), at=AT)

        asked = provider.requests[0].messages[0].content
        assert "4 weeks of hands-on work" in asked
        assert "Nadeesha" in asked

    def test_the_clock_it_is_given_is_timezone_aware(self) -> None:
        assert AT.tzinfo is UTC
        assert datetime(2026, 5, 1, tzinfo=UTC).tzinfo is not None
