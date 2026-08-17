"""What an agent was told, what came back, and what it cost.

The trace store and the audit trail are different things and are deliberately
not one table. The audit trail records what an agent *changed* -- one row per
mutation, written inside the mutation's own transaction, and a record it
describes always exists. A trace records what an agent was *asked* and what the
model said: it exists whether or not anything was written, it exists for the
calls that failed, and the most interesting rows in it are the ones that
produced nothing at all. Merging them would mean either dropping the failures
or inventing an entity for them to point at.

The sink is a protocol rather than a class so that `praxis.llm` never imports
`praxis.store`. A provider takes a sink; what is behind it -- SQLite, a list in
a test -- is the caller's business, and the seam stays a seam.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from praxis.config.models import ModelRole
from praxis.llm.types import CallOutcome, StopReason, TokenUsage


def new_run_id() -> str:
    """Return an id for one run of the pipeline.

    The only value in a trace row that is not a function of the run's inputs.
    Nothing downstream computes a metric from it -- it groups rows so that two
    runs over one corpus can be compared, and a metric that varied with it
    would be a metric that varied between identical runs.
    """
    return f"RUN-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True, slots=True)
class LLMTrace:
    """One attempt at one model call, as it is recorded.

    Attributes:
        run_id: Groups every call made by one run.
        agent: Who asked. Routed to `role` by `praxis.config.models`.
        task: The stable name of this kind of call, for grouping.
        role: The role the agent's calls are routed to.
        provider: Which implementation served it -- `mock`, `replay`,
            `anthropic`. Recorded because a metric computed over a mixture of
            mock and live rows is a metric about nothing.
        model_id: The model that answered, or `MOCK_MODEL_ID` offline.
        prompt_hash: The replay key, so "how often was this exact question
            asked" is a query rather than a diff.
        prompt_id: Which stored prompt produced the system text, as
            `<task>@v<n>`. `None` for a call whose system prompt was not
            rendered from the library, and for every row written before
            migration 004 -- which is an honest answer rather than an invented
            one. ADR 0014.
        prompt_sha: The digest of that prompt's bytes, which is what catches a
            prompt file edited in place rather than versioned.
        attempt: 1 for the first try, 2+ for a repair after malformed output.
        outcome: How the attempt ended.
        stop_reason: Why the model stopped, when it answered at all.
        usage: What it consumed. Counted offline too.
        cost_usd: What it cost. Zero for a mock or a replayed fixture.
        latency_ms: Wall-clock time for the call.
        request_json: The canonical rendering of the request -- every word the
            model read. This is the column that makes a failure reproducible.
        response_text: The raw answer, kept even when it failed to parse.
        error: The translated failure, when there was one.
        occurred_at: When the call started. Timezone-aware, invariant 5.
    """

    run_id: str
    agent: str
    task: str
    role: ModelRole
    provider: str
    model_id: str
    prompt_hash: str
    outcome: CallOutcome
    usage: TokenUsage
    cost_usd: Decimal
    latency_ms: int
    request_json: str
    attempt: int = 1
    prompt_id: str | None = None
    prompt_sha: str | None = None
    stop_reason: StopReason | None = None
    response_text: str = ""
    error: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        """Reject a row that would make the trace store lie."""
        if self.occurred_at.tzinfo is None:
            message = "occurred_at must be timezone-aware"
            raise ValueError(message)
        if self.latency_ms < 0:
            message = f"latency_ms must be non-negative, got {self.latency_ms}"
            raise ValueError(message)
        if self.cost_usd < 0:
            message = f"cost_usd must be non-negative, got {self.cost_usd}"
            raise ValueError(message)
        if self.attempt < 1:
            message = f"attempts are numbered from 1, got {self.attempt}"
            raise ValueError(message)
        if (self.outcome is CallOutcome.ERROR) != (self.error is not None):
            message = (
                f"an error outcome carries a message and nothing else does, "
                f"got {self.outcome.value} with error={self.error!r}"
            )
            raise ValueError(message)

    @property
    def succeeded(self) -> bool:
        """Whether this attempt produced a usable answer."""
        return self.outcome is CallOutcome.OK


@runtime_checkable
class TraceSink(Protocol):
    """Somewhere a trace row goes.

    One method on purpose. A sink that could also be read from would tempt an
    agent into asking the trace store what it said last time, which is a cache
    with no invalidation rule wearing an audit trail's clothes.
    """

    def record(self, trace: LLMTrace) -> None:
        """Persist one attempt at one call."""
        ...


class MemoryTraceSink:
    """A sink that keeps rows in a list.

    What the tests assert against, and what a run with no store falls back to
    -- `praxis doctor` and the CLI's offline paths make calls before anything
    has been initialised, and a provider that could not run without a database
    would make the no-credentials promise depend on one.
    """

    def __init__(self) -> None:
        """Start empty."""
        self.traces: list[LLMTrace] = []

    def record(self, trace: LLMTrace) -> None:
        """Append one attempt."""
        self.traces.append(trace)

    @property
    def total_cost_usd(self) -> Decimal:
        """What every recorded call cost together."""
        return sum((trace.cost_usd for trace in self.traces), Decimal("0"))

    @property
    def total_usage(self) -> TokenUsage:
        """Every recorded call's tokens, summed."""
        total = TokenUsage()
        for trace in self.traces:
            total += trace.usage
        return total
