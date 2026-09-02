"""Schema reduction, and the repair loop against a provider told to misbehave.

The repair tests deliberately do not use a stub. `MockProvider` takes a
`malformed_share`, which Phase 2 built for exactly this and which truncates a
real synthesised answer the way a real `max_tokens` cut does. A hand-written
stub returning `"{"` would agree with whatever this module assumed, and the
Phase 2 report is explicit that the tests which found defects were the ones
built on real objects.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum

import pytest
from praxis.config.settings import Settings
from praxis.llm.errors import MalformedOutputError, SchemaNotSupportedError
from praxis.llm.mock import MockProvider
from praxis.llm.structured import REPAIR_ATTEMPTS, ask_for, schema_for
from praxis.llm.trace import MemoryTraceSink
from praxis.llm.types import CallOutcome, LLMRequest, Message, MessageRole
from pydantic import BaseModel, Field

AGENT = "SegmenterAgent"
SOURCE = (
    "We chose SQLite over Postgres for the graph store.\n"
    "The migration is assumed to take at most six weeks.\n"
    "Nobody will run a server for a single-writer tool.\n"
)


class Verdict(StrEnum):
    KEEP = "keep"
    DROP = "drop"


class Evidence(BaseModel):
    quote: str
    span_id: str


class Answer(BaseModel):
    """A response model carrying most of what the dialect has to strip."""

    title: str = Field(min_length=3, max_length=80)
    weeks: int = Field(ge=1, le=12)
    confidence: float = Field(ge=0.0, le=1.0)
    verdict: Verdict
    evidence: tuple[Evidence, ...] = Field(min_length=1, max_length=4)
    note: str | None = None


def request(source: str = SOURCE, *, task: str = "segment") -> LLMRequest:
    return LLMRequest(
        agent=AGENT,
        task=task,
        system="You group blocks of a document.",
        messages=(Message(role=MessageRole.USER, content=source),),
    )


def provider(settings: Settings, **kwargs) -> MockProvider:
    return MockProvider(sink=MemoryTraceSink(), settings=settings, **kwargs)


# -- reduction ---------------------------------------------------------------


def test_the_root_is_closed_and_every_property_is_required():
    """What the API does with a class-derived schema, matched so that a live
    answer and a replayed one have the same set of keys."""
    schema = schema_for(Answer).json_schema

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "note" in schema["required"]


def test_the_schema_is_named_after_the_model():
    assert schema_for(Answer).name == "Answer"


@pytest.mark.parametrize(
    "keyword", ["minimum", "maximum", "exclusiveMinimum", "minLength", "maxLength", "pattern"]
)
def test_unsupported_keywords_are_gone_from_every_node(keyword):
    """An unsupported keyword is a 400, not an ignored field."""
    assert keyword not in keywords_in(schema_for(Answer).json_schema)


def test_a_stripped_bound_is_said_in_the_description_instead():
    """The documented workaround, and the reason a stripped constraint is not
    a lost one: the model is still told, and Pydantic still enforces."""
    weeks = schema_for(Answer).json_schema["properties"]["weeks"]

    assert "minimum" not in weeks
    assert weeks["description"] == "Must be at least 1, at most 12."


def test_an_existing_description_keeps_its_own_words():
    class Described(BaseModel):
        weeks: int = Field(ge=1, description="How long the migration takes.")

    described = schema_for(Described).json_schema["properties"]["weeks"]

    assert described["description"] == "How long the migration takes. Must be at least 1."


def test_min_items_survives_only_at_the_two_values_the_dialect_accepts():
    class Lists(BaseModel):
        one: tuple[str, ...] = Field(min_length=1)
        three: tuple[str, ...] = Field(min_length=3)

    properties = schema_for(Lists).json_schema["properties"]

    assert properties["one"]["minItems"] == 1
    assert "minItems" not in properties["three"]
    assert "at least 3 entries" in properties["three"]["description"]


def test_an_unsupported_string_format_is_dropped_and_a_supported_one_is_kept():
    class Formats(BaseModel):
        when: datetime
        where: str = Field(json_schema_extra={"format": "hostname"})
        what: str = Field(json_schema_extra={"format": "praxis-span"})

    properties = schema_for(Formats).json_schema["properties"]

    assert properties["when"]["format"] == "date-time"
    assert properties["where"]["format"] == "hostname"
    assert "format" not in properties["what"]


def test_a_nested_model_keeps_its_internal_reference_and_is_closed_too():
    schema = schema_for(Answer).json_schema
    evidence = schema["$defs"]["Evidence"]

    assert schema["properties"]["evidence"]["items"]["$ref"] == "#/$defs/Evidence"
    assert evidence["additionalProperties"] is False
    assert set(evidence["required"]) == {"quote", "span_id"}


def test_an_enum_survives_reduction():
    assert schema_for(Answer).json_schema["$defs"]["Verdict"]["enum"] == ["keep", "drop"]


def keywords_in(node, found=None):
    """Every JSON Schema *keyword* in a schema tree, property names excluded.

    Written out rather than grepping the rendered JSON, because `Answer` has a
    field called `title` and a check that could not tell a keyword from a
    property name would be testing the wrong thing in both directions.
    """
    found = set() if found is None else found
    if isinstance(node, list):
        for item in node:
            keywords_in(item, found)
    elif isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            if key in {"properties", "$defs", "definitions"}:
                for child in value.values():
                    keywords_in(child, found)
            else:
                keywords_in(value, found)
    return found


def test_titles_are_dropped_because_the_property_name_already_says_it():
    schema = schema_for(Answer).json_schema

    assert "title" not in keywords_in(schema)
    # And the field actually named `title` is untouched.
    assert schema["properties"]["title"]["type"] == "string"


def test_an_all_of_wrapping_one_reference_is_flattened():
    """Pydantic wraps a referenced model in `allOf` as soon as the field has a
    description of its own, and the dialect rejects `allOf` with `$ref`."""

    class Wrapped(BaseModel):
        evidence: Evidence = Field(description="Where this came from.")

    field = schema_for(Wrapped).json_schema["properties"]["evidence"]

    assert "allOf" not in field
    assert field["$ref"] == "#/$defs/Evidence"


def test_an_open_ended_mapping_is_refused_when_the_model_is_asked_for():
    """Rather than on the first live call, which -- given the default provider
    is offline -- could be weeks later."""

    class Bag(BaseModel):
        fields: dict[str, str]

    with pytest.raises(SchemaNotSupportedError, match="additionalProperties"):
        schema_for(Bag)


def test_a_recursive_model_is_refused():
    class Node(BaseModel):
        label: str
        children: tuple[Node, ...] = ()

    with pytest.raises(SchemaNotSupportedError, match="recursive"):
        schema_for(Node)


def test_recursion_through_a_second_model_is_refused_too():
    class Left(BaseModel):
        right: Right | None = None

    class Right(BaseModel):
        left: Left | None = None

    Left.model_rebuild()

    with pytest.raises(SchemaNotSupportedError, match="recursive"):
        schema_for(Left)


# -- the reduced schema is still answerable ----------------------------------


def test_the_mock_can_answer_a_reduced_schema_and_pydantic_accepts_it(settings):
    """The pairing that makes the offline pipeline real.

    Reduction removes the bounds the synthesiser would otherwise respect, so
    this is the test that says the two still fit together for the shapes this
    project uses. A response model that breaks it should break here rather
    than inside an agent.
    """
    result = ask_for(provider(settings), request(), Answer)

    assert result.attempts == 1
    assert isinstance(result.value, Answer)


def test_the_answer_quotes_the_source_it_was_given(settings):
    """What makes an offline citation checkable rather than merely well-formed:
    VerifierAgent has something real to check, and can still reject it."""
    result = ask_for(provider(settings), request(), Answer)

    assert any(evidence.quote in SOURCE for evidence in result.value.evidence)


def test_the_same_question_gets_the_same_answer(settings):
    first = ask_for(provider(settings), request(), Answer)
    second = ask_for(provider(settings), request(), Answer)

    assert first.value == second.value


# -- repair ------------------------------------------------------------------


def test_a_truncated_answer_is_repaired_rather_than_raised(settings):
    """The loop working, which is the only reason it exists.

    Which calls truncate is decided by the replay key rather than by a
    generator, so at a share of one half some first attempts fail and their
    repairs -- a different conversation, so a different key -- succeed. The
    sample is walked to find one rather than a single seed being trusted to be
    the interesting case, which is the mistake Phase 2 recorded.
    """
    for index in range(20):
        sink = MemoryTraceSink()
        misbehaving = MockProvider(sink=sink, settings=settings, malformed_share=0.5)
        try:
            result = ask_for(misbehaving, request(task=f"segment-{index}"), Answer)
        except MalformedOutputError:
            continue
        if result.attempts > 1:
            assert sink.traces[0].outcome is CallOutcome.MALFORMED
            assert [trace.attempt for trace in sink.traces] == [1, result.attempts]
            return
    pytest.fail("no call in the sample needed repairing, so the loop went untested")


def test_a_call_that_never_parses_gives_up_after_the_last_attempt(settings):
    sink = MemoryTraceSink()

    with pytest.raises(MalformedOutputError):
        ask_for(MockProvider(sink=sink, settings=settings, malformed_share=1.0), request(), Answer)

    assert len(sink.traces) == REPAIR_ATTEMPTS
    assert all(trace.outcome is CallOutcome.MALFORMED for trace in sink.traces)


def test_giving_up_carries_the_schema_the_attempts_and_the_last_answer(settings):
    with pytest.raises(MalformedOutputError) as caught:
        ask_for(
            MockProvider(sink=MemoryTraceSink(), settings=settings, malformed_share=1.0),
            request(),
            Answer,
        )

    failure = caught.value
    assert failure.schema_name == "Answer"
    assert failure.attempts == REPAIR_ATTEMPTS
    assert failure.raw
    assert "not valid JSON" in failure.detail


def test_the_repair_prompt_carries_the_bad_answer_and_the_reason(settings):
    sink = MemoryTraceSink()

    with pytest.raises(MalformedOutputError):
        ask_for(
            MockProvider(sink=sink, settings=settings, malformed_share=1.0),
            request(),
            Answer,
            max_attempts=2,
        )

    repaired = json.loads(sink.traces[1].request_json)
    roles = [message["role"] for message in repaired["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert "could not be used" in repaired["messages"][2]["content"]


def test_one_attempt_means_no_repair_at_all(settings):
    sink = MemoryTraceSink()

    with pytest.raises(MalformedOutputError):
        ask_for(
            MockProvider(sink=sink, settings=settings, malformed_share=1.0),
            request(),
            Answer,
            max_attempts=1,
        )

    assert len(sink.traces) == 1


def test_valid_json_that_is_the_wrong_shape_is_also_repaired(settings):
    """The failure the provider cannot classify: it parsed, so the seam calls
    it OK, and only the response model knows it is unusable."""

    class Narrow(BaseModel):
        chosen_option: str
        rejected_options: tuple[str, ...] = Field(min_length=2)

    repairs = 0
    for index in range(20):
        sink = MemoryTraceSink()
        try:
            result = ask_for(
                MockProvider(sink=sink, settings=settings), request(task=f"narrow-{index}"), Narrow
            )
        except MalformedOutputError:
            continue
        assert len(result.value.rejected_options) >= 2
        # Every attempt parsed as JSON, so the seam recorded them all as fine.
        # Only the response model knew the short list was unusable.
        assert all(trace.outcome is CallOutcome.OK for trace in sink.traces)
        repairs += result.attempts - 1
    assert repairs > 0, "the minItems the dialect dropped was never actually violated"


# -- the request the caller supplies -----------------------------------------


def test_the_schema_is_filled_in_from_the_response_model(settings):
    sink = MemoryTraceSink()

    ask_for(MockProvider(sink=sink, settings=settings), request(), Answer)

    assert json.loads(sink.traces[0].request_json)["schema"]["name"] == "Answer"


def test_asking_for_one_shape_and_parsing_another_is_refused(settings):
    asked = LLMRequest(
        agent=AGENT,
        task="segment",
        system="s",
        messages=(Message(role=MessageRole.USER, content=SOURCE),),
        schema=schema_for(Evidence),
    )

    with pytest.raises(ValueError, match="Evidence"):
        ask_for(provider(settings), asked, Answer)


def test_the_same_model_is_reduced_once_and_the_result_is_shared():
    """Reduction is ~2ms and `ask_for` asks for it on every call."""
    assert schema_for(Answer) is schema_for(Answer)


def test_a_shared_schema_cannot_be_edited_by_a_caller():
    """The price of sharing one object: it has to be read-only."""
    with pytest.raises(TypeError):
        schema_for(Answer).json_schema["properties"] = {}  # type: ignore[index]
