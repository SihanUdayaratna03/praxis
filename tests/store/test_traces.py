"""The trace table.

Three properties are being defended. A trace survives the round trip through
SQLite unchanged, including the parts a lazy mapping would quietly drop -- an
exact cost, an offset-aware timestamp, four token counters. A trace cannot be
edited or erased once written, because a record of what a model was asked is
only evidence while that is true. And the table stays out of the graph: it has
no `node` row, so the audit trail and the trace store remain two things.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.config.models import MOCK_MODEL_ID, ModelRole
from praxis.llm.trace import LLMTrace, TraceSink
from praxis.llm.types import CallOutcome, StopReason, TokenUsage
from praxis.store.connection import MEMORY, connect, transaction
from praxis.store.errors import AppendOnlyViolationError, StoreError
from praxis.store.migrations import migrate
from praxis.store.traces import (
    SqliteTraceSink,
    from_row,
    prompt_versions_in_run,
    run_cost_usd,
    to_row,
    trace_count,
    traces_for_prompt,
    traces_in_run,
    write_trace,
)

RUN = "RUN-000000000001"


@pytest.fixture
def store() -> Iterator[sqlite3.Connection]:
    """An empty, migrated store."""
    connection = connect(MEMORY)
    migrate(connection)
    yield connection
    connection.close()


def trace(**overrides) -> LLMTrace:
    """Build a valid trace row, overriding one field at a time."""
    fields = {
        "run_id": RUN,
        "agent": "DecisionScout",
        "task": "scan_for_decisions",
        "role": ModelRole.SCAN,
        "provider": "mock",
        "model_id": MOCK_MODEL_ID,
        "prompt_hash": "a" * 32,
        "outcome": CallOutcome.OK,
        "usage": TokenUsage(input_tokens=40, output_tokens=9),
        "cost_usd": Decimal("0"),
        "latency_ms": 3,
        "request_json": '{"agent":"DecisionScout"}',
        "stop_reason": StopReason.END_TURN,
        "response_text": "{}",
        "occurred_at": datetime(2026, 8, 16, 9, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return LLMTrace(**fields)


class TestRoundTrip:
    def test_a_trace_comes_back_as_it_went_in(self, store):
        original = trace()
        write_trace(store, original)
        assert traces_in_run(store, RUN) == (original,)

    def test_the_cost_survives_as_an_exact_decimal(self, store):
        # Invariant 4. A REAL column would round this and the eval total would
        # stop being reproducible.
        cost = Decimal("0.00013275")
        write_trace(store, trace(cost_usd=cost))
        stored = traces_in_run(store, RUN)[0].cost_usd
        assert stored == cost
        assert str(stored) == str(cost)

    def test_the_timestamp_keeps_its_offset(self, store):
        write_trace(store, trace())
        assert traces_in_run(store, RUN)[0].occurred_at.tzinfo is not None

    def test_every_token_counter_survives(self, store):
        usage = TokenUsage(
            input_tokens=1,
            output_tokens=2,
            cache_read_input_tokens=3,
            cache_creation_input_tokens=4,
        )
        write_trace(store, trace(usage=usage))
        assert traces_in_run(store, RUN)[0].usage == usage

    def test_a_failed_call_survives_with_its_message(self, store):
        failed = trace(
            outcome=CallOutcome.ERROR,
            error="the provider was unreachable",
            stop_reason=None,
            response_text="",
        )
        write_trace(store, failed)
        assert traces_in_run(store, RUN)[0] == failed

    def test_the_mapping_covers_every_column(self, store):
        # The check that catches a column added to the write side and
        # forgotten on the read side, which otherwise returns a default.
        write_trace(store, trace())
        row = store.execute("SELECT * FROM llm_trace").fetchone()
        assert set(row.keys()) - {"seq"} == set(to_row(trace()))

    @given(
        latency=st.integers(min_value=0, max_value=600_000),
        attempt=st.integers(min_value=1, max_value=5),
        text=st.text(max_size=60),
    )
    def test_any_valid_trace_round_trips(self, latency, attempt, text):
        connection = connect(MEMORY)
        migrate(connection)
        try:
            original = trace(latency_ms=latency, attempt=attempt, response_text=text)
            write_trace(connection, original)
            assert traces_in_run(connection, RUN)[0] == original
        finally:
            connection.close()


class TestOrdering:
    def test_calls_come_back_in_the_order_they_were_made(self, store):
        for attempt in (1, 2, 3):
            write_trace(store, trace(attempt=attempt))
        assert [row.attempt for row in traces_in_run(store, RUN)] == [1, 2, 3]

    def test_two_calls_in_one_millisecond_still_have_an_order(self, store):
        # Ordered by the sequence SQLite allocates, not by the timestamp:
        # occurred_at is not unique and a run makes calls in bursts.
        when = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
        first = write_trace(store, trace(occurred_at=when, attempt=1))
        second = write_trace(store, trace(occurred_at=when, attempt=2))
        assert second > first
        assert [row.attempt for row in traces_in_run(store, RUN)] == [1, 2]

    def test_another_run_is_not_included(self, store):
        write_trace(store, trace())
        write_trace(store, trace(run_id="RUN-000000000002"))
        assert len(traces_in_run(store, RUN)) == 1
        assert trace_count(store) == 2


class TestQueries:
    def test_the_same_question_asked_twice_is_a_lookup(self, store):
        # What the replay key is for: two rows here with different answers are
        # evidence of non-determinism, not a curiosity.
        write_trace(store, trace(response_text="one"))
        write_trace(store, trace(response_text="two"))
        write_trace(store, trace(prompt_hash="b" * 32))
        asked = traces_for_prompt(store, "a" * 32)
        assert [row.response_text for row in asked] == ["one", "two"]

    def test_a_run_costs_the_sum_of_its_calls(self, store):
        write_trace(store, trace(cost_usd=Decimal("0.01")))
        write_trace(store, trace(cost_usd=Decimal("0.02")))
        assert run_cost_usd(store, RUN) == Decimal("0.03")

    def test_a_run_that_made_no_calls_cost_a_decimal_zero(self, store):
        total = run_cost_usd(store, "RUN-nothing")
        assert isinstance(total, Decimal)
        assert total == Decimal("0")

    def test_the_sum_is_exact_where_a_float_would_not_be(self, store):
        # Three tenths of a cent, summed ten times. In binary floating point
        # this is 0.029999999999999995.
        for _ in range(10):
            write_trace(store, trace(cost_usd=Decimal("0.003")))
        assert run_cost_usd(store, RUN) == Decimal("0.030")

    def test_an_empty_store_has_recorded_nothing(self, store):
        assert trace_count(store) == 0


class TestAppendOnly:
    def test_a_trace_cannot_be_edited(self, store):
        write_trace(store, trace())
        with pytest.raises(AppendOnlyViolationError), transaction(store):
            store.execute("UPDATE llm_trace SET response_text = 'edited'")

    def test_a_trace_cannot_be_deleted(self, store):
        write_trace(store, trace())
        with pytest.raises(AppendOnlyViolationError), transaction(store):
            store.execute("DELETE FROM llm_trace")
        assert trace_count(store) == 1


class TestTheSchemaRefusesALie:
    def test_a_naive_timestamp_is_refused_by_the_database_too(self, store):
        # The dataclass rejects this as well. Both, because invariant 5 has to
        # hold for a row written by hand at the sqlite3 shell.
        row = to_row(trace())
        row["occurred_at"] = "2026-08-16T09:00:00"
        with pytest.raises(StoreError), transaction(store):
            store.execute(_insert_sql(), row)

    def test_an_error_with_no_message_is_refused(self, store):
        row = to_row(trace())
        row["outcome"] = "error"
        with pytest.raises(StoreError), transaction(store):
            store.execute(_insert_sql(), row)

    def test_an_unknown_provider_is_refused(self, store):
        # The provider column is what keeps a metric from being computed over a
        # mixture of mock and live rows, so an unrecognised value is a bug.
        row = to_row(trace())
        row["provider"] = "guesswork"
        with pytest.raises(StoreError), transaction(store):
            store.execute(_insert_sql(), row)

    def test_a_negative_token_count_is_refused(self, store):
        row = to_row(trace())
        row["input_tokens"] = -1
        with pytest.raises(StoreError), transaction(store):
            store.execute(_insert_sql(), row)


class TestNotPartOfTheGraph:
    def test_a_trace_writes_no_node_row(self, store):
        # The audit trail and the trace store stay two things. A trace that
        # became a graph node would make the log part of what it describes.
        write_trace(store, trace())
        assert store.execute("SELECT count(*) FROM node").fetchone()[0] == 0

    def test_a_trace_writes_no_audit_event(self, store):
        write_trace(store, trace())
        assert store.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 0


class TestSink:
    def test_it_satisfies_the_protocol(self, store):
        assert isinstance(SqliteTraceSink(store), TraceSink)

    def test_recording_through_the_sink_lands_in_the_table(self, store):
        SqliteTraceSink(store).record(trace())
        assert trace_count(store) == 1

    def test_it_leaves_the_connection_open_for_its_owner(self, store):
        sink = SqliteTraceSink(store)
        sink.record(trace())
        sink.record(trace())
        assert trace_count(store) == 2

    def test_a_trace_written_inside_a_transaction_joins_it(self, store):
        # Not because a trace needs to be atomic with anything -- because a
        # provider called from inside a repository write must not commit that
        # write early.
        with transaction(store):
            write_trace(store, trace())
            assert store.in_transaction
        assert trace_count(store) == 1

    def test_a_rolled_back_transaction_takes_its_traces_with_it(self, store):
        with pytest.raises(RuntimeError), transaction(store):
            write_trace(store, trace())
            raise RuntimeError
        assert trace_count(store) == 0


def _insert_sql() -> str:
    """The insert the sink uses, for the tests that write a bad row by hand."""
    from praxis.store.traces import _INSERT  # noqa: PLC0415 -- deliberately private

    return _INSERT


def test_the_row_helpers_are_inverses(store):
    write_trace(store, trace())
    row = store.execute("SELECT * FROM llm_trace").fetchone()
    assert to_row(from_row(row)) == to_row(trace())


class TestPromptProvenance:
    """Migration 004: which prompt, at which version, produced this row."""

    def test_the_prompt_id_and_digest_survive_the_round_trip(self, store):
        original = trace(prompt_id="scan_for_decisions@v1", prompt_sha="b" * 64)
        write_trace(store, original)
        assert traces_in_run(store, RUN) == (original,)

    def test_a_call_with_no_stored_prompt_records_none(self, store):
        # Not an empty string. "No prompt version" and "a prompt version named
        # nothing" are different claims, and every row written before migration
        # 004 is honestly the first one.
        write_trace(store, trace())
        assert traces_in_run(store, RUN)[0].prompt_id is None
        assert traces_in_run(store, RUN)[0].prompt_sha is None

    def test_a_run_reports_the_prompt_versions_it_read(self, store):
        write_trace(store, trace(prompt_id="group_blocks@v1"))
        write_trace(store, trace(prompt_id="scan_for_decisions@v1"))
        write_trace(store, trace(prompt_id="group_blocks@v1"))
        assert prompt_versions_in_run(store, RUN) == (
            "group_blocks@v1",
            "scan_for_decisions@v1",
        )

    def test_a_run_with_no_versioned_prompts_reports_nothing(self, store):
        write_trace(store, trace())
        assert prompt_versions_in_run(store, RUN) == ()

    def test_versions_are_not_reported_across_runs(self, store):
        write_trace(store, trace(prompt_id="group_blocks@v1"))
        write_trace(store, trace(run_id="RUN-000000000002", prompt_id="other@v3"))
        assert prompt_versions_in_run(store, RUN) == ("group_blocks@v1",)
