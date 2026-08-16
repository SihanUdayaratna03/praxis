"""The replay key.

The properties here are the ones a recorded corpus rests on. If the same
request can hash two ways, a fixture recorded on one machine is a cache miss on
another; if two different requests can hash the same way, one question is
answered with another question's recording and nothing says so.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
from praxis.llm.hashing import DIGEST_CHARS, canonical_json, digest_of, prompt_hash
from praxis.llm.types import LLMRequest, Message, MessageRole, ResponseSchema

TEXT = st.text(min_size=1, max_size=40)


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


class TestShape:
    def test_the_digest_is_lower_case_hex_of_a_fixed_width(self):
        value = prompt_hash(request())
        assert len(value) == DIGEST_CHARS
        assert set(value) <= set("0123456789abcdef")

    def test_it_is_wide_enough_that_a_collision_is_not_the_likely_bug(self):
        # 128 bits. Twice a record id's width, because a replay collision is
        # silent where a span id collision is visible.
        assert DIGEST_CHARS == 32


class TestStability:
    def test_the_same_request_hashes_the_same_way_every_time(self):
        assert prompt_hash(request()) == prompt_hash(request())

    def test_two_separately_built_identical_requests_agree(self):
        # The property that makes a recorded corpus shareable: two callers
        # arrive at one key without coordinating.
        assert prompt_hash(request()) == prompt_hash(request())

    def test_key_order_does_not_change_the_rendering(self):
        # json.dumps follows insertion order by default, so this is the
        # difference between a stable key and one that depends on which code
        # path built the dictionary.
        assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})

    def test_non_ascii_is_not_escaped(self):
        # ensure_ascii=True would hash the escape sequence rather than the
        # prompt, which is stable but needlessly surprising -- and it changes
        # the moment anyone reads a fixture by hand.
        assert canonical_json({"k": "සිංහල"}) == '{"k":"සිංහල"}'

    def test_a_non_ascii_prompt_hashes_consistently(self):
        sinhala = request(system="ඔබ තීරණ සොයයි.")
        assert prompt_hash(sinhala) == prompt_hash(request(system="ඔබ තීරණ සොයයි."))


class TestSensitivity:
    def test_a_different_system_prompt_is_a_different_key(self):
        assert prompt_hash(request()) != prompt_hash(request(system="Something else."))

    def test_a_different_agent_is_a_different_key(self):
        # The agent picks the role and therefore the model, so two agents
        # sending identical text are not asking the same question.
        assert prompt_hash(request()) != prompt_hash(request(agent="EstimateExtractor"))

    def test_a_different_task_is_a_different_key(self):
        assert prompt_hash(request()) != prompt_hash(request(task="scan_for_estimates"))

    def test_a_different_output_cap_is_a_different_key(self):
        # The cap changes what comes back -- a truncated answer is a different
        # response to the same words.
        assert prompt_hash(request()) != prompt_hash(request(max_tokens=1_024))

    def test_asking_for_a_schema_is_a_different_key(self):
        schema = ResponseSchema(name="Candidates", json_schema={"type": "object"})
        assert prompt_hash(request()) != prompt_hash(request(schema=schema))

    def test_a_different_schema_of_the_same_name_is_a_different_key(self):
        # A response model that gains a field must not replay a recording made
        # before the field existed.
        first = ResponseSchema(name="Candidates", json_schema={"type": "object"})
        second = ResponseSchema(
            name="Candidates",
            json_schema={"type": "object", "properties": {"found": {"type": "boolean"}}},
        )
        assert prompt_hash(request(schema=first)) != prompt_hash(request(schema=second))

    def test_a_continued_conversation_is_a_different_key(self):
        # This is what makes a repair attempt recordable: the second attempt
        # carries the failure in its messages, so it hashes differently
        # without the attempt number needing to be part of the key.
        original = request()
        repaired = original.with_messages(
            [
                *original.messages,
                Message(role=MessageRole.ASSISTANT, content="{"),
                Message(role=MessageRole.USER, content="That was not valid JSON."),
            ],
            attempt=2,
        )
        assert prompt_hash(original) != prompt_hash(repaired)

    def test_trace_only_fields_are_not_part_of_the_key(self):
        # Metadata and the attempt counter never reach the model. Keying on
        # them would mean a fixture recorded while processing one document
        # could never be replayed against another.
        assert prompt_hash(request()) == prompt_hash(
            request(metadata={"doc": "DOC-0001"}, attempt=4)
        )


class TestProperties:
    @given(system=TEXT, content=TEXT)
    def test_hashing_is_a_function_of_the_request(self, system, content):
        first = request(system=system, messages=(Message(role=MessageRole.USER, content=content),))
        second = request(system=system, messages=(Message(role=MessageRole.USER, content=content),))
        assert prompt_hash(first) == prompt_hash(second)

    @given(first=TEXT, second=TEXT)
    def test_different_prompts_hash_differently(self, first, second):
        # Not a proof of collision resistance -- a check that nothing upstream
        # is discarding the input, which is the way a hash usually breaks.
        if first == second:
            return
        assert prompt_hash(request(system=first)) != prompt_hash(request(system=second))

    @given(payload=st.dictionaries(TEXT, st.integers(), max_size=5))
    def test_any_json_value_hashes_stably(self, payload):
        assert digest_of(payload) == digest_of(dict(reversed(list(payload.items()))))
