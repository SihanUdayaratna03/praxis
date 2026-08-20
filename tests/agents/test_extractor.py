"""AssumptionExtractor, against answers built to be wrong in specific ways.

Two things are being tested and they are worth naming apart. One is the same
contract every Half A agent has: cite what you were shown, refuse precisely,
degrade about one document rather than about the run. The other is the fusion
judgement -- an assumption that is really an estimate -- which is the
relationship the whole product exists to find, and which nothing before this
agent could produce.

The fixture ADR states its assumptions under one heading and its effort under
another, three passages apart. That is not incidental to these tests: it is why
the estimate carries its own citation, and every test about the estimate's
`span_id` is asserting that the two really are different passages.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from praxis.agents.errors import ExtractionError, Refusal
from praxis.agents.extractor import (
    DEFAULT_AHEAD,
    DEFAULT_BEHIND,
    EXTRACT_TASK,
    EXTRACTOR_NAME,
    NOT_STATED,
    UNCLASSIFIED,
    AssumptionExtractor,
    ExtractionResult,
)
from praxis.config.models import ModelRole, role_for_agent
from praxis.config.settings import Settings
from praxis.domain.enums import (
    AssumptionStatus,
    DecisionScope,
    DecisionStatus,
    Impact,
    RecordKind,
    Unit,
)
from praxis.domain.ids import DecisionId, format_sequential_id
from praxis.domain.links import LinkType
from praxis.domain.records import Decision, RejectedOption
from praxis.llm.mock import MockProvider
from praxis.llm.trace import MemoryTraceSink
from praxis.prompts.library import load

from tests.agents.conftest import ADR_BODY, AT, Answering, Refusing, make_document, spans_of

DECISION_INDEX = 3
"""Which span of the fixture ADR states the decision."""

WORLD_ORDINAL = 8
"""Where the assumption about the world lands in the offering. The window opens
at span 0 because the decision is near the start, so ordinals and document
positions coincide here -- asserted, not assumed."""

EFFORT_ORDINAL = 9
ESTIMATE_ORDINAL = 11

DECIDED_AT = datetime(2026, 1, 5, tzinfo=UTC)

WORLD_QUOTE = "This rests on the assumption that the index stays under 50 GB"
EFFORT_QUOTE = "It also rests on the work finishing inside 4 weeks."
ESTIMATE_QUOTE = "Nadeesha put this at 4 weeks of hands-on work"
INVENTED_QUOTE = "This rests on the assumption that Colombo stays under 50 degrees."


def a_decision(spans, index: int = DECISION_INDEX) -> Decision:
    """What the structurer would have handed over, citing a real span."""
    return Decision(
        id=DecisionId("D-0001"),
        title="OpenSearch for the product index",
        chosen="OpenSearch on managed nodes",
        rejected=(RejectedOption(option="Postgres full-text search", reason="ranking lost"),),
        decision_maker="Nadeesha",
        decided_at=DECIDED_AT,
        scope=DecisionScope.TEAM,
        impact=Impact.HIGH,
        status=DecisionStatus.ACCEPTED,
        span_id=spans[index].id,
        confidence=0.8,
        created_by="DecisionStructurer",
        created_at=AT,
    )


def counting_allocator():
    """Ids from a counter, which is what the pipeline will pass.

    `Repository.next_id` reads the store's highest ordinal, so asking it twice
    before writing anything returns one id twice. Every caller of this agent
    has to count, so the tests count too.
    """
    seen: Counter[RecordKind] = Counter()

    def allocate(kind: RecordKind) -> str:
        seen[kind] += 1
        return format_sequential_id(kind, seen[kind])

    return allocate


def an_assumption(**overrides: object) -> dict[str, object]:
    """The world assumption, as the model would report it, spoilt to order."""
    return {
        "statement": "the index stays under 50 GB for the next year",
        "predicate": "index_size_gb <= 50",
        "expiry_condition": "when(indexed_documents >= 10000000)",
        "confidence": 0.7,
        "evidence_ordinal": WORLD_ORDINAL,
        "evidence_quote": WORLD_QUOTE,
        "quantified": False,
        "estimate_subject": None,
        "estimate_owner": None,
        "estimate_work_class": None,
        "estimate_active_quantity": None,
        "estimate_blocked_quantity": None,
        "estimate_unit": None,
        "estimate_ordinal": None,
        "estimate_quote": None,
    } | overrides


def an_estimate_in_disguise(**overrides: object) -> dict[str, object]:
    """The effort assumption: quantified, forward-looking, cited twice."""
    return an_assumption(
        **{
            "statement": "the work finishes inside 4 weeks",
            "predicate": "search_index_weeks <= 4",
            "expiry_condition": 'on_event("the work ships")',
            "evidence_ordinal": EFFORT_ORDINAL,
            "evidence_quote": EFFORT_QUOTE,
            "quantified": True,
            "estimate_subject": "the search index work",
            "estimate_owner": "Nadeesha",
            "estimate_work_class": "search-infrastructure",
            "estimate_active_quantity": 4,
            "estimate_unit": "weeks",
            "estimate_ordinal": ESTIMATE_ORDINAL,
            "estimate_quote": ESTIMATE_QUOTE,
        }
        | overrides
    )


def answers(*items: dict[str, object]) -> str:
    """Render an answer the way the model would."""
    return json.dumps({"assumptions": list(items)})


def extract(provider, spans, document, *, decision=None, **kwargs) -> ExtractionResult:
    """Run one decision's assumptions through the agent."""
    agent = AssumptionExtractor(provider, **kwargs)
    return agent.extract(
        decision if decision is not None else a_decision(spans),
        spans,
        document,
        allocate=counting_allocator(),
        at=AT,
    )


class TestRouting:
    def test_the_extractor_is_the_one_half_a_agent_that_reasons(self):
        # ADR 0006's routing table, and the tier is the argument: locating a
        # phrase is an extraction, deciding a sentence is a claim a decision
        # depends on is not.
        assert role_for_agent(EXTRACTOR_NAME) is ModelRole.REASON

    def test_the_agent_names_itself_and_never_a_model(self, spans, document):
        provider = Answering([answers(an_assumption())])
        extract(provider, spans, document)
        assert all(request.agent == EXTRACTOR_NAME for request in provider.requests)


class TestPromptProvenance:
    def test_every_call_names_the_prompt_version_it_read(self, spans, document):
        provider = Answering([answers()])
        extract(provider, spans, document)
        prompt = load(EXTRACT_TASK)
        assert provider.requests[0].prompt_id == prompt.id
        assert provider.requests[0].prompt_sha == prompt.sha256

    def test_the_task_and_the_prompt_name_agree(self):
        assert load(EXTRACT_TASK).name == EXTRACT_TASK

    def test_the_prompt_carries_the_decision_and_where_it_was_stated(self, spans, document):
        provider = Answering([answers()])
        extract(provider, spans, document)
        system = provider.requests[0].system
        assert "OpenSearch on managed nodes" in system
        assert f"passage {DECISION_INDEX}" in system


class TestTheWindow:
    def test_the_window_reaches_further_forward_than_back(self, spans, document):
        # Not a preference: both corpus shapes state the assumptions after the
        # decision. Reaching equally would spend half the listing on the title.
        assert DEFAULT_AHEAD > DEFAULT_BEHIND
        provider = Answering([answers()])
        extract(provider, spans, document)
        shown = provider.requests[0].messages[0].content
        assert f"[{ESTIMATE_ORDINAL}] " in shown

    def test_the_listing_stays_within_what_adr_0015_assumed(self, spans, document):
        # `spans_per_offering <= 12`. Widening either distance breaches a
        # recorded assumption rather than changing a constant.
        assert DEFAULT_BEHIND + DEFAULT_AHEAD + 1 <= 12
        provider = Answering([answers()])
        extract(provider, spans, document)
        assert provider.requests[0].messages[0].content.count("\n\n[") + 1 <= 12

    def test_the_effort_section_is_offered_at_all(self, spans, document):
        # The window's whole purpose. A quantity three passages past the
        # assumption is either shown or the fusion edge cannot exist.
        provider = Answering([answers()])
        extract(provider, spans, document)
        assert ESTIMATE_QUOTE in provider.requests[0].messages[0].content

    @pytest.mark.parametrize(("behind", "ahead"), [(-1, 4), (4, -1)])
    def test_a_distance_cannot_be_negative(self, behind, ahead):
        with pytest.raises(ValueError, match="cannot be negative"):
            AssumptionExtractor(Answering([]), behind=behind, ahead=ahead)

    def test_a_decision_citing_another_document_is_a_bug_not_an_answer(self, spans, document):
        other = spans_of(make_document(doc_id="DOC-0002"))
        agent = AssumptionExtractor(Answering([]))
        with pytest.raises(ExtractionError, match="not one of"):
            agent.extract(a_decision(other), spans, document, allocate=counting_allocator(), at=AT)


class TestAnExtractedAssumption:
    def test_a_well_cited_assumption_becomes_a_record(self, spans, document):
        result = extract(Answering([answers(an_assumption())]), spans, document)
        (extracted,) = result.assumptions
        assumption = extracted.assumption
        assert assumption.statement == "the index stays under 50 GB for the next year"
        assert assumption.predicate == "index_size_gb <= 50"
        assert assumption.expiry_condition == "when(indexed_documents >= 10000000)"
        assert assumption.confidence == 0.7

    def test_the_assumption_cites_the_span_the_quotation_is_in(self, spans, document):
        result = extract(Answering([answers(an_assumption())]), spans, document)
        assert result.assumptions[0].assumption.span_id == spans[WORLD_ORDINAL].id

    def test_a_freshly_extracted_assumption_has_not_been_evaluated(self, spans, document):
        # `AssumptionMonitor` sets both fields together or neither; an extractor
        # that pre-filled a status would be asserting a verdict nobody reached.
        assumption = (
            extract(Answering([answers(an_assumption())]), spans, document)
            .assumptions[0]
            .assumption
        )
        assert assumption.status is AssumptionStatus.UNVERIFIED
        assert assumption.last_evaluated_at is None

    def test_the_agent_signs_what_it_made(self, spans, document):
        assumption = (
            extract(Answering([answers(an_assumption())]), spans, document)
            .assumptions[0]
            .assumption
        )
        assert assumption.created_by == EXTRACTOR_NAME
        assert assumption.created_at == AT

    def test_several_assumptions_come_back_in_the_order_they_were_reported(self, spans, document):
        reply = answers(an_assumption(), an_estimate_in_disguise())
        result = extract(Answering([reply]), spans, document)
        assert [e.assumption.span_id for e in result.assumptions] == [
            spans[WORLD_ORDINAL].id,
            spans[EFFORT_ORDINAL].id,
        ]

    def test_an_empty_list_is_a_real_answer(self, spans, document):
        # Most passages state no assumption, and an invented one is worse than
        # a missing one: it carries a predicate that will expire and demand
        # attention for a claim nobody made.
        result = extract(Answering([answers()]), spans, document)
        assert result.assumptions == ()
        assert result.rejections == ()
        assert not result.blind


class TestTheEdges:
    def test_the_decision_is_joined_to_what_it_rests_on(self, spans, document):
        # Without this edge a breached assumption reaches nothing, which is the
        # entire mechanism.
        result = extract(Answering([answers(an_assumption())]), spans, document)
        (extracted,) = result.assumptions
        assert extracted.assumes.link_type is LinkType.ASSUMES
        assert extracted.assumes.source_id == "D-0001"
        assert extracted.assumes.target_id == extracted.assumption.id

    def test_the_assumption_is_joined_to_the_passage_it_was_read_from(self, spans, document):
        result = extract(Answering([answers(an_assumption())]), spans, document)
        (extracted,) = result.assumptions
        assert extracted.justification.link_type is LinkType.JUSTIFIED_BY
        assert extracted.justification.source_id == extracted.assumption.id
        assert extracted.justification.target_id == spans[WORLD_ORDINAL].id

    def test_both_edges_carry_the_agent_and_the_answers_confidence(self, spans, document):
        (extracted,) = extract(Answering([answers(an_assumption())]), spans, document).assumptions
        for link in (extracted.assumes, extracted.justification):
            assert link.created_by == EXTRACTOR_NAME
            assert link.confidence == 0.7


class TestTheFusionEdge:
    def test_a_quantified_forward_looking_claim_also_becomes_an_estimate(self, spans, document):
        result = extract(Answering([answers(an_estimate_in_disguise())]), spans, document)
        (extracted,) = result.assumptions
        assert extracted.estimate is not None
        assert extracted.estimate.estimate.active_quantity == Decimal(4)
        assert extracted.estimate.estimate.unit is Unit.WEEKS
        assert extracted.estimate.estimate.owner == "Nadeesha"

    def test_the_two_are_joined_by_the_edge_the_product_exists_to_find(self, spans, document):
        (extracted,) = extract(
            Answering([answers(an_estimate_in_disguise())]), spans, document
        ).assumptions
        edge = extracted.estimate.estimated_as
        assert edge.link_type is LinkType.ESTIMATED_AS
        assert edge.source_id == extracted.assumption.id
        assert edge.target_id == extracted.estimate.estimate.id

    def test_the_estimate_cites_the_passage_its_quantity_is_stated_in(self, spans, document):
        # Three passages from the assumption's. An estimate inheriting the
        # assumption's span would be a citation nobody checked.
        (extracted,) = extract(
            Answering([answers(an_estimate_in_disguise())]), spans, document
        ).assumptions
        assert extracted.estimate.estimate.span_id == spans[ESTIMATE_ORDINAL].id
        assert extracted.estimate.estimate.span_id != extracted.assumption.span_id

    def test_the_estimate_carries_the_assumption_as_its_condition(self, spans, document):
        # What `FusionBridge` reads later when deciding the two are one claim.
        (extracted,) = extract(
            Answering([answers(an_estimate_in_disguise())]), spans, document
        ).assumptions
        assert extracted.estimate.estimate.conditions == (extracted.assumption.statement,)

    def test_the_estimate_was_made_when_the_decision_was(self, spans, document):
        # Not when the extraction ran. A prediction's date is what calibration
        # orders history by, and the run's clock says nothing about the corpus.
        (extracted,) = extract(
            Answering([answers(an_estimate_in_disguise())]), spans, document
        ).assumptions
        assert extracted.estimate.estimate.estimated_at == DECIDED_AT

    def test_an_assumption_about_the_world_produces_no_estimate_and_no_rejection(
        self, spans, document
    ):
        # Most assumptions are not estimates. Counting them as losses would make
        # the fusion recall meaningless.
        result = extract(Answering([answers(an_assumption())]), spans, document)
        assert result.assumptions[0].estimate is None
        assert result.rejections == ()
        assert result.estimates == ()

    def test_the_estimates_are_reachable_without_walking_the_assumptions(self, spans, document):
        reply = answers(an_assumption(), an_estimate_in_disguise())
        result = extract(Answering([reply]), spans, document)
        assert len(result.estimates) == 1


class TestWorkClass:
    def test_a_class_the_document_did_not_make_clear_is_said_to_be_unclassified(
        self, spans, document
    ):
        reply = answers(an_estimate_in_disguise(estimate_work_class=None))
        (extracted,) = extract(Answering([reply]), spans, document).assumptions
        assert extracted.estimate.estimate.work_class == UNCLASSIFIED

    def test_only_spelling_is_repaired(self, spans, document):
        # `Data Migration` and `data migration` are one class written by two
        # models. Letting both through halves a sample `BiasDetective` already
        # refuses to answer about below n = 5.
        reply = answers(an_estimate_in_disguise(estimate_work_class="Data Migration"))
        (extracted,) = extract(Answering([reply]), spans, document).assumptions
        assert extracted.estimate.estimate.work_class == "data-migration"

    @pytest.mark.parametrize("stated", ["", "   ", "!!", "a/b", "3.5 weeks of work?"])
    def test_anything_that_is_not_a_run_of_words_is_not_repaired_into_one(
        self, spans, document, stated
    ):
        reply = answers(an_estimate_in_disguise(estimate_work_class=stated))
        (extracted,) = extract(Answering([reply]), spans, document).assumptions
        assert extracted.estimate.estimate.work_class == UNCLASSIFIED

    def test_an_owner_the_document_did_not_name_is_said_to_be_unnamed(self, spans, document):
        # Calibration is per estimator, so an invented name would put one
        # person's miss in another person's history.
        reply = answers(an_estimate_in_disguise(estimate_owner=None))
        (extracted,) = extract(Answering([reply]), spans, document).assumptions
        assert extracted.estimate.estimate.owner == NOT_STATED


class TestAMalformedEstimateDoesNotCostTheAssumption:
    def test_a_quantity_with_no_unit_is_refused_and_the_assumption_survives(self, spans, document):
        # No default. Four weeks silently recorded as four hours would not fail
        # anywhere; it would make one estimator look forty times worse.
        reply = answers(an_estimate_in_disguise(estimate_unit=None))
        result = extract(Answering([reply]), spans, document)
        assert len(result.assumptions) == 1
        assert result.assumptions[0].estimate is None
        (rejection,) = result.rejections
        assert rejection.refusal is Refusal.INCOHERENT_RECORD
        assert rejection.lost is RecordKind.ESTIMATE

    def test_an_estimate_of_nothing_is_refused(self, spans, document):
        reply = answers(an_estimate_in_disguise(estimate_active_quantity=None))
        result = extract(Answering([reply]), spans, document)
        assert result.assumptions[0].estimate is None
        assert result.rejections[0].lost is RecordKind.ESTIMATE

    def test_a_fabricated_quantity_citation_is_refused_on_its_own(self, spans, document):
        reply = answers(an_estimate_in_disguise(estimate_quote=INVENTED_QUOTE))
        result = extract(Answering([reply]), spans, document)
        assert len(result.assumptions) == 1
        assert result.assumptions[0].estimate is None
        (rejection,) = result.rejections
        assert rejection.refusal is Refusal.FABRICATED_QUOTE
        assert rejection.lost is RecordKind.ESTIMATE

    def test_a_quantity_cited_to_an_unoffered_passage_is_refused_on_its_own(self, spans, document):
        reply = answers(an_estimate_in_disguise(estimate_ordinal=99))
        result = extract(Answering([reply]), spans, document)
        assert len(result.assumptions) == 1
        (rejection,) = result.rejections
        assert rejection.refusal is Refusal.UNOFFERED_SPAN
        assert rejection.ordinal == 99


class TestCitationsItRefuses:
    def test_an_unoffered_ordinal_costs_the_assumption(self, spans, document):
        result = extract(Answering([answers(an_assumption(evidence_ordinal=99))]), spans, document)
        assert result.assumptions == ()
        (rejection,) = result.rejections
        assert rejection.refusal is Refusal.UNOFFERED_SPAN
        assert rejection.lost is RecordKind.ASSUMPTION

    def test_a_fabricated_quotation_costs_the_assumption(self, spans, document):
        reply = answers(an_assumption(evidence_quote=INVENTED_QUOTE))
        result = extract(Answering([reply]), spans, document)
        assert result.assumptions == ()
        assert result.rejections[0].refusal is Refusal.FABRICATED_QUOTE

    def test_a_quotation_from_another_offered_passage_is_a_mis_attribution(self, spans, document):
        reply = answers(an_assumption(evidence_quote=ESTIMATE_QUOTE))
        result = extract(Answering([reply]), spans, document)
        assert result.rejections[0].refusal is Refusal.MIS_ATTRIBUTED_QUOTE

    @pytest.mark.parametrize("missing", ["statement", "predicate", "expiry_condition"])
    def test_a_well_cited_answer_that_is_not_an_assumption_is_refused(
        self, spans, document, missing
    ):
        # A predicate is what `AssumptionMonitor` evaluates and an expiry is
        # when it runs. An assumption without either is prose.
        result = extract(Answering([answers(an_assumption(**{missing: None}))]), spans, document)
        assert result.assumptions == ()
        assert result.rejections[0].refusal is Refusal.INCOHERENT_RECORD

    def test_one_bad_assumption_does_not_cost_the_others(self, spans, document):
        reply = answers(an_assumption(evidence_ordinal=99), an_estimate_in_disguise())
        result = extract(Answering([reply]), spans, document)
        assert len(result.assumptions) == 1
        assert len(result.rejections) == 1


class TestIds:
    def test_every_record_gets_its_own_id_from_the_caller(self, spans, document):
        reply = answers(an_estimate_in_disguise(), an_assumption())
        result = extract(Answering([reply]), spans, document)
        assert [e.assumption.id for e in result.assumptions] == ["A-0001", "A-0002"]
        assert [e.estimate.id for e in result.estimates] == ["EST-0001"]

    def test_the_agent_never_holds_a_repository(self, spans, document):
        # The seam: an allocator it calls, and a clock it was handed. Anything
        # more would make an agent able to write.
        agent = AssumptionExtractor(Answering([answers()]))
        assert not any("repo" in name.lower() for name in vars(agent))


class TestBadAnswersAboutOneDecision:
    def test_a_refusal_leaves_the_decision_blind_rather_than_ending_the_run(self, spans, document):
        result = extract(Refusing([]), spans, document)
        assert result.blind
        assert result.assumptions == ()
        assert result.rejections[0].refusal is Refusal.NO_USABLE_ANSWER

    def test_an_unrepairable_answer_is_counted_as_the_attempts_it_took(self, spans, document):
        result = extract(Answering(["not json", "still not", "no"]), spans, document)
        assert result.blind
        assert result.calls == 3

    def test_a_repaired_answer_still_produces_assumptions(self, spans, document):
        result = extract(Answering(["not json", answers(an_assumption())]), spans, document)
        assert len(result.assumptions) == 1
        assert result.calls == 2
        assert not result.blind


class TestTracing:
    def test_every_attempt_is_traced(self, spans, document):
        provider = Answering(["not json", answers(an_assumption())])
        extract(provider, spans, document)
        assert len(provider.sink.traces) == 2

    def test_the_trace_says_which_document_and_which_decision(self, spans, document):
        provider = Answering([answers()])
        extract(provider, spans, document)
        metadata = provider.requests[0].metadata
        assert metadata["doc_id"] == document.id
        assert metadata["decision_id"] == "D-0001"


class TestOffline:
    def test_two_offline_runs_over_one_decision_agree(self, spans, document):
        # The determinism the whole ablation table rests on.
        def run() -> ExtractionResult:
            provider = MockProvider(sink=MemoryTraceSink(), settings=Settings())
            return extract(provider, spans, document)

        assert run() == run()

    def test_the_offline_run_makes_one_call_and_survives_whatever_it_says(self, spans, document):
        # `praxis.llm.synthesis` fills every field independently, so an offline
        # answer is a well-formed reply about nothing in particular. What is
        # being asserted is that the agent handles it without raising -- the
        # numbers it produces are about the plumbing, and ADR 0016 says so.
        provider = MockProvider(sink=MemoryTraceSink(), settings=Settings())
        result = extract(provider, spans, document)
        assert result.calls == 1
        assert len(result.assumptions) + len(result.rejections) >= 1


def test_a_moved_document_is_not_charged_to_the_model(spans):
    """The shared gate's rule, reaching this agent for free."""
    rewritten = make_document(text=ADR_BODY.replace("OpenSearch", "Solr"))
    result = extract(Answering([answers(an_assumption())]), spans, rewritten)
    assert result.rejections[0].refusal is Refusal.SPAN_DOES_NOT_RESOLVE
