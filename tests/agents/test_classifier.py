"""WorkClassifier, and the failure it exists to prevent.

That failure is not a wrong class. A wrong class is visible and somebody fixes
it. It is **two spellings of one class**, which is invisible: nothing raises,
nothing looks wrong, and one estimator's ten migrations quietly become two sets
of five under a detective that refuses below `n = 5`. So the tests that matter
most here are the normalisation ones and the "was this class new" one, not the
happy path.

`work_class_of` is property-tested as well as exampled, because it is the single
function standing between a model's prose and a grouping key. It is total by
construction -- every input produces a valid `WorkClass` or `unclassified` -- and
`Estimate` is what checks that claim, since the record model is the authority on
what its own field accepts.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.agents.classifier import (
    CLASSIFIER_NAME,
    CLASSIFY_TASK,
    MAX_KNOWN_CLASSES,
    UNCLASSIFIED,
    Classification,
    ClassificationRefusal,
    WorkClassifier,
    is_classified,
    work_class_of,
)
from praxis.agents.errors import Refusal
from praxis.config.models import ModelRole, role_for_agent
from praxis.domain.enums import Unit
from praxis.domain.ids import EstimateId
from praxis.domain.records import Document, Estimate, Span
from praxis.llm.mock import MockProvider
from praxis.llm.trace import MemoryTraceSink
from praxis.prompts.library import load

from tests.agents.conftest import AT, Answering, Refusing, make_document, spans_of

KNOWN = ("infrastructure", "backend", "migration", "data-engineering")
"""A vocabulary the store already holds, in the shape the corpus really uses."""


@pytest.fixture
def document() -> Document:
    return make_document()


@pytest.fixture
def evidence(document: Document) -> Span:
    """The passage the estimate was read from. Verified when it was written."""
    return spans_of(document)[0]


def an_estimate(work_class: str = UNCLASSIFIED, span_id: str = "SPAN-0000000000000001") -> Estimate:
    """An estimate as `EstimateExtractor` leaves one: cited, and unclassified."""
    return Estimate(
        id=EstimateId("EST-0001"),
        subject="the search index migration",
        owner="Nadeesha",
        work_class=work_class,
        active_quantity=Decimal(4),
        blocked_quantity=Decimal(0),
        unit=Unit.WEEKS,
        confidence=0.7,
        estimated_at=AT,
        span_id=span_id,
        created_by="EstimateExtractor",
        created_at=AT,
    )


def answer(**fields: object) -> str:
    """One classification in the shape the agent asks for."""
    payload = {
        "work_class": "migration",
        "existing": True,
        "why": "the passage is about moving a search index onto new infrastructure",
        "confidence": 0.8,
    }
    payload.update(fields)
    return json.dumps(payload)


class TestRouting:
    """The name buys the tier, and the deterministic set must not claim it."""

    def test_is_routed_to_the_scan_tier(self) -> None:
        assert role_for_agent(CLASSIFIER_NAME) is ModelRole.SCAN

    def test_reads_a_prompt_that_exists(self) -> None:
        prompt = load(CLASSIFY_TASK)
        assert prompt.id.startswith(CLASSIFY_TASK)
        assert prompt.sha256

    def test_the_prompt_argues_against_a_near_duplicate_class(self) -> None:
        """The whole point of showing the model the vocabulary is in the prompt."""
        body = load(CLASSIFY_TASK).render().lower()
        assert "two spellings" in body
        assert "null" in body


class TestNormalisation:
    """`work_class_of` is the one function between prose and a grouping key."""

    @pytest.mark.parametrize(
        ("stated", "expected"),
        [
            ("migration", "migration"),
            ("Data Migration", "data-migration"),
            ("data migration", "data-migration"),
            ("  DATA   MIGRATION  ", "data-migration"),
            ("data-engineering", "data-engineering"),
            ("Mobile", "mobile"),
        ],
    )
    def test_two_spellings_of_one_class_become_one(self, stated: str, expected: str) -> None:
        assert work_class_of(stated) == expected

    @pytest.mark.parametrize(
        "stated",
        ["", "   ", "data_migration", "data/migration", "migration!", "—", "a--b", "-migration"],
    )
    def test_anything_that_is_not_a_run_of_words_is_unclassified(self, stated: str) -> None:
        """Not repaired into a class. `unclassified` is a row somebody can act on."""
        assert work_class_of(stated) == UNCLASSIFIED

    def test_nothing_said_is_unclassified(self) -> None:
        assert work_class_of(None) == UNCLASSIFIED

    @given(st.text(max_size=60))
    def test_is_total_and_always_produces_something_the_record_accepts(self, stated: str) -> None:
        """Every input produces a class `Estimate` will hold. The record decides."""
        assigned = work_class_of(stated)
        assert an_estimate(work_class=assigned).work_class == assigned

    @given(st.text(max_size=60))
    def test_is_idempotent(self, stated: str) -> None:
        """Normalising twice is normalising once, or the vocabulary drifts per pass."""
        once = work_class_of(stated)
        assert work_class_of(once) == once


class TestIsClassified:
    """The cheap check that makes a second pass cost nothing."""

    def test_an_unclassified_estimate_is_not_classified(self) -> None:
        assert not is_classified(an_estimate())

    def test_a_classified_one_is(self) -> None:
        assert is_classified(an_estimate(work_class="migration"))


class TestClassification:
    """What the agent does with a usable answer."""

    def test_puts_the_estimate_on_the_axis(self, evidence: Span) -> None:
        provider = Answering([answer()])

        result = WorkClassifier(provider).classify(an_estimate(), evidence, KNOWN, at=AT)

        assert isinstance(result, Classification)
        assert result.work_class == "migration"
        assert result.estimate.work_class == "migration"
        assert result.classified

    def test_keeps_the_citation_the_estimate_already_had(self, evidence: Span) -> None:
        """A classification is a judgement about a record, not a claim about a document."""
        estimate = an_estimate()
        provider = Answering([answer()])

        result = WorkClassifier(provider).classify(estimate, evidence, KNOWN, at=AT)

        assert isinstance(result, Classification)
        assert result.estimate.span_id == estimate.span_id

    def test_changes_nothing_else_about_the_record(self, evidence: Span) -> None:
        estimate = an_estimate()
        provider = Answering([answer()])

        result = WorkClassifier(provider).classify(estimate, evidence, KNOWN, at=AT)

        assert isinstance(result, Classification)
        assert result.estimate.model_dump(
            exclude={"work_class", "created_by", "created_at"}
        ) == estimate.model_dump(exclude={"work_class", "created_by", "created_at"})

    def test_records_itself_as_the_author_of_the_new_version(self, evidence: Span) -> None:
        provider = Answering([answer()])

        result = WorkClassifier(provider).classify(an_estimate(), evidence, KNOWN, at=AT)

        assert isinstance(result, Classification)
        assert result.estimate.created_by == CLASSIFIER_NAME
        assert result.estimate.created_at == AT

    def test_a_class_already_in_use_is_not_reported_as_proposed(self, evidence: Span) -> None:
        provider = Answering([answer(work_class="backend")])

        result = WorkClassifier(provider).classify(an_estimate(), evidence, KNOWN, at=AT)

        assert isinstance(result, Classification)
        assert not result.proposed

    def test_a_class_new_to_the_store_is_reported_as_proposed(self, evidence: Span) -> None:
        """A run inventing a class per estimate is a fragmenting vocabulary.

        Reported as a number here rather than discovered six phases later as a
        calibration history nobody can group.
        """
        provider = Answering([answer(work_class="observability", existing=False)])

        result = WorkClassifier(provider).classify(an_estimate(), evidence, KNOWN, at=AT)

        assert isinstance(result, Classification)
        assert result.proposed

    def test_proposed_is_computed_from_the_store_not_taken_from_the_model(
        self, evidence: Span
    ) -> None:
        """The model claiming it picked an existing class does not make it one."""
        provider = Answering([answer(work_class="observability", existing=True)])

        result = WorkClassifier(provider).classify(an_estimate(), evidence, KNOWN, at=AT)

        assert isinstance(result, Classification)
        assert result.proposed

    def test_unclassified_is_never_reported_as_a_proposed_class(self, evidence: Span) -> None:
        provider = Answering([answer(work_class=None)])

        result = WorkClassifier(provider).classify(an_estimate(), evidence, KNOWN, at=AT)

        assert isinstance(result, Classification)
        assert not result.proposed

    def test_an_empty_store_is_told_so_rather_than_shown_an_empty_list(
        self, evidence: Span
    ) -> None:
        provider = Answering([answer()])

        WorkClassifier(provider).classify(an_estimate(), evidence, (), at=AT)

        asked = provider.requests[0].messages[0].content
        assert "none yet" in asked

    def test_the_vocabulary_is_offered_in_the_house_numbering(self, evidence: Span) -> None:
        """`praxis.llm.synthesis` reads bracketed labels, so the shape is load-bearing."""
        provider = Answering([answer()])

        WorkClassifier(provider).classify(an_estimate(), evidence, KNOWN, at=AT)

        asked = provider.requests[0].messages[0].content
        assert "[0] infrastructure" in asked
        assert "[3] data-engineering" in asked

    def test_an_enormous_vocabulary_is_truncated_rather_than_pasted_whole(
        self, evidence: Span
    ) -> None:
        """A store past the cap has a fragmentation problem a prompt cannot fix."""
        many = tuple(f"class-{index}" for index in range(MAX_KNOWN_CLASSES * 2))
        provider = Answering([answer()])

        WorkClassifier(provider).classify(an_estimate(), evidence, many, at=AT)

        asked = provider.requests[0].messages[0].content
        assert f"[{MAX_KNOWN_CLASSES - 1}] " in asked
        assert f"[{MAX_KNOWN_CLASSES}] " not in asked


class TestSayingNothing:
    """`unclassified` is an answer. No answer at all is a different row."""

    def test_a_model_that_cannot_tell_leaves_the_estimate_unclassified(
        self, evidence: Span
    ) -> None:
        provider = Answering([answer(work_class=None, why="the passage names no kind of work")])

        result = WorkClassifier(provider).classify(an_estimate(), evidence, KNOWN, at=AT)

        assert isinstance(result, Classification)
        assert result.work_class == UNCLASSIFIED
        assert not result.classified

    def test_a_refusing_model_is_a_refusal_rather_than_an_unclassified_row(
        self, evidence: Span
    ) -> None:
        """Different responses: one is a row to look at, one is a call to make again."""
        provider = Refusing([])

        result = WorkClassifier(provider).classify(an_estimate(), evidence, KNOWN, at=AT)

        assert isinstance(result, ClassificationRefusal)
        assert result.refusal is Refusal.NO_USABLE_ANSWER
        assert result.estimate_id == "EST-0001"

    def test_an_unusable_answer_is_a_refusal_and_counts_its_attempts(self, evidence: Span) -> None:
        provider = Answering(["not json"] * 8)

        result = WorkClassifier(provider).classify(an_estimate(), evidence, KNOWN, at=AT)

        assert isinstance(result, ClassificationRefusal)
        assert result.calls > 1

    def test_a_classification_with_no_stated_reason_still_has_an_audit_reason(
        self, evidence: Span
    ) -> None:
        """`AuditEvent.reason` is non-empty, so a thin answer must not fail the write."""
        provider = Answering([answer(why=None)])

        result = WorkClassifier(provider).classify(an_estimate(), evidence, KNOWN, at=AT)

        assert isinstance(result, Classification)
        assert result.reason.strip()

    def test_an_unclassified_row_with_no_reason_says_why_it_is_unclassified(
        self, evidence: Span
    ) -> None:
        provider = Answering([answer(work_class=None, why="   ")])

        result = WorkClassifier(provider).classify(an_estimate(), evidence, KNOWN, at=AT)

        assert isinstance(result, Classification)
        assert "does not make the kind of work clear" in result.reason


class TestOffline:
    """The mock is the default and this has to survive it without credentials."""

    def test_runs_against_the_mock_and_produces_a_valid_class(self, evidence: Span) -> None:
        sink = MemoryTraceSink()
        provider = MockProvider(sink=sink)

        result = WorkClassifier(provider).classify(an_estimate(), evidence, KNOWN, at=AT)

        if isinstance(result, Classification):
            # Whatever the mock said, the record model is what accepted it.
            assert result.estimate.work_class == result.work_class
        assert len(sink.traces) == result.calls
        assert {trace.agent for trace in sink.traces} == {CLASSIFIER_NAME}

    def test_the_request_names_the_agent_the_prompt_and_the_record(self, evidence: Span) -> None:
        provider = Answering([answer()])

        WorkClassifier(provider).classify(an_estimate(), evidence, KNOWN, at=AT)

        request = provider.requests[0]
        assert request.agent == CLASSIFIER_NAME
        assert request.task == CLASSIFY_TASK
        assert request.prompt_id.startswith(CLASSIFY_TASK)
        assert request.metadata["estimate_id"] == "EST-0001"
        assert request.metadata["known_classes"] == str(len(KNOWN))

    def test_the_clock_it_is_given_is_timezone_aware(self) -> None:
        assert AT.tzinfo is UTC
        assert datetime(2026, 3, 1, tzinfo=UTC).tzinfo is not None
