"""DecisionStructurer, against answers built to be wrong in specific ways.

Every test here decides what the model says. That is the only way to test the
part of this agent worth testing: the structurer's job is not to extract well,
it is to refuse precisely, and a refusal can only be checked against an answer
whose defect is known. The provider is `tests.agents.conftest.Answering`, a real
`LLMProvider` subclass, so routing, the cost ledger and the trace sink stay in
the path exactly as they do live.

The quotations below are lifted from the fixture ADR rather than written here,
so a test that says "this quotation is in span 3" is making a claim about a
document the real adapter produced and the real block grid cut.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from praxis.agents.errors import ExtractionError, Refusal
from praxis.agents.scout import Candidate
from praxis.agents.structurer import (
    DEFAULT_REACH,
    NO_ALTERNATIVE_RECORDED,
    STRUCTURE_TASK,
    STRUCTURER_NAME,
    DecisionAnswer,
    DecisionStructurer,
    StructureResult,
)
from praxis.config.models import ModelRole, role_for_agent
from praxis.config.settings import Settings
from praxis.domain.enums import DecisionScope, DecisionStatus, Impact
from praxis.domain.links import LinkType
from praxis.ingest.verifier import ClaimMatch
from praxis.llm.mock import MockProvider
from praxis.llm.trace import MemoryTraceSink
from praxis.prompts.library import load

from tests.agents.conftest import ADR_BODY, AT, Answering, Refusing, make_document, spans_of

DECISION_INDEX = 3
"""Which span of the fixture ADR states the decision. See `conftest.ADR_BODY`."""

CANDIDATE_ORDINAL = 2
"""Where that span lands in the offering at `DEFAULT_REACH`: spans 1 to 5, so
the candidate is third. Asserted rather than assumed, below."""

DECISION_ID = "D-0001"

EXACT_QUOTE = "We are going with OpenSearch on managed nodes"
"""Character for character in span 3."""

WRAPPED_QUOTE = "managed nodes, rather than Postgres full-text search"
"""In span 3 with a newline where this has a space -- the whitespace latitude."""

HEADING_QUOTE = "ADR: the product search index"
"""In span 0, which is the only passage a candidate at the start can cite."""

NEIGHBOUR_QUOTE = "Decided 2026-01-05 by the Discovery team."
"""In span 1, which the offering also shows. A mis-attribution, not a lie."""

INVENTED_QUOTE = "We are going with Cassandra on bare metal in the Colombo rack."
"""In none of the passages, and in no other document either."""


def answer(**overrides: object) -> str:
    """A well-formed answer about the fixture ADR, spoilt to order."""
    return json.dumps(
        {
            "found": True,
            "title": "OpenSearch for the product index",
            "chosen": "OpenSearch on managed nodes",
            "rejected": [{"option": "Postgres full-text search", "reason": "ranking quality lost"}],
            "decision_maker": "Nadeesha",
            "decided_on": "2026-01-05",
            "rationale": "Ranking quality on our own queries was not close.",
            "scope": "team",
            "impact": "high",
            "status": "accepted",
            "confidence": 0.8,
            "evidence_ordinal": CANDIDATE_ORDINAL,
            "evidence_quote": EXACT_QUOTE,
        }
        | overrides
    )


def candidate_over(spans, index=DECISION_INDEX) -> Candidate:
    """What the scout would have handed over for that span."""
    return Candidate(
        span=spans[index],
        label="chose a search index",
        why="it says we are going with one",
        confidence=0.6,
    )


def structure(provider, spans, document, index=DECISION_INDEX, **kwargs) -> StructureResult:
    """Run one candidate through the agent."""
    agent = DecisionStructurer(provider, **kwargs)
    return agent.structure(
        candidate_over(spans, index), spans, document, decision_id=DECISION_ID, at=AT
    )


class TestRouting:
    def test_the_structurer_is_on_the_middle_tier(self):
        # ADR 0006's routing table. One call per candidate rather than per span,
        # which is what the scout's low precision buys and what pays for this.
        assert role_for_agent(STRUCTURER_NAME) is ModelRole.EXTRACT

    def test_the_agent_names_itself_and_never_a_model(self, spans, document):
        provider = Answering([answer()])
        structure(provider, spans, document)
        assert all(request.agent == STRUCTURER_NAME for request in provider.requests)


class TestPromptProvenance:
    def test_every_call_names_the_prompt_version_it_read(self, spans, document):
        provider = Answering([answer()])
        structure(provider, spans, document)
        prompt = load(STRUCTURE_TASK)
        assert provider.requests[0].prompt_id == prompt.id
        assert provider.requests[0].prompt_sha == prompt.sha256

    def test_the_task_and_the_prompt_name_agree(self):
        assert load(STRUCTURE_TASK).name == STRUCTURE_TASK

    def test_the_prompt_is_told_which_passage_the_scout_marked(self, spans, document):
        provider = Answering([answer()])
        structure(provider, spans, document)
        assert f"passage {CANDIDATE_ORDINAL}" in provider.requests[0].system


class TestWhatIsOffered:
    def test_the_candidate_is_offered_with_its_neighbours(self, spans, document):
        provider = Answering([answer()])
        structure(provider, spans, document)
        shown = provider.requests[0].messages[0].content
        neighbours = spans[DECISION_INDEX - DEFAULT_REACH : DECISION_INDEX + DEFAULT_REACH + 1]
        assert len(neighbours) == 2 * DEFAULT_REACH + 1
        for span in neighbours:
            assert span.text.strip().splitlines()[0] in shown

    def test_the_candidate_is_not_marked_in_the_listing(self, spans, document):
        # The agent has to pick the decision out of its neighbours, which is the
        # question the live run asks. Marking it would ask an easier one.
        provider = Answering([answer()])
        structure(provider, spans, document)
        assert "candidate" not in provider.requests[0].messages[0].content.lower()

    def test_reach_zero_offers_the_candidate_alone(self, spans, document):
        provider = Answering([answer(evidence_ordinal=0)])
        result = structure(provider, spans, document, reach=0)
        assert result.ok
        assert provider.requests[0].messages[0].content.startswith("[0] ")
        assert "[1] " not in provider.requests[0].messages[0].content

    def test_a_candidate_near_the_start_is_still_offered_in_full(self, spans, document):
        # The window is clipped, not shifted, so the candidate's ordinal moves.
        # An agent that assumed it is always `reach` would cite the wrong span.
        provider = Answering([answer(evidence_ordinal=0, evidence_quote=HEADING_QUOTE)])
        result = structure(provider, spans, document, index=0)
        assert result.ok
        assert result.decision.decision.span_id == spans[0].id
        assert "passage 0" in provider.requests[0].system

    @pytest.mark.parametrize("reach", [-1, -5])
    def test_reach_is_a_distance(self, reach):
        with pytest.raises(ValueError, match="cannot be"):
            DecisionStructurer(Answering([]), reach=reach)

    def test_a_candidate_from_another_document_is_a_bug_not_an_answer(self, spans, document):
        # `ExtractionError` rather than a rejection: the model did not do this,
        # the caller did, and mixing two documents has to fail loudly.
        other = spans_of(make_document(doc_id="DOC-0002"))
        agent = DecisionStructurer(Answering([]))
        with pytest.raises(ExtractionError, match="not one of"):
            agent.structure(candidate_over(other), spans, document, decision_id=DECISION_ID, at=AT)


class TestAVerifiedDecision:
    def test_a_well_cited_answer_becomes_a_decision(self, spans, document):
        result = structure(Answering([answer()]), spans, document)
        assert result.ok
        decision = result.decision.decision
        assert decision.id == DECISION_ID
        assert decision.title == "OpenSearch for the product index"
        assert decision.chosen == "OpenSearch on managed nodes"
        assert decision.decision_maker == "Nadeesha"
        assert decision.confidence == 0.8

    def test_the_decision_cites_the_span_the_quotation_is_in(self, spans, document):
        # Invariant 6, at the only place this agent could break it.
        result = structure(Answering([answer()]), spans, document)
        assert result.decision.decision.span_id == spans[DECISION_INDEX].id

    def test_the_agent_signs_what_it_made(self, spans, document):
        result = structure(Answering([answer()]), spans, document)
        assert result.decision.decision.created_by == STRUCTURER_NAME
        assert result.decision.decision.created_at == AT

    def test_the_enums_come_back_as_the_document_presented_them(self, spans, document):
        result = structure(Answering([answer()]), spans, document)
        decision = result.decision.decision
        assert decision.scope is DecisionScope.TEAM
        assert decision.impact is Impact.HIGH
        assert decision.status is DecisionStatus.ACCEPTED

    def test_an_unclassified_answer_takes_the_cautious_default(self, spans, document):
        # A decision the model would not place is recorded as team scope, medium
        # impact and merely proposed -- the reading that claims least.
        blank = answer(scope=None, impact=None, status=None)
        decision = structure(Answering([blank]), spans, document).decision.decision
        assert decision.scope is DecisionScope.TEAM
        assert decision.impact is Impact.MEDIUM
        assert decision.status is DecisionStatus.PROPOSED

    def test_a_quotation_that_only_wrapped_is_accepted_and_said_to_have(self, spans, document):
        result = structure(Answering([answer(evidence_quote=WRAPPED_QUOTE)]), spans, document)
        assert result.ok
        assert result.decision.quote_match is ClaimMatch.WHITESPACE

    def test_an_exact_quotation_is_recorded_as_exact(self, spans, document):
        result = structure(Answering([answer()]), spans, document)
        assert result.decision.quote_match is ClaimMatch.EXACT
        assert result.decision.quote == EXACT_QUOTE

    def test_one_candidate_costs_one_call(self, spans, document):
        assert structure(Answering([answer()]), spans, document).calls == 1


class TestTheRationaleIsOnTheEdge:
    def test_the_reasoning_is_carried_by_the_justification_not_the_record(self, spans, document):
        # The design decision this agent's docstring argues for: a rationale is
        # not a property of a decision, it is why the decision points at that
        # evidence. `Decision` has no field for it and this does not add one.
        result = structure(Answering([answer()]), spans, document)
        assert not hasattr(result.decision.decision, "rationale")
        assert result.decision.justification.rationale == (
            "Ranking quality on our own queries was not close."
        )

    def test_the_edge_runs_from_the_decision_to_the_span_it_was_read_from(self, spans, document):
        result = structure(Answering([answer()]), spans, document)
        link = result.decision.justification
        assert link.link_type is LinkType.JUSTIFIED_BY
        assert link.source_id == DECISION_ID
        assert link.target_id == spans[DECISION_INDEX].id
        assert link.span_id == spans[DECISION_INDEX].id

    def test_the_edge_carries_the_agent_and_the_answers_confidence(self, spans, document):
        result = structure(Answering([answer()]), spans, document)
        assert result.decision.justification.created_by == STRUCTURER_NAME
        assert result.decision.justification.confidence == 0.8

    def test_a_missing_rationale_says_where_the_decision_was_read_from(self, spans, document):
        # An empty rationale is not storable on an edge, and inventing one would
        # be inventing evidence. Naming the span is neither.
        result = structure(Answering([answer(rationale=None)]), spans, document)
        assert result.decision.justification.rationale == f"read from {spans[DECISION_INDEX].id}"


class TestAlternatives:
    def test_the_alternatives_are_kept_with_the_reason_each_lost(self, spans, document):
        result = structure(Answering([answer()]), spans, document)
        (rejected,) = result.decision.decision.rejected
        assert rejected.option == "Postgres full-text search"
        assert rejected.reason == "ranking quality lost"

    def test_a_decision_with_no_alternatives_says_so_rather_than_inventing_one(
        self, spans, document
    ):
        result = structure(Answering([answer(rejected=[])]), spans, document)
        (rejected,) = result.decision.decision.rejected
        assert rejected.reason == NO_ALTERNATIVE_RECORDED

    def test_an_empty_alternative_is_dropped_rather_than_stored_blank(self, spans, document):
        # `RejectedOption` would refuse a blank field anyway; dropping it here
        # means one useless entry does not cost the whole extraction.
        spoilt = [{"option": "   ", "reason": ""}, {"option": "a vendor", "reason": "price"}]
        result = structure(Answering([answer(rejected=spoilt)]), spans, document)
        assert [entry.option for entry in result.decision.decision.rejected] == ["a vendor"]

    def test_alternatives_that_are_all_blank_fall_back_to_the_honest_entry(self, spans, document):
        spoilt = [{"option": " ", "reason": " "}]
        result = structure(Answering([answer(rejected=spoilt)]), spans, document)
        (rejected,) = result.decision.decision.rejected
        assert rejected.reason == NO_ALTERNATIVE_RECORDED


class TestDates:
    def test_a_stated_date_becomes_midnight_utc_and_is_said_to_be_stated(self, spans, document):
        result = structure(Answering([answer()]), spans, document)
        assert result.decision.decision.decided_at == datetime(2026, 1, 5, tzinfo=UTC)
        assert result.decision.date_was_stated

    def test_no_stated_date_falls_back_to_ingestion_and_says_that_it_did(self, spans, document):
        # The weaker claim, made honestly: the earliest instant anyone can prove
        # the decision existed by. `date_was_stated` is what stops a reader
        # taking the fallback for something the document said.
        result = structure(Answering([answer(decided_on=None)]), spans, document)
        assert result.decision.decision.decided_at == document.ingested_at
        assert not result.decision.date_was_stated

    def test_the_fallback_is_still_timezone_aware(self, spans, document):
        # Invariant 5. An expiry is a comparison against wall-clock time.
        result = structure(Answering([answer(decided_on=None)]), spans, document)
        assert result.decision.decision.decided_at.tzinfo is not None


class TestCitationsItRefuses:
    def test_an_ordinal_that_was_never_offered_is_refused(self, spans, document):
        result = structure(Answering([answer(evidence_ordinal=99)]), spans, document)
        assert not result.ok
        assert result.rejection.refusal is Refusal.UNOFFERED_SPAN
        assert result.rejection.ordinal == 99

    def test_a_decision_found_and_never_cited_is_refused(self, spans, document):
        result = structure(Answering([answer(evidence_ordinal=None)]), spans, document)
        assert result.rejection.refusal is Refusal.UNOFFERED_SPAN
        assert result.rejection.ordinal is None

    def test_a_quotation_from_another_offered_passage_is_a_mis_attribution(self, spans, document):
        # ADR 0015's distinction. The model read the material and pointed at the
        # wrong part of it, which is a different defect from inventing one.
        result = structure(Answering([answer(evidence_quote=NEIGHBOUR_QUOTE)]), spans, document)
        assert result.rejection.refusal is Refusal.MIS_ATTRIBUTED_QUOTE

    def test_a_quotation_from_nowhere_is_a_fabrication(self, spans, document):
        result = structure(Answering([answer(evidence_quote=INVENTED_QUOTE)]), spans, document)
        assert result.rejection.refusal is Refusal.FABRICATED_QUOTE

    def test_a_decision_found_and_never_quoted_is_a_fabrication(self, spans, document):
        result = structure(Answering([answer(evidence_quote=None)]), spans, document)
        assert result.rejection.refusal is Refusal.FABRICATED_QUOTE
        assert "quotes nothing" in result.rejection.detail

    def test_a_span_that_no_longer_resolves_is_not_charged_to_the_model(self, spans, document):
        # A different failure with a different cause: the citation is fine and
        # the document moved. Reporting it as a fabrication would read as a
        # hallucinating model in the eval table when it is a re-ingested corpus.
        rewritten = make_document(text=ADR_BODY.replace("OpenSearch", "Solr"))
        result = structure(Answering([answer()]), spans, rewritten)
        assert result.rejection.refusal is Refusal.SPAN_DOES_NOT_RESOLVE
        assert result.decision is None

    def test_a_refused_citation_produces_no_decision_at_all(self, spans, document):
        # Not a decision with a warning attached. An object that exists is an
        # object something can write.
        result = structure(Answering([answer(evidence_quote=INVENTED_QUOTE)]), spans, document)
        assert result.decision is None


class TestAnswersThatAreNotRecords:
    @pytest.mark.parametrize("missing", ["title", "chosen", "decision_maker"])
    def test_a_well_cited_answer_that_is_not_a_decision_is_refused(self, spans, document, missing):
        result = structure(Answering([answer(**{missing: None})]), spans, document)
        assert result.rejection.refusal is Refusal.INCOHERENT_RECORD
        assert "problems" in result.rejection.detail

    def test_the_record_models_are_the_authority_not_this_agent(self, spans, document):
        # The agent does not re-implement the rules; it catches the refusal and
        # names it. What it quotes back is the model's own complaint.
        result = structure(Answering([answer(title="")]), spans, document)
        assert result.rejection.refusal is Refusal.INCOHERENT_RECORD


class TestAnswersThatFoundNothing:
    def test_found_false_is_a_real_answer_and_is_counted(self, spans, document):
        result = structure(Answering([answer(found=False)]), spans, document)
        assert result.rejection.refusal is Refusal.EMPTY_ANSWER
        assert result.calls == 1

    def test_found_false_needs_no_citation(self, spans, document):
        # The whole point of offering the model this exit: it costs one call and
        # produces no fabricated quotation, which a forced answer would.
        empty = answer(found=False, evidence_ordinal=None, evidence_quote=None, title=None)
        result = structure(Answering([empty]), spans, document)
        assert result.rejection.refusal is Refusal.EMPTY_ANSWER


class TestBadAnswersAboutOneCandidate:
    def test_a_refusal_is_recorded_rather_than_ending_the_run(self, spans, document):
        result = structure(Refusing([]), spans, document)
        assert result.rejection.refusal is Refusal.NO_USABLE_ANSWER
        assert result.decision is None

    def test_an_unrepairable_answer_is_recorded_as_the_attempts_it_took(self, spans, document):
        provider = Answering(["not json", "still not json", "no"])
        result = structure(provider, spans, document)
        assert result.rejection.refusal is Refusal.NO_USABLE_ANSWER
        assert result.calls == 3

    def test_a_repaired_answer_still_produces_a_decision(self, spans, document):
        result = structure(Answering(["not json", answer()]), spans, document)
        assert result.ok
        assert result.calls == 2


class TestTracing:
    def test_every_attempt_is_traced(self, spans, document):
        provider = Answering(["not json", answer()])
        structure(provider, spans, document)
        assert len(provider.sink.traces) == 2

    def test_the_trace_says_which_document_and_which_candidate(self, spans, document):
        provider = Answering([answer()])
        structure(provider, spans, document)
        metadata = provider.requests[0].metadata
        assert metadata["doc_id"] == document.id
        assert metadata["candidate_ordinal"] == str(CANDIDATE_ORDINAL)


class TestOffline:
    def test_the_offline_provider_cites_and_quotes_independently(self, spans, document):
        # Worth pinning, because it is why offline runs extract almost nothing.
        # `praxis.llm.synthesis` draws the ordinal from the bracketed labels and
        # the quotation from the passages separately, so the two agree only by
        # chance -- which is exactly what keeps `VerifierAgent` a gate offline
        # rather than a formality it always passes.
        provider = MockProvider(sink=MemoryTraceSink(), settings=Settings())
        result = structure(provider, spans, document)
        assert result.calls == 1
        assert result.ok or result.rejection.refusal in {
            Refusal.EMPTY_ANSWER,
            Refusal.MIS_ATTRIBUTED_QUOTE,
            Refusal.FABRICATED_QUOTE,
        }

    def test_two_offline_runs_over_one_candidate_agree(self, spans, document):
        # The determinism the whole ablation table rests on.
        def run() -> StructureResult:
            provider = MockProvider(sink=MemoryTraceSink(), settings=Settings())
            return structure(provider, spans, document)

        assert run() == run()


def test_the_answer_model_asks_for_an_ordinal_and_never_a_span_id():
    """ADR 0015 at the schema. A fabricated citation target is unspellable."""
    fields = DecisionAnswer.model_fields
    assert "evidence_ordinal" in fields
    assert not any(name.endswith("span_id") for name in fields)
