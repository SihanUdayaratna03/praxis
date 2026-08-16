"""The live provider, tested without a network and without a credential.

Two things make this worth testing at all when nothing here can call the API.
The translation in both directions is where a live run breaks first, and it is
pure: a request in, an SDK payload out; an SDK message in, an `LLMResponse`
out. And the vendor-exception boundary is a promise the rest of the codebase
relies on -- every failure raised here must be a `praxis.llm` class, or the
seam is decorative.

Responses are built as real `anthropic.types.Message` objects rather than as
stubs. A stub would agree with whatever this module assumed; the SDK's own
model does not, which is the point -- it is how the cache fields being `None`
rather than zero was found.
"""

from __future__ import annotations

import json
from decimal import Decimal

import anthropic
import httpx
import pytest
from anthropic.types import Message, TextBlock, Usage
from anthropic.types.refusal_stop_details import RefusalStopDetails
from praxis.config.models import resolve, role_for_agent
from praxis.config.settings import ProviderName, Settings
from praxis.llm.accounting import CostLedger
from praxis.llm.anthropic import (
    REQUEST_TIMEOUT_SECONDS,
    AnthropicProvider,
    RecordingAnthropicProvider,
)
from praxis.llm.errors import (
    CostCeilingExceededError,
    ProviderError,
    ProviderRefusalError,
    ProviderUnavailableError,
)
from praxis.llm.replay import Recording, ReplayProvider, fixture_path
from praxis.llm.trace import MemoryTraceSink
from praxis.llm.types import (
    CallOutcome,
    LLMRequest,
    MessageRole,
    ResponseSchema,
    StopReason,
)
from praxis.llm.types import (
    Message as PraxisMessage,
)

LIVE_MODEL = resolve(role_for_agent("DecisionScout")).model_id

SCHEMA = ResponseSchema(
    name="Candidates",
    json_schema={
        "type": "object",
        "properties": {"found": {"type": "boolean"}},
        "required": ["found"],
        "additionalProperties": False,
    },
)


def request(**overrides) -> LLMRequest:
    """Build a valid request, overriding one field at a time."""
    fields = {
        "agent": "DecisionScout",
        "task": "scan_for_decisions",
        "system": "You find decisions.",
        "messages": (PraxisMessage(role=MessageRole.USER, content="We chose SQLite."),),
    }
    fields.update(overrides)
    return LLMRequest(**fields)


def api_message(**overrides) -> Message:
    """Build a real SDK message, so the field names are the SDK's own."""
    fields = {
        "id": "msg_01",
        "content": [TextBlock(type="text", text='{"found": true}')],
        "model": LIVE_MODEL,
        "role": "assistant",
        "stop_reason": "end_turn",
        "type": "message",
        "usage": Usage(input_tokens=820, output_tokens=17),
    }
    fields.update(overrides)
    return Message(**fields)


class FakeMessages:
    """Stands in for `client.messages`, recording what it was asked."""

    def __init__(self, answer=None, raises=None) -> None:
        self.answer = answer
        self.raises = raises
        self.calls: list[dict] = []

    def create(self, **payload):
        self.calls.append(payload)
        if self.raises is not None:
            raise self.raises
        return self.answer if self.answer is not None else api_message()


class FakeClient:
    """A client shaped like the SDK's, wired to a fake `messages`."""

    def __init__(self, answer=None, raises=None) -> None:
        self.messages = FakeMessages(answer, raises)


def api_error(cls, status: int):
    """Build a real SDK exception of a given status, as the SDK builds them."""
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.invalid"))
    return cls("boom", response=response, body=None)


def provider(**kwargs) -> AnthropicProvider:
    """A live provider wired to a fake client and a throwaway sink."""
    kwargs.setdefault("client", FakeClient())
    kwargs.setdefault("sink", MemoryTraceSink())
    return AnthropicProvider(**kwargs)


class TestCredentials:
    def test_it_refuses_to_build_a_client_without_a_key(self, tmp_path):
        # Settings already rejects this pair; failing here too means a caller
        # who built the provider by hand still cannot get a mid-run surprise.
        with pytest.raises(ProviderUnavailableError, match="PRAXIS_ANTHROPIC_API_KEY"):
            AnthropicProvider(settings=Settings(data_dir=tmp_path))

    def test_the_message_points_at_the_offline_default(self):
        # Invariant 1: the answer to a missing credential is never "get one".
        with pytest.raises(ProviderUnavailableError, match="mock provider"):
            AnthropicProvider()

    def test_a_supplied_client_is_used_as_given(self):
        # How every test below avoids both a credential and a network.
        client = FakeClient()
        assert provider(client=client)._client is client

    def test_it_bills_and_says_so(self):
        # The class-level flag is what subjects it to the ceiling.
        assert AnthropicProvider.bills
        assert AnthropicProvider.name is ProviderName.ANTHROPIC


class TestWhatIsSent:
    def test_the_model_comes_from_routing_not_from_the_caller(self):
        # Invariant 2: this module holds no model id, and no caller can pass
        # one -- the agent's role decides it.
        live = provider()
        live.complete(request())
        assert live._client.messages.calls[0]["model"] == LIVE_MODEL

    def test_the_system_prompt_is_a_field_not_a_message(self):
        # The Messages API models it that way, and MessageRole has no system
        # member precisely so a caller cannot build the other shape.
        payload = _sent(request(system="You find decisions."))
        assert payload["system"] == "You find decisions."
        assert all(message["role"] != "system" for message in payload["messages"])

    def test_the_conversation_is_sent_in_order(self):
        payload = _sent(
            request(
                messages=(
                    PraxisMessage(role=MessageRole.USER, content="first"),
                    PraxisMessage(role=MessageRole.ASSISTANT, content="second"),
                    PraxisMessage(role=MessageRole.USER, content="third"),
                )
            )
        )
        assert [message["content"] for message in payload["messages"]] == [
            "first",
            "second",
            "third",
        ]

    def test_no_sampling_parameters_are_sent(self):
        # They are a 400 on the routed models, which is why the seam has no
        # field to forward. Asserted rather than assumed: a helpful future
        # edit adding one would fail every live call.
        payload = _sent(request())
        assert not {"temperature", "top_p", "top_k"} & set(payload)

    def test_no_thinking_configuration_is_sent(self):
        # The default is what these models are tuned for. Pinning it here
        # would put a model-specific choice in the model-agnostic file.
        assert "thinking" not in _sent(request())

    def test_a_free_text_call_asks_for_no_format(self):
        assert "output_config" not in _sent(request())

    def test_a_structured_call_goes_through_output_config_format(self):
        # Not forced tool use, and not the deprecated top-level output_format.
        payload = _sent(request(schema=SCHEMA))
        assert payload["output_config"]["format"]["type"] == "json_schema"
        assert payload["output_config"]["format"]["schema"] == dict(SCHEMA.json_schema)

    def test_the_output_cap_is_forwarded(self):
        assert _sent(request(max_tokens=2_048))["max_tokens"] == 2_048

    def test_the_client_is_built_with_a_bounded_timeout(self):
        # The SDK's ten-minute default is sized for a 128k streaming answer;
        # this seam makes one capped non-streaming call.
        assert REQUEST_TIMEOUT_SECONDS < 600


class TestWhatComesBack:
    def test_the_text_blocks_are_joined(self):
        answer = api_message(
            content=[TextBlock(type="text", text="one "), TextBlock(type="text", text="two")]
        )
        assert _answered(answer).text == "one two"

    def test_a_structured_answer_arrives_parseable(self):
        assert json.loads(_answered(api_message()).text) == {"found": True}

    def test_the_model_that_answered_is_recorded_not_the_one_asked_for(self):
        # A response naming a dated snapshot is what a trace should show, so
        # that "which model produced this number" survives an alias moving.
        answer = api_message(model="claude-haiku-4-5-20251001")
        assert _answered(answer).model_id == "claude-haiku-4-5-20251001"

    def test_token_counts_come_through(self):
        usage = _answered(api_message()).usage
        assert usage.input_tokens == 820
        assert usage.output_tokens == 17

    def test_absent_cache_counts_become_zero_not_none(self):
        # The SDK reports None when no cache was touched, and TokenUsage
        # refuses None. This is the vendor detail that must not reach a
        # column the eval harness sums.
        assert _answered(api_message()).usage.cache_read_input_tokens == 0

    def test_reported_cache_counts_are_kept(self):
        answer = api_message(
            usage=Usage(
                input_tokens=10,
                output_tokens=2,
                cache_read_input_tokens=4_000,
                cache_creation_input_tokens=100,
            )
        )
        usage = _answered(answer).usage
        assert usage.cache_read_input_tokens == 4_000
        assert usage.cache_creation_input_tokens == 100

    def test_a_priced_call_reaches_the_ledger(self):
        live = provider()
        live.complete(request())
        assert live.ledger.spent_usd > Decimal("0")

    def test_a_truncated_answer_is_seen_as_truncated(self):
        answer = api_message(stop_reason="max_tokens", content=[TextBlock(type="text", text="{")])
        assert _answered(answer).is_truncated

    def test_a_stop_reason_this_build_does_not_know_is_tolerated(self):
        # A new member appearing in the API is not a reason for a run to die.
        answer = api_message(stop_reason="pause_turn")
        assert _answered(answer).stop_reason is StopReason.PAUSE_TURN


class TestRefusals:
    def test_a_refusal_raises_rather_than_returning_empty_text(self):
        # It arrives as a successful 200, so a caller that only checked the
        # status code would read an empty answer as a real one.
        refusal = api_message(
            stop_reason="refusal",
            content=[],
            stop_details=RefusalStopDetails(
                type="refusal", category="cyber", explanation="declined on policy grounds"
            ),
        )
        with pytest.raises(ProviderRefusalError):
            provider(client=FakeClient(refusal)).complete(request())

    def test_the_reason_is_kept_as_the_response_text(self):
        # A row reading outcome=refused with nothing beside it answers no
        # question anyone asks of it later.
        sink = MemoryTraceSink()
        refusal = api_message(
            stop_reason="refusal",
            content=[],
            stop_details=RefusalStopDetails(
                type="refusal", category="cyber", explanation="declined on policy grounds"
            ),
        )
        with pytest.raises(ProviderRefusalError):
            provider(client=FakeClient(refusal), sink=sink).complete(request())
        assert sink.traces[0].outcome is CallOutcome.REFUSED
        assert sink.traces[0].response_text == "declined on policy grounds"

    def test_a_refusal_without_details_still_works(self):
        # stop_details is populated only on a refusal -- and not always even
        # then, so it is guarded rather than assumed.
        refusal = api_message(stop_reason="refusal", content=[], stop_details=None)
        with pytest.raises(ProviderRefusalError):
            provider(client=FakeClient(refusal)).complete(request())


class TestNoVendorExceptionEscapes:
    @pytest.mark.parametrize(
        ("cls", "status"),
        [
            (anthropic.AuthenticationError, 401),
            (anthropic.PermissionDeniedError, 403),
            (anthropic.NotFoundError, 404),
            (anthropic.RateLimitError, 429),
            (anthropic.InternalServerError, 500),
        ],
    )
    def test_every_status_failure_becomes_the_unavailable_error(self, cls, status):
        # A caller forced to write `except anthropic.RateLimitError` has
        # imported the SDK as surely as one that builds a request.
        live = provider(client=FakeClient(raises=api_error(cls, status)))
        with pytest.raises(ProviderUnavailableError):
            live.complete(request())

    def test_a_transport_failure_becomes_the_unavailable_error(self):
        failure = anthropic.APIConnectionError(request=httpx.Request("POST", "https://x.invalid"))
        with pytest.raises(ProviderUnavailableError):
            provider(client=FakeClient(raises=failure)).complete(request())

    def test_a_rejected_request_is_not_reported_as_a_transport_problem(self):
        # A 400 is Praxis's bug. Telling the caller to wait and retry would be
        # advice that never comes good.
        live = provider(client=FakeClient(raises=api_error(anthropic.BadRequestError, 400)))
        with pytest.raises(ProviderError) as caught:
            live.complete(request())
        assert not isinstance(caught.value, ProviderUnavailableError)
        assert "DecisionScout" in str(caught.value)

    def test_a_failed_call_is_still_written_down(self):
        # The trace store missing exactly the calls that went wrong would
        # describe a pipeline that never has any trouble.
        sink = MemoryTraceSink()
        live = provider(
            client=FakeClient(raises=api_error(anthropic.RateLimitError, 429)), sink=sink
        )
        with pytest.raises(ProviderUnavailableError):
            live.complete(request())
        assert sink.traces[0].outcome is CallOutcome.ERROR
        assert "RateLimitError" in (sink.traces[0].error or "")


class TestTheCeilingIsRealHere:
    def test_a_call_past_the_ceiling_is_never_made(self):
        # Checked before the call, not after: a limit enforced afterwards is
        # a report. The owner is paying for this personally.
        client = FakeClient()
        live = provider(client=client, ledger=CostLedger(ceiling_usd=Decimal("0.0000001")))
        with pytest.raises(CostCeilingExceededError):
            live.complete(request())
        assert client.messages.calls == []


class TestRecording:
    def test_a_live_answer_is_saved_where_replay_will_find_it(self, tmp_path):
        settings = Settings(data_dir=tmp_path, replay_dir=tmp_path / "fixtures")
        live = RecordingAnthropicProvider(
            client=FakeClient(), sink=MemoryTraceSink(), settings=settings
        )
        live.complete(request())
        assert fixture_path(settings.replay_dir, request()).is_file()

    def test_the_recording_replays_identically(self, tmp_path):
        # The round trip is the claim worth testing: record once live, and an
        # offline run answers the same question with the same bytes.
        settings = Settings(data_dir=tmp_path, replay_dir=tmp_path / "fixtures")
        live = RecordingAnthropicProvider(
            client=FakeClient(), sink=MemoryTraceSink(), settings=settings
        )
        expected = live.complete(request())
        replayed = ReplayProvider(root=settings.replay_dir).complete(request())
        assert replayed.text == expected.text
        assert replayed.usage == expected.usage
        assert replayed.model_id == expected.model_id

    def test_a_failed_call_records_nothing(self, tmp_path):
        # There is no answer to record, and a fixture of a failure would
        # replay as a success.
        settings = Settings(data_dir=tmp_path, replay_dir=tmp_path / "fixtures")
        live = RecordingAnthropicProvider(
            client=FakeClient(raises=api_error(anthropic.InternalServerError, 500)),
            sink=MemoryTraceSink(),
            settings=settings,
        )
        with pytest.raises(ProviderUnavailableError):
            live.complete(request())
        assert not fixture_path(settings.replay_dir, request()).exists()

    def test_the_saved_recording_names_the_model_that_answered(self, tmp_path):
        settings = Settings(data_dir=tmp_path, replay_dir=tmp_path / "fixtures")
        live = RecordingAnthropicProvider(
            client=FakeClient(), sink=MemoryTraceSink(), settings=settings
        )
        live.complete(request())
        saved = json.loads(fixture_path(settings.replay_dir, request()).read_text(encoding="utf-8"))
        assert Recording.from_json(saved).model_id == LIVE_MODEL


def _sent(call: LLMRequest) -> dict:
    """Return the payload the SDK would have received for one request."""
    live = provider()
    live.complete(call)
    return live._client.messages.calls[0]


def _answered(answer: Message):
    """Return the response a given SDK message translates into."""
    return provider(client=FakeClient(answer)).complete(request())
