"""The default provider, tested as the thing the whole suite actually runs on.

`MockProvider` is not a test double. It is the implementation invariant 1 names
-- every test, eval run and CLI command reaches a model through it -- so the
claims worth making here are the ones the rest of the project relies on being
true offline: that an answer is a function of its request, that the answer
satisfies the schema that asked for it and quotes text that really exists, that
nothing is ever charged and tokens are nonetheless counted, and that the two
failure modes hardest to imitate can be produced on demand and reproducibly.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from praxis.config.models import MOCK_MODEL_ID, ModelRole
from praxis.config.settings import ProviderName, Settings
from praxis.llm.accounting import CostLedger
from praxis.llm.errors import ProviderRefusalError
from praxis.llm.mock import CHARS_PER_TOKEN_ESTIMATE, MockProvider
from praxis.llm.synthesis import identifiers_in, passages_in, sentences_of
from praxis.llm.trace import MemoryTraceSink
from praxis.llm.types import (
    CallOutcome,
    LLMRequest,
    Message,
    MessageRole,
    ResponseSchema,
    StopReason,
)

DOCUMENT = (
    "Reading document DOC-0007.\n"
    "SPAN-0123456789abcdef says: we chose SQLite over Postgres for the graph store.\n"
    "SPAN-fedcba9876543210 says: the migration should take no more than six weeks.\n"
)

CANDIDATE_SCHEMA = ResponseSchema(
    name="DecisionCandidates",
    json_schema={
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "quote": {"type": "string"},
                        "span_id": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "impact": {"enum": ["low", "medium", "high"]},
                    },
                },
            }
        },
    },
)


ORDINAL_SCHEMA = ResponseSchema(
    name="OrdinalCandidates",
    json_schema={
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "quote": {"type": "string"},
                        "span_ordinal": {"type": "integer"},
                    },
                },
            }
        },
    },
)
"""A listing-cited answer, which `CANDIDATE_SCHEMA` is not -- it cites by id."""


def request(**overrides) -> LLMRequest:
    """Build a valid request, overriding one field at a time."""
    fields = {
        "agent": "DecisionScout",
        "task": "scan_for_decisions",
        "system": "You find decisions in engineering documents.",
        "messages": (Message(role=MessageRole.USER, content=DOCUMENT),),
    }
    fields.update(overrides)
    return LLMRequest(**fields)


class TestItNeedsNothing:
    def test_it_answers_with_no_configuration_at_all(self):
        # Invariant 1. A provider that needed a key, a store or a directory
        # here would make the no-credentials promise conditional.
        assert MockProvider().complete(request()).text

    def test_it_is_what_the_default_settings_select(self):
        assert Settings().llm_provider is ProviderName.MOCK

    def test_it_reports_itself_as_the_mock_on_every_trace(self):
        sink = MemoryTraceSink()
        MockProvider(sink=sink).complete(request())
        assert sink.traces[0].provider == ProviderName.MOCK.value

    def test_it_never_claims_to_be_a_real_model(self):
        # A metric computed over a mixture of mock and live rows is a metric
        # about nothing, so the mixture has to be visible in the table.
        assert MockProvider().complete(request()).model_id == MOCK_MODEL_ID

    def test_routing_still_happens_even_though_the_model_is_fake(self):
        # The role is what a mock run reports, so an offline run says which
        # model the live run it stands in for would have used.
        sink = MemoryTraceSink()
        MockProvider(sink=sink).complete(request())
        assert sink.traces[0].role is ModelRole.SCAN


class TestTheAnswerIsAFunctionOfTheRequest:
    def test_the_same_request_gets_the_same_answer(self):
        assert MockProvider().complete(request()).text == MockProvider().complete(request()).text

    def test_call_order_does_not_change_an_answer(self):
        # Phase 4 requires a parallel run and a serial run to agree. A provider
        # carrying state between calls is the usual way that stops being true.
        provider = MockProvider()
        provider.complete(request(task="something_else"))
        first = provider.complete(request()).text
        assert first == MockProvider().complete(request()).text

    def test_a_different_prompt_gets_a_different_answer(self):
        other = request(messages=(Message(role=MessageRole.USER, content="Unrelated text here."),))
        assert MockProvider().complete(request()).text != MockProvider().complete(other).text

    def test_a_different_agent_asking_the_same_thing_answers_differently(self):
        # The agent is part of the replay key because it picks the model, so
        # two agents sending identical words are not asking the same question.
        other = request(agent="EstimateExtractor", task="scan_for_estimates")
        assert MockProvider().complete(request()).text != MockProvider().complete(other).text


class TestStructuredOutput:
    def test_a_schema_call_comes_back_as_json(self):
        response = MockProvider().complete(request(schema=CANDIDATE_SCHEMA))
        assert json.loads(response.text)["candidates"]

    def test_the_answer_satisfies_the_schema_that_asked_for_it(self):
        candidate = json.loads(MockProvider().complete(request(schema=CANDIDATE_SCHEMA)).text)[
            "candidates"
        ][0]
        assert candidate["impact"] in {"low", "medium", "high"}
        assert 0 <= candidate["confidence"] <= 1

    def test_a_quotation_really_occurs_in_the_document(self):
        # Invariant 6 offline: VerifierAgent has something real to check, and
        # can still fail, which a fixed-string mock could not arrange.
        candidate = json.loads(MockProvider().complete(request(schema=CANDIDATE_SCHEMA)).text)[
            "candidates"
        ][0]
        assert candidate["quote"] in sentences_of(DOCUMENT)

    def test_a_citation_names_a_span_the_agent_was_shown(self):
        candidate = json.loads(MockProvider().complete(request(schema=CANDIDATE_SCHEMA)).text)[
            "candidates"
        ][0]
        assert candidate["span_id"] in identifiers_in(DOCUMENT)

    def test_a_free_text_call_is_not_json(self):
        with pytest.raises(json.JSONDecodeError):
            json.loads(MockProvider().complete(request()).text)

    def test_a_structured_call_is_recorded_as_a_clean_outcome(self):
        sink = MemoryTraceSink()
        MockProvider(sink=sink).complete(request(schema=CANDIDATE_SCHEMA))
        assert sink.traces[0].outcome is CallOutcome.OK


class TestMoneyAndTokens:
    def test_nothing_is_ever_charged(self):
        provider = MockProvider()
        provider.complete(request())
        assert provider.ledger.spent_usd == Decimal("0")

    def test_the_ceiling_is_not_consulted_at_all(self):
        # bills=False is what exempts the offline providers, rather than a
        # special case inside the check. A ceiling this low would stop a live
        # call before it was made.
        provider = MockProvider(ledger=CostLedger(ceiling_usd=Decimal("0.0000001")))
        assert provider.complete(request()).text

    def test_tokens_are_counted_even_though_they_are_free(self):
        # The eval harness reports what an offline run would have cost, and
        # this is the cheapest place in the system to produce that column.
        usage = MockProvider().complete(request()).usage
        assert usage.input_tokens > 0
        assert usage.output_tokens > 0

    def test_the_input_count_follows_the_prompt_it_was_given(self):
        small = MockProvider().complete(request()).usage.input_tokens
        long_document = request(messages=(Message(role=MessageRole.USER, content=DOCUMENT * 20),))
        assert MockProvider().complete(long_document).usage.input_tokens > small

    def test_the_estimate_is_a_ratio_and_not_a_tokenizer(self):
        # Stated rather than hidden: counting exactly needs a round trip, which
        # is the one thing the offline provider may not do.
        provider_usage = MockProvider().complete(request()).usage
        assert provider_usage.input_tokens == -(
            -len(request().prompt_text) // CHARS_PER_TOKEN_ESTIMATE
        )

    def test_the_reported_output_never_exceeds_the_cap_that_was_set(self):
        response = MockProvider().complete(request(schema=CANDIDATE_SCHEMA, max_tokens=1))
        assert response.usage.output_tokens <= 1


class TestTheFailuresWorthImitating:
    def test_by_default_nothing_misbehaves(self):
        provider = MockProvider()
        assert all(
            provider.complete(request(task=f"task_{n}")).stop_reason is StopReason.END_TURN
            for n in range(20)
        )

    def test_a_truncated_answer_is_recorded_as_malformed(self):
        # The most common cause of malformed structured output is a max_tokens
        # cut, and the repair loop above this has to have something to repair.
        sink = MemoryTraceSink()
        provider = MockProvider(sink=sink, malformed_share=1.0)
        response = provider.complete(request(schema=CANDIDATE_SCHEMA))
        assert response.stop_reason is StopReason.MAX_TOKENS
        assert sink.traces[0].outcome is CallOutcome.MALFORMED
        with pytest.raises(json.JSONDecodeError):
            json.loads(response.text)

    def test_a_truncated_answer_keeps_the_text_it_managed_to_produce(self):
        response = MockProvider(malformed_share=1.0).complete(request(schema=CANDIDATE_SCHEMA))
        assert response.text.startswith('{"candidates"')

    def test_a_free_text_call_is_never_truncated(self):
        # There is no schema to fail, so a cut answer would be a shorter
        # answer rather than a malformed one, and nothing downstream notices.
        assert MockProvider(malformed_share=1.0).complete(request()).stop_reason is (
            StopReason.END_TURN
        )

    def test_a_refusal_raises_and_is_written_down_first(self):
        sink = MemoryTraceSink()
        provider = MockProvider(sink=sink, refusal_share=1.0)
        with pytest.raises(ProviderRefusalError):
            provider.complete(request())
        assert sink.traces[0].outcome is CallOutcome.REFUSED

    def test_which_call_fails_is_decided_by_the_request_not_by_chance(self):
        # A bug found offline has to be reproducible from its trace row, which
        # it is not if failure depends on how many calls preceded it.
        first = [
            _refused(MockProvider(refusal_share=0.5), request(task=f"task_{n}")) for n in range(30)
        ]
        second = [
            _refused(MockProvider(refusal_share=0.5), request(task=f"task_{n}")) for n in range(30)
        ]
        assert first == second
        assert any(first) and not all(first)

    def test_the_two_failures_are_drawn_independently(self):
        # Salted per purpose, so the requests that refuse are not also exactly
        # the requests that truncate.
        refused = {
            n for n in range(40) if _refused(MockProvider(refusal_share=0.5), request(task=f"t{n}"))
        }
        malformed = {
            n
            for n in range(40)
            if MockProvider(malformed_share=0.5)
            .complete(request(task=f"t{n}", schema=CANDIDATE_SCHEMA))
            .is_truncated
        }
        assert refused != malformed

    @pytest.mark.parametrize("share", [-0.1, 1.1])
    def test_a_share_outside_zero_to_one_is_refused(self, share):
        # Silently meaning "never" would hide a misconfigured test.
        with pytest.raises(ValueError, match="0 to 1"):
            MockProvider(malformed_share=share)

    def test_the_refusal_share_is_checked_too(self):
        with pytest.raises(ValueError, match="refusal_share"):
            MockProvider(refusal_share=2.0)


def _refused(provider: MockProvider, call: LLMRequest) -> bool:
    """Whether this provider declines this request."""
    try:
        provider.complete(call)
    except ProviderRefusalError:
        return True
    return False


class TestCitingCoherently:
    """ADR 0034's flag, seen from the provider rather than the synthesiser."""

    OFFERED = """Choose a passage and quote it.

[0] We are going with OpenSearch on managed nodes, rather than Postgres.

[1] This rests on the assumption that the index stays under 50 GB.

[2] Nadeesha put this at four weeks of hands-on work, blocked aside.
"""

    def _candidates(self, *, coherent: bool):
        """The candidates one answer carries, cited into the listing above."""
        provider = MockProvider(cite_coherently=coherent)
        call = request(
            messages=(Message(role=MessageRole.USER, content=self.OFFERED),),
            schema=ORDINAL_SCHEMA,
        )
        return json.loads(provider.complete(call).text)["candidates"]

    def test_it_is_off_by_default(self):
        assert MockProvider().cite_coherently is False

    def test_the_default_answer_is_unchanged_by_the_flag_being_off(self):
        """ADR 0034 assumption 1. Nine phases of numbers rest on this."""
        call = request(schema=CANDIDATE_SCHEMA)

        assert (
            MockProvider(cite_coherently=False).complete(call).text
            == MockProvider().complete(call).text
        )

    def test_with_it_on_a_quotation_is_in_the_passage_it_cites(self):
        found = passages_in(self.OFFERED)

        for candidate in self._candidates(coherent=True):
            assert candidate["quote"] in found[candidate["span_ordinal"]]

    def test_with_it_off_they_disagree(self):
        found = passages_in(self.OFFERED)
        drawn = self._candidates(coherent=False)

        assert any(c["quote"] not in found[c["span_ordinal"]] for c in drawn)
