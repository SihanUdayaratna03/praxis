"""The seam itself: what every provider must do, and what none of them may skip.

An implementation supplies one method -- `_invoke`, which turns a request into a
response by whatever means it has. Everything an agent is entitled to assume
happens here instead, once:

- **The agent names itself, and routing decides the model.** Invariant 2 holds
  because no implementation is ever handed a model id to choose from.
- **The ceiling is checked before the call.** A provider that bills is asked
  whether the run can afford the worst case; one that does not bill is not.
- **Every attempt is traced, including the ones that failed.** The trace is
  written in the failure path as well as the success path, which is the only
  version of that promise worth making -- a trace store missing exactly the
  calls that went wrong describes a pipeline that never has any trouble.

`_invoke` may raise anything in `praxis.llm.errors`; it may not raise a vendor's
exception, and `praxis.llm.anthropic` is the only module that could.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import ClassVar

from praxis.config.models import ModelRole, ModelSpec, resolve, role_for_agent
from praxis.config.settings import ProviderName, Settings, get_settings
from praxis.llm.accounting import CostLedger
from praxis.llm.errors import ProviderError, ProviderRefusalError
from praxis.llm.hashing import canonical_json, prompt_hash
from praxis.llm.trace import LLMTrace, MemoryTraceSink, TraceSink, new_run_id
from praxis.llm.types import CallOutcome, LLMRequest, LLMResponse, StopReason, TokenUsage
from praxis.obs.logging import get_logger

_log = get_logger(__name__)


class LLMProvider(ABC):
    """One way of answering a request, wrapped in the guarantees all of them owe.

    Attributes:
        name: Which implementation this is. Recorded on every trace, because a
            metric computed over a mixture of mock and live rows is a metric
            about nothing.
        bills: Whether calls cost money. False for the offline implementations,
            which is what exempts them from the ceiling rather than a special
            case inside the check.
    """

    name: ClassVar[ProviderName]
    bills: ClassVar[bool] = False

    def __init__(
        self,
        *,
        sink: TraceSink | None = None,
        ledger: CostLedger | None = None,
        run_id: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Wire a provider to somewhere to record and something to spend.

        Args:
            sink: Where traces go. Defaults to an in-memory sink so that a
                provider works before any store exists -- `praxis doctor` and
                the offline CLI paths make calls against an uninitialised
                store, and a provider that needed a database would make the
                no-credentials promise depend on one.
            ledger: The run's budget. Defaults to the configured ceiling.
            run_id: Groups this run's traces. Defaults to a fresh one.
            settings: Configuration, read once if not supplied.
        """
        self._settings = settings or get_settings()
        self.sink = sink if sink is not None else MemoryTraceSink()
        self.ledger = ledger if ledger is not None else CostLedger.from_settings(self._settings)
        self.run_id = run_id or new_run_id()

    @abstractmethod
    def _invoke(self, request: LLMRequest, spec: ModelSpec) -> LLMResponse:
        """Answer the request, by network, by fixture or by synthesis.

        Args:
            request: What to ask.
            spec: The model routing chose for this agent. An offline
                implementation still receives it, because a mock run reports
                what the live run it stands in for would have cost.

        Returns:
            The raw response, unparsed.

        Raises:
            ProviderError: and nothing else. A vendor exception reaching a
                caller would make the seam decorative.
        """

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Make one attempt at one call, and record it.

        Args:
            request: What to ask. Its `agent` decides the model.

        Returns:
            The response, including one that failed to parse -- a malformed
            answer is an attempt, and the repair loop above this is what turns
            a run of them into a failure.

        Raises:
            UnknownAgentError: if the agent is deterministic or unrouted.
            CostCeilingExceededError: if the run cannot afford the worst case.
                Raised before the call, and deliberately not traced: nothing
                was asked, so there is nothing to record.
            ProviderRefusalError: if the model declined. Traced first.
            ProviderError: whatever `_invoke` raised, traced first.
        """
        role = role_for_agent(request.agent)
        spec = resolve(role)
        if self.bills:
            self.ledger.check_affordable(spec, request)

        started = time.perf_counter()
        try:
            response = self._invoke(request, spec)
        except ProviderError as exc:
            self._record_failure(request, role, spec, exc, self._elapsed_ms(started))
            raise

        latency_ms = self._elapsed_ms(started)
        self._record(
            request,
            role=role,
            model_id=response.model_id,
            outcome=self._outcome_of(request, response),
            usage=response.usage,
            cost_usd=self._charge(spec, response.usage),
            latency_ms=latency_ms,
            response_text=response.text,
            stop_reason=response.stop_reason,
        )
        if response.is_refusal:
            raise ProviderRefusalError(request.agent)
        return response

    def _charge(self, spec: ModelSpec, usage: TokenUsage) -> Decimal:
        """Add the call to the ledger, priced or free.

        A free provider still counts tokens: the eval harness reports what an
        offline run *would* have cost, and a mock run with no counts would make
        that column unavailable exactly where it is cheapest to produce.
        """
        if self.bills:
            return self.ledger.record(spec, usage)
        return self.ledger.record_free(usage)

    @staticmethod
    def _outcome_of(request: LLMRequest, response: LLMResponse) -> CallOutcome:
        """Classify an answer as far as a provider honestly can.

        A structured call whose answer is not even valid JSON is malformed
        here, because that is a fact about the response rather than about any
        schema -- and truncation is the usual cause, which the provider is the
        first to see. Whether valid JSON *satisfies* the schema is the
        structured layer's question, and its repair is a new request with a new
        attempt number and a row of its own.
        """
        if response.is_refusal:
            return CallOutcome.REFUSED
        if request.schema is not None and not _is_json(response.text):
            return CallOutcome.MALFORMED
        return CallOutcome.OK

    def _record_failure(
        self,
        request: LLMRequest,
        role: ModelRole,
        spec: ModelSpec,
        exc: ProviderError,
        latency_ms: int,
    ) -> None:
        """Trace a call that never produced a response.

        The model id recorded is the one routing chose, not one the response
        reported, because there was no response. It is what was asked of, which
        is the question this row answers.
        """
        self._record(
            request,
            role=role,
            model_id=spec.model_id,
            outcome=CallOutcome.ERROR,
            usage=TokenUsage(),
            cost_usd=Decimal("0"),
            latency_ms=latency_ms,
            response_text="",
            stop_reason=None,
            error=f"{type(exc).__name__}: {exc}",
        )

    def _record(  # noqa: PLR0913 -- a trace row has this many irreducible parts
        self,
        request: LLMRequest,
        *,
        role: ModelRole,
        model_id: str,
        outcome: CallOutcome,
        usage: TokenUsage,
        cost_usd: Decimal,
        latency_ms: int,
        response_text: str,
        stop_reason: StopReason | None,
        error: str | None = None,
    ) -> None:
        """Assemble and write one trace row.

        The request is rendered canonically rather than repr'd: the trace has
        to hold every word the model read, in the same rendering the replay key
        hashed, or a failure recorded here cannot be reproduced from it.
        """
        trace = LLMTrace(
            run_id=self.run_id,
            agent=request.agent,
            task=request.task,
            role=role,
            provider=self.name.value,
            model_id=model_id,
            prompt_hash=prompt_hash(request),
            outcome=outcome,
            usage=usage,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            request_json=canonical_json(request.canonical()),
            attempt=request.attempt,
            stop_reason=stop_reason,
            response_text=response_text,
            error=error,
        )
        self.sink.record(trace)
        _log.info(
            "llm_call",
            agent=trace.agent,
            task=trace.task,
            provider=trace.provider,
            outcome=trace.outcome.value,
            tokens=trace.usage.total,
            latency_ms=trace.latency_ms,
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        """Milliseconds since a `perf_counter` reading, never negative.

        `perf_counter` rather than wall clock: latency is a duration, and a
        clock that steps backwards mid-call would record one that is negative.
        """
        return max(0, round((time.perf_counter() - started) * 1000))


def _is_json(text: str) -> bool:
    """Whether the answer parses at all.

    Not a schema check. The most common malformed structured response is a
    truncated one, and a truncated JSON object fails here.
    """
    try:
        json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return False
    return True
