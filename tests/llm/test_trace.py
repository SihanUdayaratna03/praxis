"""The trace row, and the sink it goes to.

The trace store is the evidence that the pipeline did what a report says it
did, so the rules asserted here are about a row being unable to lie: a failure
that carries no message, a success that carries one, a naive timestamp, or a
cost that went backwards would each make the table readable and untrue.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from praxis.config.models import MOCK_MODEL_ID, ModelRole
from praxis.llm.trace import LLMTrace, MemoryTraceSink, TraceSink, new_run_id
from praxis.llm.types import CallOutcome, StopReason, TokenUsage


def trace(**overrides) -> LLMTrace:
    """Build a valid trace row, overriding one field at a time."""
    fields = {
        "run_id": "RUN-000000000001",
        "agent": "DecisionScout",
        "task": "scan_for_decisions",
        "role": ModelRole.SCAN,
        "provider": "mock",
        "model_id": MOCK_MODEL_ID,
        "prompt_hash": "0" * 32,
        "outcome": CallOutcome.OK,
        "usage": TokenUsage(input_tokens=40, output_tokens=9),
        "cost_usd": Decimal("0"),
        "latency_ms": 3,
        "request_json": '{"agent":"DecisionScout"}',
        "stop_reason": StopReason.END_TURN,
        "response_text": "{}",
    }
    fields.update(overrides)
    return LLMTrace(**fields)


class TestRunId:
    def test_two_runs_are_told_apart(self):
        assert new_run_id() != new_run_id()

    def test_it_is_recognisable_in_a_table(self):
        assert new_run_id().startswith("RUN-")


class TestRow:
    def test_a_valid_row_is_accepted(self):
        assert trace().succeeded

    def test_the_timestamp_is_timezone_aware_by_default(self):
        # Invariant 5. A naive timestamp here makes "which run was first"
        # depend on which machine wrote the row.
        assert trace().occurred_at.tzinfo is not None

    def test_a_naive_timestamp_is_refused(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            trace(occurred_at=datetime(2026, 8, 16, 12, 0))  # noqa: DTZ001 -- the point

    def test_an_aware_timestamp_is_kept_as_given(self):
        when = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
        assert trace(occurred_at=when).occurred_at == when

    def test_negative_latency_is_a_bug_not_a_measurement(self):
        with pytest.raises(ValueError, match="latency_ms"):
            trace(latency_ms=-1)

    def test_a_negative_cost_is_refused(self):
        with pytest.raises(ValueError, match="cost_usd"):
            trace(cost_usd=Decimal("-0.01"))

    def test_attempts_are_numbered_from_one(self):
        with pytest.raises(ValueError, match="numbered from 1"):
            trace(attempt=0)

    def test_a_repair_attempt_is_recorded_as_its_own_row(self):
        # One row per attempt, not one per call: the failed attempt is the
        # interesting one and it must not be overwritten by the retry.
        assert trace(attempt=2).attempt == 2


class TestFailuresCannotBeSilent:
    def test_an_error_outcome_must_carry_a_message(self):
        with pytest.raises(ValueError, match="error outcome"):
            trace(outcome=CallOutcome.ERROR, error=None)

    def test_a_successful_outcome_must_not_carry_one(self):
        with pytest.raises(ValueError, match="error outcome"):
            trace(error="something went wrong")

    def test_an_errored_call_is_still_a_row(self):
        # A call that never reached the model is the row a report needs most.
        failed = trace(
            outcome=CallOutcome.ERROR,
            error="the provider was unreachable",
            stop_reason=None,
            response_text="",
        )
        assert not failed.succeeded

    def test_a_malformed_answer_keeps_its_raw_text(self):
        # Held as text rather than parsed, so the evidence survives the
        # failure that makes it worth having.
        malformed = trace(outcome=CallOutcome.MALFORMED, response_text="{ oops")
        assert malformed.response_text == "{ oops"
        assert not malformed.succeeded

    def test_a_refusal_is_not_an_error(self):
        # It arrives as a successful response, so it is its own outcome and
        # carries no error message.
        refused = trace(outcome=CallOutcome.REFUSED, stop_reason=StopReason.REFUSAL)
        assert not refused.succeeded


class TestMemorySink:
    def test_it_satisfies_the_protocol(self):
        # The point of the protocol: praxis.llm never imports praxis.store, so
        # anything with record() can stand in for the database.
        assert isinstance(MemoryTraceSink(), TraceSink)

    def test_rows_arrive_in_the_order_they_were_recorded(self):
        sink = MemoryTraceSink()
        sink.record(trace(attempt=1))
        sink.record(trace(attempt=2))
        assert [row.attempt for row in sink.traces] == [1, 2]

    def test_it_sums_what_a_run_spent(self):
        sink = MemoryTraceSink()
        sink.record(trace(cost_usd=Decimal("0.01")))
        sink.record(trace(cost_usd=Decimal("0.02")))
        assert sink.total_cost_usd == Decimal("0.03")

    def test_the_sum_of_nothing_is_a_decimal_zero(self):
        # Invariant 4 survives the empty case: sum() would return int 0.
        total = MemoryTraceSink().total_cost_usd
        assert isinstance(total, Decimal)
        assert total == Decimal("0")

    def test_it_sums_tokens_across_calls(self):
        sink = MemoryTraceSink()
        sink.record(trace(usage=TokenUsage(input_tokens=10, output_tokens=2)))
        sink.record(trace(usage=TokenUsage(input_tokens=5, cache_read_input_tokens=100)))
        assert sink.total_usage.input_tokens == 15
        assert sink.total_usage.cache_read_input_tokens == 100
        assert sink.total_usage.total == 117

    def test_two_sinks_do_not_share_rows(self):
        first, second = MemoryTraceSink(), MemoryTraceSink()
        first.record(trace())
        assert second.traces == []
