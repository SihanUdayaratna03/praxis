"""Synthesising an answer out of the question that asked for it.

Two families of claim are worth testing here and they pull in opposite
directions. One is that the answer *satisfies the schema*, because an agent
written against a mock that quietly violates its own contract will break on the
first live response. The other is that the answer is *made of the prompt* --
quotations that really occur, ids that were really supplied -- because that is
the only reason `VerifierAgent` has anything to check offline.

Determinism sits underneath both: every assertion here would be flaky if the
generator were not a function of the seed, and an eval harness whose numbers
moved between runs would be measuring the mock rather than the pipeline.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.llm.errors import SchemaNotSupportedError
from praxis.llm.synthesis import (
    MAX_DEPTH,
    MIN_SENTENCE_CHARS,
    identifiers_in,
    sentences_of,
    synthesise_answer,
    synthesise_prose,
    synthesise_value,
)
from praxis.llm.types import ResponseSchema

PROMPT = """You are extracting decisions from an engineering document.

Reading document DOC-0007.

SPAN-0123456789abcdef says: we chose SQLite over Postgres for the graph store.
SPAN-fedcba9876543210 says: the migration should take no more than six weeks.
SPAN-00112233445566aa says: nobody on the team has run Postgres in production.
"""

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {"$ref": "#/$defs/Decision"},
        },
        "notes": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "$defs": {
        "Decision": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "maxLength": 60},
                "quote": {"type": "string"},
                "span_id": {"type": "string"},
                "document_id": {"type": "string"},
                "scope": {"enum": ["personal", "team", "project", "organisation"]},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "is_reversible": {"type": "boolean"},
                "decided_on": {"type": "string", "format": "date"},
                "rationale": {"type": "string"},
            },
        }
    },
}


def answer(schema: dict, prompt: str = PROMPT, seed: str = "seed-1"):
    """Synthesise one value, with the usual prompt and seed."""
    return synthesise_value(schema, prompt, seed)


class TestSentences:
    def test_a_sentence_is_recovered_whole(self):
        found = sentences_of("We chose SQLite over Postgres for the graph store.")
        assert found == ("We chose SQLite over Postgres for the graph store.",)

    def test_two_sentences_on_one_line_are_split(self):
        found = sentences_of("We chose SQLite for the store. Postgres was the alternative.")
        assert len(found) == 2

    def test_a_bullet_is_a_sentence_like_any_other(self):
        # The corpus is ADRs and meeting notes, where most extractable claims
        # are bullets. Keeping the marker would put it inside every quotation.
        assert sentences_of("- We chose SQLite over Postgres for the store.")[0].startswith("We")

    def test_a_blockquote_marker_is_stripped_too(self):
        assert sentences_of("> We chose SQLite over Postgres for the store.")[0].startswith("We")

    def test_a_heading_is_too_short_to_quote(self):
        # A fragment this short verifies against half the corpus, which is a
        # weaker test of the citation gate than no citation at all.
        assert sentences_of("## Storage") == ()

    def test_the_threshold_is_the_documented_one(self):
        short = "x" * (MIN_SENTENCE_CHARS - 1)
        long = "y" * MIN_SENTENCE_CHARS
        assert sentences_of(f"{short}\n{long}") == (long,)

    def test_a_repeated_sentence_is_offered_once(self):
        # A quotation occurring twice cannot be resolved to one span, so it
        # would fail verification for a reason unrelated to the agent.
        text = "The migration should take six weeks.\nThe migration should take six weeks."
        assert len(sentences_of(text)) == 1

    def test_order_is_the_order_of_the_document(self):
        first, second = sentences_of(PROMPT)[:2]
        assert PROMPT.index(first) < PROMPT.index(second)


class TestIdentifiers:
    def test_a_content_addressed_id_is_found(self):
        assert "SPAN-0123456789abcdef" in identifiers_in(PROMPT)

    def test_a_sequential_id_is_found(self):
        assert "DOC-0007" in identifiers_in(PROMPT)

    def test_prose_that_merely_looks_like_an_id_is_not_one(self):
        assert identifiers_in("We shipped D-1 and SPAN-notahexdigest today.") == ()

    def test_ids_are_offered_once_each(self):
        assert identifiers_in("DOC-0007 and DOC-0007") == ("DOC-0007",)


class TestDeterminism:
    def test_the_same_seed_gives_the_same_answer(self):
        assert answer(DECISION_SCHEMA) == answer(DECISION_SCHEMA)

    def test_a_different_seed_gives_a_different_answer(self):
        # Not a cryptographic claim -- a check that the seed reaches the
        # generator at all, which is how this usually breaks.
        assert answer(DECISION_SCHEMA, seed="one") != answer(DECISION_SCHEMA, seed="two")

    def test_a_different_prompt_gives_a_different_answer(self):
        other = PROMPT.replace("SQLite", "DuckDB")
        assert answer(DECISION_SCHEMA, prompt=other) != answer(DECISION_SCHEMA)

    def test_a_date_comes_from_the_synthetic_epoch_not_the_clock(self):
        # A timestamp read from now() would make two runs over one corpus
        # differ in a column, which is the one thing offline mode promises.
        value = answer({"type": "string", "format": "date-time"})
        assert datetime.fromisoformat(value) >= datetime(2026, 1, 1, tzinfo=UTC)
        assert datetime.fromisoformat(value) < datetime(2027, 3, 1, tzinfo=UTC)

    def test_a_synthesised_datetime_is_timezone_aware(self):
        # Invariant 5 has no exception for a value a mock invented.
        value = answer({"type": "string", "format": "date-time"})
        assert datetime.fromisoformat(value).tzinfo is not None


class TestTheAnswerIsMadeOfThePrompt:
    def test_a_quotation_field_quotes_the_document(self):
        value = answer({"type": "object", "properties": {"quote": {"type": "string"}}})
        assert value["quote"] in PROMPT

    def test_every_quotation_alias_is_recognised(self):
        properties = {name: {"type": "string"} for name in ("excerpt", "statement", "evidence")}
        value = answer({"type": "object", "properties": properties})
        assert all(text in PROMPT for text in value.values())

    def test_an_id_field_cites_an_id_it_was_given(self):
        value = answer({"type": "object", "properties": {"span_id": {"type": "string"}}})
        assert value["span_id"] in identifiers_in(PROMPT)

    def test_an_id_field_prefers_the_kind_its_name_asks_for(self):
        value = answer({"type": "object", "properties": {"document_id": {"type": "string"}}})
        assert value["document_id"] == "DOC-0007"

    def test_a_citation_of_a_document_never_shown_is_well_formed_and_wrong(self):
        # Deliberate: a prompt carrying no ids gets a fabricated span id, which
        # VerifierAgent then rejects. That is the correct outcome, and it is
        # what a live model does in the same situation.
        value = answer(
            {"type": "object", "properties": {"span_id": {"type": "string"}}},
            prompt="There is nothing here that carries an identifier at all.",
        )
        assert value["span_id"].startswith("SPAN-")
        assert len(value["span_id"]) == len("SPAN-") + 16

    def test_a_rationale_is_prose_built_round_a_real_sentence(self):
        value = answer({"type": "object", "properties": {"rationale": {"type": "string"}}})
        assert any(sentence in value["rationale"] for sentence in sentences_of(PROMPT))

    def test_a_prompt_with_nothing_quotable_says_so(self):
        value = answer({"type": "string"}, prompt="Hi.")
        assert "no sentence long enough" in value


class TestSchemaConformance:
    def test_a_const_is_returned_as_written(self):
        assert answer({"const": "assumption_breach"}) == "assumption_breach"

    def test_an_enum_member_is_chosen(self):
        members = ["low", "medium", "high", "critical"]
        assert answer({"enum": members}) in members

    def test_a_boolean_is_a_boolean(self):
        assert isinstance(answer({"type": "boolean"}), bool)

    def test_a_null_type_is_none(self):
        assert answer({"type": "null"}) is None

    def test_a_nullable_union_still_carries_a_value(self):
        # The absent path is covered on purpose by the structured layer's
        # repair tests. A mock that took it at random would decide which of an
        # agent's branches gets exercised.
        assert answer({"anyOf": [{"type": "string"}, {"type": "null"}]}) is not None

    def test_a_nullable_type_list_still_carries_a_value(self):
        assert answer({"type": ["integer", "null"]}) is not None

    def test_an_object_is_inferred_from_its_properties(self):
        # A schema that omits "type" is common in hand-written fixtures, and
        # answering it with a string would fail validation for no good reason.
        value = answer({"properties": {"name": {"type": "string"}}})
        assert isinstance(value, dict)
        assert isinstance(value["name"], str)

    def test_every_declared_property_is_filled(self):
        value = answer(DECISION_SCHEMA)
        assert set(value) == {"decisions", "notes"}
        assert set(value["decisions"][0]) == set(DECISION_SCHEMA["$defs"]["Decision"]["properties"])

    def test_an_array_honours_its_bounds(self):
        value = answer({"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "string"}})
        assert len(value) == 2

    def test_an_array_without_items_is_empty(self):
        assert answer({"type": "array"}) == []

    def test_an_unbounded_array_is_plural_enough_to_catch_a_bug(self):
        # An agent that mishandles the plural case passes every test against a
        # single-element list.
        lengths = {
            len(answer({"type": "array", "items": {"type": "string"}}, seed=f"s{n}"))
            for n in range(20)
        }
        assert max(lengths) >= 2

    def test_unique_items_are_unique(self):
        value = answer(
            {
                "type": "array",
                "items": {"enum": ["a", "b"]},
                "minItems": 4,
                "maxItems": 4,
                "uniqueItems": True,
            }
        )
        assert len(value) == len(set(value))


class TestNumbers:
    def test_an_integer_is_an_integer(self):
        value = answer({"type": "integer"})
        assert isinstance(value, int)
        assert not isinstance(value, bool)

    def test_a_pinned_integer_has_one_answer(self):
        assert answer({"type": "integer", "minimum": 5, "maximum": 5}) == 5

    def test_an_impossible_integer_range_still_returns_a_number(self):
        # A bound of 0.2 to 0.8 contains no integer. Returning the floor beats
        # raising: the schema is the caller's bug, and a mock that dies on it
        # takes the whole offline run down with it.
        assert answer({"type": "integer", "minimum": 0.2, "maximum": 0.8}) == 1

    def test_exclusive_bounds_are_respected_after_rounding(self):
        # Rounding to two places is what a model writes, and it is also what
        # would step a value back onto a bound the schema excludes.
        for seed in range(30):
            value = answer(
                {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1}, seed=f"s{seed}"
            )
            assert 0 < value < 1

    def test_a_confidence_lands_where_a_model_would_put_one(self):
        # A confidence uniform over the whole unit interval is what the schema
        # permits and not what a model produces; an AbstentionGate tuned
        # against the first would be tuned against noise.
        values = [answer({"type": "number"}, seed=f"c{n}") for n in range(20)]
        confidences = [
            answer(
                {"type": "object", "properties": {"confidence": {"type": "number"}}}, seed=f"c{n}"
            )["confidence"]
            for n in range(20)
        ]
        assert all(0.0 <= value <= 1.0 for value in confidences)
        assert max(values) > 1.0

    def test_an_explicit_bound_beats_the_plausible_range(self):
        # The schema always wins: a value outside it is not an answer at all.
        for seed in range(10):
            value = answer(
                {
                    "type": "object",
                    "properties": {"confidence": {"type": "number", "minimum": 10, "maximum": 20}},
                },
                seed=f"b{seed}",
            )["confidence"]
            assert 10 <= value <= 20


class TestStringLengths:
    def test_a_long_answer_is_trimmed_to_the_maximum(self):
        assert len(answer({"type": "string", "maxLength": 12})) <= 12

    def test_a_short_answer_is_extended_to_the_minimum(self):
        assert len(answer({"type": "string", "minLength": 400})) >= 400

    def test_a_declared_format_is_honoured(self):
        # A value failing its own format would be rejected by the validation
        # the mock exists to exercise.
        assert answer({"type": "string", "format": "uri"}).startswith("https://")

    def test_a_fabricated_host_can_never_resolve(self):
        # RFC 2606 reserves .invalid. A synthesised URL that happened to be
        # real would be a mock capable of causing a network request.
        assert answer({"type": "string", "format": "uri"}).endswith(
            "example.invalid/praxis/synthesised"
        )

    def test_an_unknown_format_is_answered_like_any_other_string(self):
        # format is annotation in JSON Schema. Refusing one would make adding a
        # response model to an agent a change to the synthesiser.
        assert isinstance(answer({"type": "string", "format": "hostname"}), str)


class TestReferences:
    def test_a_ref_into_defs_is_followed(self):
        # Pydantic hoists every nested model into $defs, so a synthesiser that
        # ignored references would answer a nested schema with {} and call it
        # valid.
        assert answer(DECISION_SCHEMA)["decisions"][0]["scope"] in {
            "personal",
            "team",
            "project",
            "organisation",
        }

    def test_a_dangling_ref_is_refused_rather_than_guessed(self):
        with pytest.raises(SchemaNotSupportedError, match="does not carry"):
            answer({"$ref": "#/$defs/Missing"})

    def test_a_schema_nested_past_the_limit_is_refused(self):
        # The structured-output dialect has no recursion, so this is a caller's
        # bug and failing loudly beats running until the stack ends.
        schema: dict = {"type": "string"}
        for _ in range(MAX_DEPTH + 2):
            schema = {"type": "object", "properties": {"inner": schema}}
        with pytest.raises(SchemaNotSupportedError, match="nested past"):
            answer(schema)


class TestTheTextThatComesBack:
    def test_a_structured_answer_is_valid_json(self):
        text = synthesise_answer(ResponseSchema("Decisions", DECISION_SCHEMA), PROMPT, "seed")
        assert json.loads(text)

    def test_a_structured_answer_is_byte_identical_between_runs(self):
        # The trace store records this string, so two runs that differ in
        # whitespace would differ in a column nobody meant to change.
        schema = ResponseSchema("Decisions", DECISION_SCHEMA)
        assert synthesise_answer(schema, PROMPT, "s") == synthesise_answer(schema, PROMPT, "s")

    def test_a_free_text_call_gets_prose_and_not_json(self):
        text = synthesise_answer(None, PROMPT, "seed")
        with pytest.raises(json.JSONDecodeError):
            json.loads(text)

    def test_free_text_demonstrably_read_its_input(self):
        text = synthesise_prose(PROMPT, "seed")
        assert any(sentence in text for sentence in sentences_of(PROMPT))


class TestProperties:
    @given(seed=st.text(min_size=1, max_size=20))
    def test_a_quotation_is_always_really_in_the_prompt(self, seed):
        # Invariant 6 offline: VerifierAgent re-reads the span and rejects a
        # claim it does not contain, so a mock inventing quotations would let
        # the offline pipeline pass a check the live one has to earn.
        value = synthesise_value(
            {"type": "object", "properties": {"quote": {"type": "string"}}}, PROMPT, seed
        )
        assert value["quote"] in PROMPT

    @given(seed=st.text(min_size=1, max_size=20))
    def test_the_answer_is_a_function_of_the_seed(self, seed):
        assert synthesise_value(DECISION_SCHEMA, PROMPT, seed) == synthesise_value(
            DECISION_SCHEMA, PROMPT, seed
        )

    @given(prompt=st.text(max_size=200))
    def test_any_prompt_at_all_produces_a_valid_answer(self, prompt):
        # Including the empty one. The mock is the default provider, so a
        # prompt it cannot answer is a pipeline that cannot start.
        text = synthesise_answer(ResponseSchema("Decisions", DECISION_SCHEMA), prompt, "seed")
        assert json.loads(text)["decisions"]

    @given(prompt=st.text(max_size=200))
    def test_every_sentence_offered_is_really_in_the_text(self, prompt):
        assert all(sentence in prompt for sentence in sentences_of(prompt))
