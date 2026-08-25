"""AssumptionFormalizer: what it compiles, what it marks, and what it never drops.

The test this file exists for is `test_an_uncompilable_assumption_is_kept_and_marked`.
Every other behaviour here follows from the design constraint Half A has carried
since `DecisionScout`: degrade quality, never correctness. A predicate nobody can
evaluate is a worse answer than one that parses and a far better one than silence,
because the monitor refuses to breach on it and a person can repair what they can
see.

The provider is `Answering`, a real `LLMProvider` subclass from the shared
conftest, so routing, the cost ledger and the trace sink stay in the path. A
double that skipped those would let this agent pass while the seam it depends on
was broken.
"""

from __future__ import annotations

import json

import pytest
from praxis.agents.errors import Refusal
from praxis.agents.formalizer import (
    UNCHECKABLE_CEILING,
    AssumptionFormalizer,
    Formalization,
    FormalizationRefusal,
    is_checkable,
)
from praxis.config.settings import Settings
from praxis.domain.records import Assumption, Document, Span
from praxis.llm.factory import provider_for
from praxis.predicates.parser import parses

from tests.agents.conftest import AT, Answering, Refusing

ASSUMPTION_INDEX = 9
"""The block holding the world assumption in the fixture ADR, which is where a
real predicate and a real expiry condition are both written."""


def answer(
    predicate: str | None = "index_size_gb <= 50",
    expiry: str | None = "when(indexed_documents >= 10000000)",
    confidence: float = 0.9,
    note: str | None = None,
) -> str:
    """One formalization answer as the structured layer would receive it."""
    return json.dumps(
        {
            "predicate": predicate,
            "expiry_condition": expiry,
            "confidence": confidence,
            "subject": "index_size_gb",
            "note": note,
        }
    )


def assumption_of(
    span: Span,
    *,
    predicate: str = "the index stays under 50 GB",
    expiry: str = "sometime next year",
    confidence: float = 0.8,
) -> Assumption:
    """An assumption as Phase 4's extractor leaves it: prose in both fields."""
    return Assumption(
        id="A-0001",
        statement="the index stays under 50 GB for the next year",
        predicate=predicate,
        expiry_condition=expiry,
        span_id=span.id,
        confidence=confidence,
        created_by="AssumptionExtractor",
        created_at=AT,
    )


@pytest.fixture
def evidence(spans: tuple[Span, ...]) -> Span:
    """The passage the fixture ADR states the assumption in."""
    return spans[ASSUMPTION_INDEX]


def formalized(provider: Answering, evidence: Span, **kwargs) -> Formalization:
    """Run the agent and insist it produced a formalization rather than a refusal."""
    result = AssumptionFormalizer(provider).formalize(
        assumption_of(evidence, **kwargs), evidence, at=AT
    )
    assert isinstance(result, Formalization)
    return result


class TestWhatCountsAsFormalized:
    def test_an_assumption_whose_fields_both_parse_is_checkable(self, spans):
        record = assumption_of(
            spans[ASSUMPTION_INDEX],
            predicate="index_size_gb <= 50",
            expiry='on_event("the work ships")',
        )
        assert is_checkable(record)

    def test_prose_in_either_field_is_not(self, spans):
        span = spans[ASSUMPTION_INDEX]
        assert not is_checkable(assumption_of(span))
        assert not is_checkable(
            assumption_of(span, predicate="index_size_gb <= 50", expiry="next year")
        )

    def test_being_formalized_is_a_parse_and_not_a_stored_flag(self, spans):
        # Deliberately not a column. A flag can disagree with the text beside
        # it; a parse cannot, and the monitor asks the same question.
        record = assumption_of(spans[ASSUMPTION_INDEX], predicate="index_size_gb <= 50")
        assert parses(record.predicate)
        assert not is_checkable(record)  # its expiry is still prose


class TestCompilingAnAssumption:
    def test_a_parseable_answer_is_stored_and_marked_checkable(self, evidence):
        result = formalized(Answering([answer()]), evidence)
        assert result.checkable
        assert result.assumption.predicate == "index_size_gb <= 50"
        assert result.assumption.expiry_condition == "when(indexed_documents >= 10000000)"
        assert result.note == ""

    def test_the_stored_predicate_is_normalised_rather_than_the_model_s_spelling(self, evidence):
        # So two assumptions about one quantity land in one blocking bucket in
        # ContradictionDetector instead of two.
        result = formalized(Answering([answer(predicate="index_size_gb<=50")]), evidence)
        assert result.assumption.predicate == "index_size_gb <= 50"

    def test_a_bare_date_expiry_is_normalised_into_an_instant(self, evidence):
        result = formalized(Answering([answer(expiry='after("2026-08-31")')]), evidence)
        assert result.assumption.expiry_condition == 'after("2026-08-31T00:00:00+00:00")'

    def test_the_two_confidences_multiply(self, evidence):
        # Two probabilities about two different questions -- is this an
        # assumption the decision rests on, and is this predicate a faithful
        # rendering of it.
        result = formalized(Answering([answer(confidence=0.5)]), evidence, confidence=0.8)
        assert result.assumption.confidence == pytest.approx(0.4)

    def test_the_next_version_carries_this_run_s_timestamp(self, evidence):
        assert formalized(Answering([answer()]), evidence).assumption.created_at == AT

    def test_the_record_is_returned_rather_than_written(self, evidence):
        # The agent holds no store: the caller writes. The same seam every
        # Phase 4 agent takes, and what lets this be tested without SQLite.
        result = formalized(Answering([answer()]), evidence)
        assert result.assumption.version == 1

    def test_the_reason_names_the_predicate_it_compiled(self, evidence):
        # It becomes the audit row's reason, which is the one field in an audit
        # trail a person actually reads.
        assert "index_size_gb <= 50" in formalized(Answering([answer()]), evidence).reason


class TestWhatIsKeptAndMarked:
    def test_an_uncompilable_assumption_is_kept_and_marked(self, evidence):
        # The test this file exists for. Silence cannot be fixed by anyone.
        result = formalized(Answering([answer(predicate="the team stays motivated")]), evidence)
        assert not result.checkable
        assert result.assumption.predicate == "the team stays motivated"
        assert "does not parse" in result.note

    def test_an_uncheckable_formalization_has_its_confidence_capped(self, evidence):
        result = formalized(
            Answering([answer(predicate="not an expression", confidence=1.0)]),
            evidence,
            confidence=1.0,
        )
        assert result.assumption.confidence <= UNCHECKABLE_CEILING

    def test_an_unparseable_expiry_alone_makes_the_whole_thing_uncheckable(self, evidence):
        # Both halves have to parse: the monitor needs the predicate to reach a
        # verdict and the condition to know whether the verdict is stale.
        result = formalized(Answering([answer(expiry="whenever we remember")]), evidence)
        assert not result.checkable
        assert result.assumption.predicate == "index_size_gb <= 50"

    def test_an_omitted_predicate_leaves_the_original_text_in_place(self, evidence):
        result = formalized(Answering([answer(predicate=None)]), evidence)
        assert result.assumption.predicate == "the index stays under 50 GB"
        assert not result.checkable

    def test_an_omitted_expiry_leaves_the_original_text_in_place(self, evidence):
        result = formalized(Answering([answer(expiry=None)]), evidence)
        assert result.assumption.expiry_condition == "sometime next year"

    def test_a_note_from_the_model_survives_into_the_reason(self, evidence):
        result = formalized(
            Answering([answer(predicate="morale >= 1", note="motivation is not measured")]),
            evidence,
        )
        assert "motivation is not measured" in result.note

    def test_a_marked_formalization_still_reads_as_a_best_attempt(self, evidence):
        result = formalized(Answering([answer(predicate="nothing here")]), evidence)
        assert "best attempt" in result.reason


class TestTheRetry:
    def test_a_malformed_predicate_is_sent_back_with_its_parse_error(self, evidence):
        provider = Answering([answer(predicate="index_size_gb"), answer()])
        result = formalized(provider, evidence)
        assert result.checkable
        assert result.calls == 2
        assert "does not parse" in provider.requests[1].messages[-1].content

    def test_the_complaint_quotes_what_would_not_parse(self, evidence):
        provider = Answering([answer(predicate="index_size_gb"), answer()])
        formalized(provider, evidence)
        assert "index_size_gb" in provider.requests[1].messages[-1].content

    def test_a_good_first_answer_costs_one_call(self, evidence):
        provider = Answering([answer()])
        assert formalized(provider, evidence).calls == 1

    def test_the_retry_is_bounded_and_the_last_attempt_is_kept(self, evidence):
        # Two would be a repair loop, and praxis.llm.structured already owns
        # that concept for the schema. This is the narrower question of whether
        # the answer is well formed in a different language.
        provider = Answering([answer(predicate="one"), answer(predicate="two")])
        result = formalized(provider, evidence)
        assert result.calls == 2
        assert result.assumption.predicate == "two"
        assert not result.checkable

    def test_retries_can_be_turned_off(self, evidence):
        provider = Answering([answer(predicate="index_size_gb"), answer()])
        result = AssumptionFormalizer(provider, retries=0).formalize(
            assumption_of(evidence), evidence, at=AT
        )
        assert isinstance(result, Formalization)
        assert result.calls == 1

    def test_a_negative_retry_count_is_refused(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            AssumptionFormalizer(Answering([]), retries=-1)


class TestWhenNothingUsableComesBack:
    def test_a_refusal_is_reported_rather_than_raised(self, evidence):
        # A run over two hundred assumptions that dies on the third has said
        # nothing about the other hundred and ninety-seven.
        result = AssumptionFormalizer(Refusing([])).formalize(
            assumption_of(evidence), evidence, at=AT
        )
        assert isinstance(result, FormalizationRefusal)
        assert result.refusal is Refusal.NO_USABLE_ANSWER
        assert result.assumption_id == "A-0001"

    def test_a_refusal_is_not_the_same_thing_as_an_uncheckable_formalization(self, evidence):
        # One has an attempt worth keeping and one does not, and the eval table
        # reports them apart.
        refused = AssumptionFormalizer(Refusing([])).formalize(
            assumption_of(evidence), evidence, at=AT
        )
        kept = formalized(Answering([answer(predicate="prose")]), evidence)
        assert isinstance(refused, FormalizationRefusal)
        assert isinstance(kept, Formalization)


class TestWhatTheModelIsShown:
    def test_the_passage_is_shown_because_it_usually_holds_the_predicate(self, evidence) -> None:
        # An ADR writes "In predicate form: `index_size_gb <= 50`." Copying that
        # is better evidence than inferring one, and it is what lets the offline
        # provider answer with a predicate that really occurs.
        provider = Answering([answer()])
        formalized(provider, evidence)
        assert evidence.text in provider.requests[0].messages[0].content

    def test_the_unparseable_stored_text_is_shown_as_a_hint(self, evidence):
        provider = Answering([answer()])
        formalized(provider, evidence)
        assert "the index stays under 50 GB" in provider.requests[0].messages[0].content

    def test_the_call_names_the_prompt_behind_it(self, evidence):
        # ADR 0014: a metrics table has to be able to name the bytes that
        # produced it.
        provider = Answering([answer()])
        formalized(provider, evidence)
        assert provider.requests[0].prompt_id == "formalize_assumption@v1"
        assert provider.requests[0].prompt_sha

    def test_the_call_is_attributed_to_the_agent_the_routing_table_names(self, evidence):
        provider = Answering([answer()])
        formalized(provider, evidence)
        assert provider.requests[0].agent == "AssumptionFormalizer"


class TestOffline:
    def test_the_mock_produces_a_predicate_that_really_parses(
        self, document: Document, evidence: Span
    ):
        # Phase 4's report had to say every extraction score was zero because
        # the mock drew each field independently. Here it draws the predicate
        # from the passage, so the storing path runs offline rather than only
        # the refusal path. The number this produces is still about the
        # plumbing and not about a model.
        result = AssumptionFormalizer(provider_for(Settings())).formalize(
            assumption_of(evidence), evidence, at=AT
        )
        assert isinstance(result, Formalization)
        assert result.checkable
        assert document.content.count(result.assumption.predicate) >= 1
