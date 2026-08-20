"""The trace table: where a model call is written down, and read back.

`praxis.llm` defines what a trace *is* and never learns where it goes; this
module is the other side of that protocol. The direction matters -- the store
imports the LLM layer's record type exactly as it imports the domain's, and the
LLM layer imports no store, so the provider seam stays testable with a list.

A trace commits on its own, unlike an audit row. That is the difference between
the two tables stated in transactions: an audit row must land with the change it
describes or the trail has a hole, while a trace describes something that
happened outside the database entirely and is true whether or not the call it
records led to a write.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from praxis.config.models import ModelRole
from praxis.llm.trace import LLMTrace
from praxis.llm.types import CallOutcome, StopReason, TokenUsage
from praxis.store.connection import transaction
from praxis.store.errors import translating_sqlite_errors

COLUMNS: Final[tuple[str, ...]] = (
    "run_id",
    "occurred_at",
    "agent",
    "task",
    "role",
    "provider",
    "model_id",
    "prompt_hash",
    "prompt_id",
    "prompt_sha",
    "attempt",
    "outcome",
    "stop_reason",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "cost_usd",
    "latency_ms",
    "request_json",
    "response_text",
    "error",
)
"""Every column but `seq`, which SQLite allocates."""

_INSERT: Final = (
    f"INSERT INTO llm_trace ({', '.join(COLUMNS)}) "  # noqa: S608 -- column names are this module's own constant, never a caller's
    f"VALUES ({', '.join(f':{column}' for column in COLUMNS)})"
)


def to_row(trace: LLMTrace) -> dict[str, Any]:
    """Flatten a trace into the columns of `llm_trace`.

    `TokenUsage` is spread across four columns rather than stored as JSON so
    that "what did this run cost in tokens" is a SUM, and `cost_usd` becomes
    the exact decimal string rather than a REAL. Invariant 4.
    """
    return {
        "run_id": trace.run_id,
        "occurred_at": trace.occurred_at.isoformat(),
        "agent": trace.agent,
        "task": trace.task,
        "role": trace.role.value,
        "provider": trace.provider,
        "model_id": trace.model_id,
        "prompt_hash": trace.prompt_hash,
        "prompt_id": trace.prompt_id,
        "prompt_sha": trace.prompt_sha,
        "attempt": trace.attempt,
        "outcome": trace.outcome.value,
        "stop_reason": None if trace.stop_reason is None else trace.stop_reason.value,
        "input_tokens": trace.usage.input_tokens,
        "output_tokens": trace.usage.output_tokens,
        "cache_read_input_tokens": trace.usage.cache_read_input_tokens,
        "cache_creation_input_tokens": trace.usage.cache_creation_input_tokens,
        "cost_usd": str(trace.cost_usd),
        "latency_ms": trace.latency_ms,
        "request_json": trace.request_json,
        "response_text": trace.response_text,
        "error": trace.error,
    }


def from_row(row: sqlite3.Row) -> LLMTrace:
    """Rebuild a trace from its row.

    The inverse of `to_row`, and property-tested as one: a column added to the
    write side and forgotten here would silently return a default, which is the
    failure mode the round trip exists to catch.
    """
    return LLMTrace(
        run_id=row["run_id"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        agent=row["agent"],
        task=row["task"],
        role=ModelRole(row["role"]),
        provider=row["provider"],
        model_id=row["model_id"],
        prompt_hash=row["prompt_hash"],
        prompt_id=row["prompt_id"],
        prompt_sha=row["prompt_sha"],
        attempt=row["attempt"],
        outcome=CallOutcome(row["outcome"]),
        stop_reason=None if row["stop_reason"] is None else StopReason(row["stop_reason"]),
        usage=TokenUsage(
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cache_read_input_tokens=row["cache_read_input_tokens"],
            cache_creation_input_tokens=row["cache_creation_input_tokens"],
        ),
        cost_usd=Decimal(row["cost_usd"]),
        latency_ms=row["latency_ms"],
        request_json=row["request_json"],
        response_text=row["response_text"],
        error=row["error"],
    )


def write_trace(connection: sqlite3.Connection, trace: LLMTrace) -> int:
    """Append one attempt to the trace table.

    Args:
        connection: An open store connection. A transaction is opened here if
            the caller has none, and joined if it has one.
        trace: The attempt to record.

    Returns:
        The sequence number SQLite assigned, which is the table's only total
        order -- two calls in one run can share a millisecond.
    """
    with transaction(connection), translating_sqlite_errors():
        cursor = connection.execute(_INSERT, to_row(trace))
        return int(cursor.lastrowid or 0)


class SqliteTraceSink:
    """A `TraceSink` backed by the store.

    Holds a connection rather than opening one per call: a run makes thousands
    of calls, and a store on a synced filesystem pays for every handle.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Write to an already-open store.

        Args:
            connection: A migrated store connection, owned by the caller. This
                class does not close it -- the process that opened the store
                decides when it is finished with it.
        """
        self._connection = connection

    def record(self, trace: LLMTrace) -> None:
        """Persist one attempt at one call."""
        write_trace(self._connection, trace)


def traces_in_run(connection: sqlite3.Connection, run_id: str) -> tuple[LLMTrace, ...]:
    """Return every call one run made, in the order it made them."""
    with translating_sqlite_errors():
        rows = connection.execute(
            "SELECT * FROM llm_trace WHERE run_id = ? ORDER BY seq", (run_id,)
        ).fetchall()
    return tuple(from_row(row) for row in rows)


def traces_for_prompt(connection: sqlite3.Connection, prompt_hash: str) -> tuple[LLMTrace, ...]:
    """Return every time this exact question was asked, oldest first.

    The query the replay key exists for. Two rows here with different answers
    are the evidence that a model is non-deterministic on a prompt Praxis
    depends on, which is a finding rather than a curiosity.
    """
    with translating_sqlite_errors():
        rows = connection.execute(
            "SELECT * FROM llm_trace WHERE prompt_hash = ? ORDER BY seq", (prompt_hash,)
        ).fetchall()
    return tuple(from_row(row) for row in rows)


def prompt_versions_in_run(connection: sqlite3.Connection, run_id: str) -> tuple[str, ...]:
    """Every stored prompt version one run read, sorted.

    What a metrics table cites beside its numbers. Rows with no prompt id --
    a call whose system text was not rendered from the library, or one written
    before migration 004 -- are left out rather than reported as an empty
    string, because "no prompt version" and "a prompt version named nothing"
    are different claims.
    """
    with translating_sqlite_errors():
        rows = connection.execute(
            "SELECT DISTINCT prompt_id FROM llm_trace "
            "WHERE run_id = ? AND prompt_id IS NOT NULL ORDER BY prompt_id",
            (run_id,),
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def run_cost_usd(connection: sqlite3.Connection, run_id: str) -> Decimal:
    """What one run spent.

    Summed in Python over the exact decimal strings rather than by SQL's SUM,
    which would have to cast a TEXT column to REAL and would make the total
    depend on the order the rows came back in. Invariant 4.
    """
    with translating_sqlite_errors():
        rows = connection.execute(
            "SELECT cost_usd FROM llm_trace WHERE run_id = ?", (run_id,)
        ).fetchall()
    return sum((Decimal(row[0]) for row in rows), Decimal("0"))


def trace_count(connection: sqlite3.Connection) -> int:
    """How many model calls this store has recorded."""
    with translating_sqlite_errors():
        row = connection.execute("SELECT count(*) FROM llm_trace").fetchone()
    return int(row[0])
