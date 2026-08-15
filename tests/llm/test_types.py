"""The request and response vocabulary.

Most of these pin a rule that exists because the API has it: a first message
that must come from the user, an output cap that needs streaming above a
threshold, a stop reason this build has never seen. Each one is cheaper to
catch here than in the first live call, which is the only kind of call this
phase cannot make.
"""

from __future__ import annotations

import json

import pytest
from praxis.llm.types import (
    MAX_TOKENS_WITHOUT_STREAMING,
    CallOutcome,
    LLMRequest,
    LLMResponse,
    Message,
    MessageRole,
    ResponseSchema,
    StopReason,
    TokenUsage,
)

SCHEMA = ResponseSchema(
    name="Candidates",
    json_schema={"type": "object", "properties": {"found": {"type": "boolean"}}},
)


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


class TestRequestValidation:
    def test_a_request_with_no_messages_is_rejected(self):
        with pytest.raises(ValueError, match="no messages"):
            request(messages=())

    def test_the_conversation_must_open_with_a_user_turn(self):
        # The API requires it. Building the request is where a caller finds
        # out, rather than the response body of a call that was billed for.
        with pytest.raises(ValueError, match="first message must be from the user"):
            request(messages=(Message(role=MessageRole.ASSISTANT, content="Sure."),))

    @pytest.mark.parametrize("max_tokens", [0, -1])
    def test_a_non_positive_output_cap_is_rejected(self, max_tokens):
        with pytest.raises(ValueError, match="max_tokens must be positive"):
            request(max_tokens=max_tokens)

    def test_an_output_cap_that_would_need_streaming_is_rejected(self):
        # Not an arbitrary limit: above this a non-streaming request risks an
        # HTTP timeout, and this phase never streams.
        with pytest.raises(ValueError, match="needs streaming"):
            request(max_tokens=MAX_TOKENS_WITHOUT_STREAMING + 1)

    def test_the_cap_itself_is_allowed(self):
        assert request(max_tokens=MAX_TOKENS_WITHOUT_STREAMING).max_tokens == 16_000

    def test_attempts_are_numbered_from_one(self):
        with pytest.raises(ValueError, match="numbered from 1"):
            request(attempt=0)

    def test_a_request_is_frozen(self):
        with pytest.raises(AttributeError):
            request().agent = "SomebodyElse"  # type: ignore[misc]


class TestCanonicalForm:
    def test_it_carries_everything_the_model_will_see(self):
        canonical = request(schema=SCHEMA).canonical()
        assert canonical["agent"] == "DecisionScout"
        assert canonical["system"] == "You find decisions."
        assert canonical["messages"] == [{"role": "user", "content": "We chose SQLite."}]
        assert canonical["schema"]["name"] == "Candidates"

    def test_a_free_text_call_has_no_schema(self):
        assert request().canonical()["schema"] is None

    def test_metadata_is_excluded(self):
        # Metadata is trace context, not prompt. Including it would key two
        # identical prompts to two different fixtures.
        with_metadata = request(metadata={"span_id": "SPAN-0000000000000001"})
        assert with_metadata.canonical() == request().canonical()

    def test_the_attempt_number_is_excluded(self):
        # A repair changes the conversation, and the conversation is already
        # in the canonical form. Counting the attempt twice would mean a
        # recorded repair could never be replayed.
        assert request(attempt=3).canonical() == request().canonical()

    def test_it_is_plain_data(self):
        # The hash and the trace both serialise this, so nothing in it may be
        # an object whose repr is an address.
        json.dumps(request(schema=SCHEMA).canonical())


class TestPromptText:
    def test_it_is_everything_the_model_reads(self):
        # MockProvider quotes from this to build citations that really appear
        # in the input, so it has to be the whole prompt and not just the
        # last turn.
        text = request(
            messages=(
                Message(role=MessageRole.USER, content="first"),
                Message(role=MessageRole.ASSISTANT, content="second"),
                Message(role=MessageRole.USER, content="third"),
            )
        ).prompt_text
        assert "You find decisions." in text
        assert "first" in text
        assert "second" in text
        assert "third" in text


class TestContinuation:
    def test_it_keeps_the_routing_and_replaces_the_conversation(self):
        original = request(schema=SCHEMA, metadata={"doc": "DOC-0001"})
        repaired = original.with_messages(
            [
                *original.messages,
                Message(role=MessageRole.ASSISTANT, content="{"),
                Message(role=MessageRole.USER, content="That was not valid JSON."),
            ],
            attempt=2,
        )
        assert repaired.agent == original.agent
        assert repaired.schema == original.schema
        assert repaired.metadata == original.metadata
        assert repaired.attempt == 2
        assert len(repaired.messages) == 3

    def test_the_original_is_untouched(self):
        original = request()
        original.with_messages([*original.messages], attempt=2)
        assert len(original.messages) == 1
        assert original.attempt == 1


class TestStopReason:
    @pytest.mark.parametrize(
        "value",
        ["end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal"],
    )
    def test_the_documented_reasons_map_to_themselves(self, value):
        assert StopReason.from_api(value).value == value

    def test_an_unknown_reason_becomes_other_rather_than_raising(self):
        # A stop reason added to the API after this build shipped is not a
        # reason for a run to die. It is a reason for the trace to say so.
        assert StopReason.from_api("teleported") is StopReason.OTHER

    def test_a_missing_reason_becomes_other(self):
        assert StopReason.from_api(None) is StopReason.OTHER


class TestTokenUsage:
    def test_total_counts_cached_tokens_too(self):
        usage = TokenUsage(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=100,
            cache_creation_input_tokens=2,
        )
        assert usage.total == 117

    def test_usage_sums_field_by_field(self):
        summed = TokenUsage(input_tokens=10, output_tokens=5) + TokenUsage(
            input_tokens=1, output_tokens=2, cache_read_input_tokens=3
        )
        assert summed.input_tokens == 11
        assert summed.output_tokens == 7
        assert summed.cache_read_input_tokens == 3

    def test_an_empty_usage_is_the_identity(self):
        usage = TokenUsage(input_tokens=7, output_tokens=9)
        assert usage + TokenUsage() == usage

    @pytest.mark.parametrize(
        "field",
        [
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ],
    )
    def test_a_negative_count_is_rejected(self, field):
        with pytest.raises(ValueError, match="non-negative"):
            TokenUsage(**{field: -1})


class TestResponse:
    def response(self, stop_reason: StopReason) -> LLMResponse:
        return LLMResponse(
            text="{}",
            model_id="mock-deterministic-v1",
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            stop_reason=stop_reason,
        )

    def test_a_refusal_is_recognisable_without_reading_the_text(self):
        assert self.response(StopReason.REFUSAL).is_refusal
        assert not self.response(StopReason.END_TURN).is_refusal

    def test_a_truncated_answer_is_recognisable(self):
        # This is the common cause of malformed JSON, and knowing it lets the
        # repair loop say so instead of guessing.
        assert self.response(StopReason.MAX_TOKENS).is_truncated
        assert not self.response(StopReason.END_TURN).is_truncated


def test_outcomes_cover_the_ways_a_call_can_end():
    assert {outcome.value for outcome in CallOutcome} == {"ok", "malformed", "refused", "error"}
