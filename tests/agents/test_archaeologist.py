"""ArchaeologistAgent: what it will say, and what it refuses to say.

The class that matters is `TestTheStoreSpeaks`. This agent's whole value is that
the answer is what the organisation actually recorded, produced years later when
nobody remembers -- so an answer that reads well and is not in the record would
be worse than no answer, because it arrives with a date and a name on it and
will be believed.

Everything else follows from that one property. The model is asked for two
*references* and never for prose; a rejected option it names is checked against
the decision's own tuple; and a question the record does not answer comes back
refused rather than filled in.

The store is real and in memory, because for this agent the store is not where
the answer goes -- it is the question's subject matter, and a double would be
testing a different agent.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta

import pytest
from praxis.agents.archaeologist import (
    ArchaeologistAgent,
    Excavation,
    Unanswered,
)
from praxis.agents.errors import Refusal
from praxis.domain.enums import AssumptionStatus
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Decision, Link, RejectedOption, Span
from praxis.ingest.adapters import MARKDOWN_ADAPTER, document_from
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

from tests.agents.conftest import Answering, Refusing
from tests.monitor.conftest import ACTOR, AT, BODY, make_assumption

QUESTION = "Why not Postgres full-text search for the product search index?"

REJECTED = (
    RejectedOption(
        option="Postgres full-text search",
        reason="ranking quality was not close on our own queries",
    ),
    RejectedOption(
        option="a hosted search vendor",
        reason="the per-query price does not survive our traffic growth",
    ),
)


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


@pytest.fixture
def span(store: Repository) -> Span:
    """A real document and a span over it, both written."""
    source = MARKDOWN_ADAPTER.normalise(BODY.encode("utf-8"), source_uri="adr.md")
    document = store.add(
        document_from(source, doc_id="DOC-0001", ingested_at=AT), actor=ACTOR, reason="fixture"
    )
    return store.add(
        Span.covering(
            document, 0, len(document.content.encode("utf-8")), created_by=ACTOR, created_at=AT
        ),
        actor=ACTOR,
        reason="fixture",
    )


@pytest.fixture
def decision(store: Repository, span: Span) -> Decision:
    """The decision the fixture question is about."""
    return store.add(
        Decision(
            id="D-0001",
            title="the product search index",
            chosen="OpenSearch on managed nodes",
            rejected=REJECTED,
            decision_maker="Nadeesha",
            decided_at=AT,
            scope="team",
            impact="medium",
            span_id=span.id,
            confidence=0.9,
            created_by=ACTOR,
            created_at=AT,
        ),
        actor=ACTOR,
        reason="fixture",
    )


def rest_on(store: Repository, decision: Decision, assumption: Assumption) -> Assumption:
    """Write an assumption and the `assumes` edge from the decision to it."""
    written = store.add(assumption, actor=ACTOR, reason="fixture")
    store.add(
        Link.between(
            LinkType.ASSUMES,
            decision.id,
            written.id,
            rationale=written.statement,
            confidence=0.9,
            created_by=ACTOR,
            created_at=AT,
            span_id=written.span_id,
        ),
        actor=ACTOR,
        reason="fixture",
    )
    return written


def selection(
    ordinal: int | None = 0,
    option: str | None = "Postgres full-text search",
    *,
    answered: bool = True,
    confidence: float = 0.9,
    note: str | None = "the only recorded decision about the search index",
) -> str:
    """One selection answer as the structured layer would receive it."""
    return json.dumps(
        {
            "decision_ordinal": ordinal,
            "rejected_option": option,
            "answered": answered,
            "confidence": confidence,
            "note": note,
        }
    )


def ask(store: Repository, answer: str, question: str = QUESTION) -> Excavation | Unanswered:
    """Put one question, with a scripted selection."""
    return ArchaeologistAgent(Answering([answer])).ask(question, store)


def answered(store: Repository, answer: str = "", question: str = QUESTION) -> Excavation:
    """Put one question and insist it was answered."""
    result = ask(store, answer or selection(), question)
    assert isinstance(result, Excavation)
    return result


class TestTheStoreSpeaks:
    """The answer is assembled from records. Nothing a model wrote is in it."""

    def test_the_recorded_reason_is_the_answer(self, store, decision):
        result = answered(store)
        assert "ranking quality was not close on our own queries" in result.answer

    def test_the_answer_names_the_option_as_the_record_spells_it(self, store, decision):
        assert "Postgres full-text search" in answered(store).answer

    def test_the_answer_names_what_was_chosen_instead(self, store, decision):
        assert "OpenSearch on managed nodes" in answered(store).answer

    def test_the_answer_carries_the_decision_maker_and_the_date(self, store, decision):
        # It arrives with a date and a name on it, which is exactly why it must
        # not contain anything nobody recorded.
        result = answered(store)
        assert "Nadeesha" in result.answer
        assert AT.date().isoformat() in result.answer

    def test_the_models_note_never_reaches_the_answer(self, store, decision):
        # Kept beside it and never spliced in. The model was asked for two
        # references; anything else it wrote is not evidence about the past.
        invented = "The team had a bad experience with Postgres at a previous company."
        result = answered(store, selection(note=invented))
        assert result.note == invented
        assert invented not in result.answer

    def test_every_sentence_of_the_answer_is_a_stored_field(self, store, decision):
        result = answered(store)
        stored = {
            result.rejected.option,
            result.rejected.reason,
            result.decision.chosen,
            result.decision.decision_maker,
            result.decision.id,
        }
        assert all(any(value in line for value in stored) for line in result.answer.splitlines())


class TestWhatItRefusesToSay:
    def test_an_option_the_decision_never_rejected_is_refused(self, store, decision):
        # The citation gate's argument, applied to a different kind of citation.
        result = ask(store, selection(option="MongoDB Atlas Search"))
        assert isinstance(result, Unanswered)
        assert result.refusal is Refusal.FABRICATED_QUOTE
        assert "MongoDB Atlas Search" in result.detail

    def test_the_refusal_says_what_was_really_rejected(self, store, decision):
        result = ask(store, selection(option="MongoDB Atlas Search"))
        assert isinstance(result, Unanswered)
        assert "Postgres full-text search" in result.detail

    def test_claiming_an_answer_while_naming_no_option_is_refused(self, store, decision):
        # `answered` is the model's word for it; the option is the part that
        # has to be in the record, and there is nothing here to check.
        result = ask(store, selection(option=None))
        assert isinstance(result, Unanswered)
        assert result.refusal is Refusal.FABRICATED_QUOTE

    def test_a_near_miss_is_still_a_miss(self, store, decision):
        # A fuzzy match would let "Postgres" answer for a decision that rejected
        # "Postgres full-text search". Usually right, and the reason attached
        # would be presented as a quotation.
        result = ask(store, selection(option="Postgres"))
        assert isinstance(result, Unanswered)
        assert result.refusal is Refusal.FABRICATED_QUOTE

    def test_a_candidate_that_was_never_offered_is_refused(self, store, decision):
        result = ask(store, selection(ordinal=7))
        assert isinstance(result, Unanswered)
        assert result.refusal is Refusal.UNOFFERED_SPAN

    def test_no_candidate_at_all_is_refused(self, store, decision):
        result = ask(store, selection(ordinal=None))
        assert isinstance(result, Unanswered)
        assert result.refusal is Refusal.UNOFFERED_SPAN

    def test_a_model_saying_it_found_nothing_is_a_real_answer(self, store, decision):
        result = ask(store, selection(answered=False, option=None))
        assert isinstance(result, Unanswered)
        assert result.refusal is Refusal.EMPTY_ANSWER

    def test_an_empty_store_costs_no_model_call(self, store):
        result = ArchaeologistAgent(Answering([selection()])).ask(QUESTION, store)
        assert isinstance(result, Unanswered)
        assert result.refusal is Refusal.EMPTY_ANSWER
        assert result.calls == 0

    def test_a_question_with_no_usable_terms_searches_for_nothing(self, store, decision):
        result = ArchaeologistAgent(Answering([selection()])).ask("?? -- ??", store)
        assert isinstance(result, Unanswered)
        assert result.calls == 0

    def test_a_refused_call_is_reported_rather_than_raised(self, store, decision):
        result = ArchaeologistAgent(Refusing([])).ask(QUESTION, store)
        assert isinstance(result, Unanswered)
        assert result.refusal is Refusal.NO_USABLE_ANSWER
        assert result.calls == 1


class TestFindingTheDecision:
    def test_a_question_is_searched_as_quoted_terms(self, store, decision):
        # Repository.search passes its argument to FTS5 unchanged and owns none
        # of its syntax, so a bare question mark would be a malformed match
        # expression rather than a search.
        assert isinstance(answered(store, question="Why not Postgres?"), Excavation)

    def test_a_hyphenated_question_does_not_reach_fts5_as_an_operator(self, store, decision):
        assert isinstance(
            answered(store, question="Why not a full-text search in Postgres?"), Excavation
        )

    def test_an_assumption_hit_is_followed_back_to_its_decision(self, store, decision, span):
        # A question about a subject often matches the assumption's wording
        # rather than the decision's, and `assumes` is the record of which
        # decision rested on it.
        rest_on(
            store,
            decision,
            make_assumption(
                span,
                assumption_id="A-0001",
                statement="the marmalade index stays under 50 GB",
                predicate="marmalade_index_gb <= 50",
            ),
        )
        result = answered(store, question="why not marmalade")
        assert result.decision.id == "D-0001"

    def test_an_assumption_resting_under_no_decision_leads_nowhere(self, store, span):
        # A hit worth following that turns out to lead to nothing. Refused
        # rather than answered from the assumption alone: an assumption is not
        # a record of what was rejected or why.
        store.add(
            make_assumption(
                span,
                assumption_id="A-0001",
                statement="the marmalade index stays under 50 GB",
                predicate="marmalade_index_gb <= 50",
            ),
            actor=ACTOR,
            reason="fixture",
        )
        result = ArchaeologistAgent(Answering([selection()])).ask("why not marmalade", store)
        assert isinstance(result, Unanswered)
        assert result.calls == 0

    def test_a_question_matching_nothing_finds_no_candidate(self, store, decision):
        result = ArchaeologistAgent(Answering([selection()])).ask(
            "why not zeppelins for freight", store
        )
        assert isinstance(result, Unanswered)
        assert result.calls == 0

    def test_the_candidates_are_numbered_with_their_rejected_options(self, store, decision):
        provider = Answering([selection()])
        ArchaeologistAgent(provider).ask(QUESTION, store)
        listing = provider.requests[0].messages[0].content
        assert "[0] the product search index" in listing
        assert "- Postgres full-text search" in listing
        assert "chose: OpenSearch on managed nodes" in listing

    def test_the_question_is_shown_unchanged(self, store, decision):
        provider = Answering([selection()])
        ArchaeologistAgent(provider).ask(QUESTION, store)
        assert QUESTION in provider.requests[0].messages[0].content

    def test_the_call_names_the_prompt_behind_it(self, store, decision):
        provider = Answering([selection()])
        ArchaeologistAgent(provider).ask(QUESTION, store)
        assert provider.requests[0].prompt_id == "answer_why_not@v1"
        assert provider.requests[0].agent == "ArchaeologistAgent"


class TestWhatHasHappenedSince:
    """The part that makes an archaeologist worth having beside a monitor."""

    def test_the_assumptions_the_decision_rested_on_are_reported(self, store, decision, span):
        rest_on(store, decision, make_assumption(span, assumption_id="A-0001"))
        result = answered(store)
        assert [found.assumption.id for found in result.resting_on] == ["A-0001"]

    def test_each_assumption_carries_its_current_status(self, store, decision, span):
        rest_on(store, decision, make_assumption(span, assumption_id="A-0001"))
        assert result_status(answered(store)) == [AssumptionStatus.UNVERIFIED]

    def test_a_breached_assumption_is_called_out_in_the_answer(self, store, decision, span):
        # "We chose this because we assumed that, and that assumption broke in
        # June" is a different sentence from "we chose this".
        rest_on(store, decision, _breached(make_assumption(span, assumption_id="A-0001")))
        result = answered(store)
        assert "worth re-reading" in result.answer
        assert "breached" in result.answer

    def test_several_breached_assumptions_read_as_a_plural(self, store, decision, span):
        for number in (1, 2):
            rest_on(
                store,
                decision,
                _breached(make_assumption(span, assumption_id=f"A-{number:04d}")),
            )
        assert "2 of those have since been breached" in answered(store).answer

    def test_a_decision_resting_on_nothing_still_answers(self, store, decision):
        result = answered(store)
        assert result.resting_on == ()
        assert "It rested on" not in result.answer

    def test_still_holds_names_the_one_status_that_is_reassuring(self, store, decision, span):
        rest_on(store, decision, _holding(make_assumption(span, assumption_id="A-0001")))
        assert answered(store).resting_on[0].still_holds


class TestCitations:
    def test_the_decisions_span_is_cited(self, store, decision, span):
        assert span.id in answered(store).citations

    def test_every_assumption_behind_the_answer_is_cited_too(self, store, decision, span):
        # Invariant 6 reaching a query rather than an extraction: every claim in
        # the answer points at a place.
        rest_on(store, decision, make_assumption(span, assumption_id="A-0001"))
        assert len(answered(store).citations) == 2

    def test_the_selection_confidence_is_the_models_and_the_content_is_not(self, store, decision):
        result = answered(store, selection(confidence=0.42))
        assert result.confidence == pytest.approx(0.42)


class TestConstruction:
    def test_offering_no_candidates_is_refused(self, store):
        with pytest.raises(ValueError, match="at least one candidate"):
            ArchaeologistAgent(Answering([]), candidates=0)

    def test_the_number_of_candidates_is_bounded(self, store, span):
        for number in range(1, 6):
            store.add(
                Decision(
                    id=f"D-{number:04d}",
                    title="the product search index",
                    chosen="OpenSearch on managed nodes",
                    rejected=REJECTED,
                    decision_maker="Nadeesha",
                    decided_at=AT,
                    scope="team",
                    impact="medium",
                    span_id=span.id,
                    confidence=0.9,
                    created_by=ACTOR,
                    created_at=AT,
                ),
                actor=ACTOR,
                reason="fixture",
            )
        provider = Answering([selection()])
        ArchaeologistAgent(provider, candidates=2).ask(QUESTION, store)
        assert "[2]" not in provider.requests[0].messages[0].content

    def test_one_question_costs_one_call(self, store, decision):
        assert answered(store).calls == 1


def result_status(result: Excavation) -> list[AssumptionStatus]:
    """The status of each assumption an answer reports."""
    return [found.status for found in result.resting_on]


def _breached(assumption: Assumption) -> Assumption:
    """The same assumption, as the monitor would have left it after a breach."""
    return assumption.model_copy(
        update={
            "status": AssumptionStatus.BREACHED,
            "last_evaluated_at": AT + timedelta(days=30),
        }
    )


def _holding(assumption: Assumption) -> Assumption:
    """The same assumption, checked and found to hold."""
    return assumption.model_copy(
        update={
            "status": AssumptionStatus.HOLDING,
            "last_evaluated_at": AT + timedelta(days=30),
        }
    )
