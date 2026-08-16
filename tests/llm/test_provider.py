"""The seam's guarantees, tested on a stub rather than on an implementation.

Everything asserted here is something an agent is entitled to assume of *any*
provider: that it never chose a model, that the call was priced before it was
made, and that it was written down afterwards whether it worked or not. Testing
that against a two-line stub is the point -- if these lived in `MockProvider`
they would be true of the offline path and unproven everywhere else.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from praxis.config.models import ModelRole, UnknownAgentError, resolve
from praxis.config.settings import ProviderName, Settings
from praxis.llm.accounting import CostLedger
from praxis.llm.errors import (
    CostCeilingExceededError,
    ProviderRefusalError,
    ProviderUnavailableError,
)
from praxis.llm.hashing import prompt_hash
from praxis.llm.provider import LLMProvider
from praxis.llm.trace import MemoryTraceSink
from praxis.llm.types import (
    CallOutcome,
    LLMRequest,
    LLMResponse,
    Message,
    MessageRole,
    ResponseSchema,
    StopReason,
    TokenUsage,
)

USAGE = TokenUsage(input_tokens=1_000, output_tokens=100)


class StubProvider(LLMProvider):
    """A provider that answers with whatever it was constructed to answer."""

    name = ProviderName.MOCK

    def __init__(self, response=None, raises=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._response = response
        self._raises = raises
        self.invocations: list[tuple[LLMRequest, str]] = []

    def _invoke(self, request, spec):
        self.invocations.append((request, spec.model_id))
        if self._raises is not None:
            raise self._raises
        return self._response or LLMResponse(
            text="an answer",
            model_id=spec.model_id,
            usage=USAGE,
            stop_reason=StopReason.END_TURN,
        )


class BillingStub(StubProvider):
    """The same stub, but one that costs money."""

    name = ProviderName.ANTHROPIC
    bills = True


def request(**overrides) -> LLMRequest:
    """Build a valid request, overriding one field at a time."""
    fields = {
        "agent": "DecisionScout",
        "task": "scan_for_decisions",
        "system": "You find decisions.",
        "messages": (Message(role=MessageRole.USER, content="We chose SQLite."),),
    }
    fields.update(overrides)
    return LLMRequest(**fields)


def provider(cls=StubProvider, **kwargs) -> StubProvider:
    """A stub wired to a fresh sink and a generous ceiling."""
    kwargs.setdefault("sink", MemoryTraceSink())
    kwargs.setdefault("ledger", CostLedger(ceiling_usd=Decimal("5")))
    kwargs.setdefault("settings", Settings())
    return cls(**kwargs)


class TestRouting:
    def test_the_agent_picks_the_model_and_the_caller_never_does(self):
        # Invariant 2 by construction: complete() takes no model argument, and
        # the implementation is handed the spec routing chose.
        stub = provider()
        stub.complete(request(agent="AssumptionExtractor"))
        _, model_id = stub.invocations[0]
        assert model_id == resolve(ModelRole.REASON).model_id

    def test_two_agents_on_different_roles_reach_different_models(self):
        stub = provider()
        stub.complete(request(agent="DecisionScout"))
        stub.complete(request(agent="AssumptionExtractor"))
        assert stub.invocations[0][1] != stub.invocations[1][1]

    def test_a_deterministic_agent_cannot_make_a_call(self):
        # Invariant 3. The seam is where that stops being a convention.
        with pytest.raises(UnknownAgentError, match="deterministic"):
            provider().complete(request(agent="VerifierAgent"))

    def test_an_unrouted_agent_fails_loudly(self):
        with pytest.raises(UnknownAgentError, match="no model route"):
            provider().complete(request(agent="SomeAgentNobodyRouted"))

    def test_an_unrouted_agent_is_refused_before_the_call(self):
        stub = provider()
        with pytest.raises(UnknownAgentError):
            stub.complete(request(agent="VerifierAgent"))
        assert stub.invocations == []


class TestTracing:
    def test_a_successful_call_is_recorded(self):
        stub = provider()
        stub.complete(request())
        (trace,) = stub.sink.traces
        assert trace.outcome is CallOutcome.OK
        assert trace.agent == "DecisionScout"
        assert trace.task == "scan_for_decisions"
        assert trace.role is ModelRole.SCAN

    def test_the_trace_carries_the_replay_key(self):
        # The same key the fixture would be recorded under, so a trace and a
        # fixture of one call can be matched without comparing prompts.
        call = request()
        stub = provider()
        stub.complete(call)
        assert stub.sink.traces[0].prompt_hash == prompt_hash(call)

    def test_the_trace_carries_every_word_the_model_read(self):
        stub = provider()
        stub.complete(request(system="a very specific instruction"))
        assert "a very specific instruction" in stub.sink.traces[0].request_json

    def test_the_trace_says_which_implementation_answered(self):
        # A metric computed over a mixture of mock and live rows is a metric
        # about nothing, so this column is not decoration.
        assert provider().complete(request()) is not None
        stub = provider(BillingStub)
        stub.complete(request())
        assert stub.sink.traces[0].provider == ProviderName.ANTHROPIC.value

    def test_a_repair_attempt_is_its_own_row(self):
        stub = provider()
        stub.complete(request())
        stub.complete(request(attempt=2))
        assert [trace.attempt for trace in stub.sink.traces] == [1, 2]

    def test_every_row_shares_the_run_id(self):
        stub = provider(run_id="RUN-000000000001")
        stub.complete(request())
        stub.complete(request(agent="WorkClassifier"))
        assert {trace.run_id for trace in stub.sink.traces} == {"RUN-000000000001"}

    def test_latency_is_measured_not_invented(self):
        stub = provider()
        stub.complete(request())
        assert stub.sink.traces[0].latency_ms >= 0

    def test_a_provider_with_no_sink_still_works(self):
        # praxis doctor and the offline CLI paths call before any store
        # exists; needing one would make the no-credentials promise depend on
        # a database.
        stub = StubProvider(settings=Settings())
        stub.complete(request())
        assert isinstance(stub.sink, MemoryTraceSink)
        assert len(stub.sink.traces) == 1


class TestFailuresAreRecordedToo:
    def test_a_failed_call_is_traced_before_it_is_raised(self):
        # The promise worth making: a trace store missing exactly the calls
        # that went wrong describes a pipeline that never has any trouble.
        stub = provider(raises=ProviderUnavailableError("the network was down"))
        with pytest.raises(ProviderUnavailableError):
            stub.complete(request())
        (trace,) = stub.sink.traces
        assert trace.outcome is CallOutcome.ERROR
        assert "the network was down" in (trace.error or "")

    def test_a_failure_names_the_error_class(self):
        stub = provider(raises=ProviderUnavailableError("gone"))
        with pytest.raises(ProviderUnavailableError):
            stub.complete(request())
        assert "ProviderUnavailableError" in (stub.sink.traces[0].error or "")

    def test_a_failed_call_records_the_model_it_asked_for(self):
        stub = provider(raises=ProviderUnavailableError("gone"))
        with pytest.raises(ProviderUnavailableError):
            stub.complete(request())
        assert stub.sink.traces[0].model_id == resolve(ModelRole.SCAN).model_id

    def test_a_refusal_is_traced_and_then_raised(self):
        refusal = LLMResponse(
            text="",
            model_id=resolve(ModelRole.SCAN).model_id,
            usage=TokenUsage(input_tokens=10),
            stop_reason=StopReason.REFUSAL,
        )
        stub = provider(response=refusal)
        with pytest.raises(ProviderRefusalError, match="DecisionScout"):
            stub.complete(request())
        assert stub.sink.traces[0].outcome is CallOutcome.REFUSED

    def test_a_refusal_is_not_recorded_as_an_error(self):
        # It arrived as a successful response. Calling it an error would put
        # it in the same bucket as a network failure, which is retried.
        refusal = LLMResponse(
            text="",
            model_id="x",
            usage=TokenUsage(),
            stop_reason=StopReason.REFUSAL,
        )
        stub = provider(response=refusal)
        with pytest.raises(ProviderRefusalError):
            stub.complete(request())
        assert stub.sink.traces[0].error is None


class TestMalformedOutput:
    def schema_request(self) -> LLMRequest:
        return request(schema=ResponseSchema(name="Candidates", json_schema={"type": "object"}))

    def test_a_structured_answer_that_is_not_json_is_malformed(self):
        # Truncation is the usual cause and the provider is the first to see
        # it, so this much is a fact about the response, not about a schema.
        broken = LLMResponse(
            text='{"found": tr',
            model_id="x",
            usage=USAGE,
            stop_reason=StopReason.MAX_TOKENS,
        )
        stub = provider(response=broken)
        stub.complete(self.schema_request())
        assert stub.sink.traces[0].outcome is CallOutcome.MALFORMED

    def test_a_malformed_answer_is_returned_rather_than_raised(self):
        # One malformed answer is an attempt. The repair loop above decides
        # when a run of them is a failure.
        broken = LLMResponse(text="{", model_id="x", usage=USAGE, stop_reason=StopReason.END_TURN)
        stub = provider(response=broken)
        assert stub.complete(self.schema_request()).text == "{"

    def test_valid_json_is_not_malformed(self):
        good = LLMResponse(
            text='{"found": true}',
            model_id="x",
            usage=USAGE,
            stop_reason=StopReason.END_TURN,
        )
        stub = provider(response=good)
        stub.complete(self.schema_request())
        assert stub.sink.traces[0].outcome is CallOutcome.OK

    def test_prose_is_not_malformed_when_no_schema_was_asked_for(self):
        stub = provider()
        stub.complete(request())
        assert stub.sink.traces[0].outcome is CallOutcome.OK


class TestSpending:
    def test_an_offline_provider_costs_nothing(self):
        stub = provider()
        stub.complete(request())
        assert stub.sink.traces[0].cost_usd == Decimal("0")
        assert stub.ledger.spent_usd == Decimal("0")

    def test_an_offline_provider_still_counts_tokens(self):
        # The eval harness reports what an offline run would have cost, and
        # that column has to work where it is cheapest to produce.
        stub = provider()
        stub.complete(request())
        assert stub.ledger.usage.total == USAGE.total
        assert stub.ledger.calls == 1

    def test_a_billing_provider_charges_the_ledger(self):
        stub = provider(BillingStub)
        stub.complete(request())
        assert stub.ledger.spent_usd > Decimal("0")
        assert stub.sink.traces[0].cost_usd == stub.ledger.spent_usd

    def test_the_ceiling_is_checked_before_the_call_not_after(self):
        # The whole point of the setting: a runaway loop costs an error
        # message rather than a bill.
        stub = provider(BillingStub, ledger=CostLedger(ceiling_usd=Decimal("0.0000001")))
        with pytest.raises(CostCeilingExceededError):
            stub.complete(request())
        assert stub.invocations == []

    def test_a_refused_call_is_not_traced_because_it_never_happened(self):
        stub = provider(BillingStub, ledger=CostLedger(ceiling_usd=Decimal("0.0000001")))
        with pytest.raises(CostCeilingExceededError):
            stub.complete(request())
        assert stub.sink.traces == []

    def test_an_offline_provider_ignores_the_ceiling(self):
        # It is exempt because it does not bill, not because of a special
        # case inside the check.
        stub = provider(ledger=CostLedger(ceiling_usd=Decimal("0.0000001")))
        stub.complete(request())
        assert len(stub.sink.traces) == 1

    def test_a_failed_call_costs_nothing(self):
        stub = provider(BillingStub, raises=ProviderUnavailableError("gone"))
        with pytest.raises(ProviderUnavailableError):
            stub.complete(request())
        assert stub.sink.traces[0].cost_usd == Decimal("0")
