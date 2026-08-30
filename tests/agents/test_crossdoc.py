"""The estimate is in the ADR and the actual is in the status update.

That is the case Phase 6 could not answer and deferred here, and the fixtures
have held both halves of it since Phase 4: `ADR_BODY` says *"Nadeesha put this
at 4 weeks of hands-on work"*, `STATUS_BODY` says *"The search index migration
actually took 7 weeks"*, and they are different documents. `OutcomeMatcher`
looks in one document, so on its own it answers `MODEL_FOUND_NONE` here forever.

The claim this file has to hold is not that the pairing is found -- that is the
easy half. It is that **finding it costs ADR 0015 nothing**: every listing is
still one document's spans, `offering_of` still refuses anything else, and the
extra cost is calls rather than passages. `TestTheInvariantIsNotSpent` is the
class that says so, and it asserts against `offering_of` itself rather than
against a description of it.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from praxis.agents.classifier import UNCLASSIFIED
from praxis.agents.crossdoc import (
    CROSSDOC_NAME,
    DEFAULT_MAX_DOCUMENTS,
    CrossDocumentMatcher,
    documents_for,
)
from praxis.agents.matcher import MatchedOutcome, OutcomeMatcher, Unmatched, UnmatchedEstimate
from praxis.agents.offering import offering_of
from praxis.config.models import NON_LLM_AGENTS, routed_agents
from praxis.domain.enums import MatchQuality, RecordKind, Unit
from praxis.domain.ids import DocumentId, EstimateId, format_sequential_id
from praxis.domain.records import Document, Estimate, Span

from tests.agents.conftest import ADR_BODY, AT, Answering, make_document, spans_of
from tests.agents.test_matcher import ACTUAL_QUOTE, STATUS_BODY, ordinal_of

HOME = DocumentId("DOC-0001")
"""The ADR the estimate is stated in."""

AWAY = DocumentId("DOC-0009")
"""The status update the actual is stated in."""

UNRELATED = DocumentId("DOC-0042")
"""A document about something else entirely, which must never be paid for."""

UNRELATED_BODY = """\
# Kitchen rota

Ravindu waters the plants on Tuesdays. Chamari refills the coffee.

Nobody has decided who buys the milk.
"""


@pytest.fixture
def adr() -> Document:
    return make_document(ADR_BODY, doc_id=HOME)


@pytest.fixture
def status() -> Document:
    return make_document(STATUS_BODY, doc_id=AWAY)


@pytest.fixture
def unrelated() -> Document:
    return make_document(UNRELATED_BODY, doc_id=UNRELATED)


@pytest.fixture
def corpus(
    adr: Document, status: Document, unrelated: Document
) -> tuple[dict[DocumentId, Document], dict[DocumentId, tuple[Span, ...]]]:
    """Three documents and their spans, as the store would hand them over."""
    documents = {adr.id: adr, status.id: status, unrelated.id: unrelated}
    spans = {
        adr.id: spans_of(adr),
        status.id: spans_of(status),
        unrelated.id: spans_of(unrelated),
    }
    return documents, spans


def an_estimate(spans: tuple[Span, ...]) -> Estimate:
    """The ADR's own estimate, citing a passage in the ADR."""
    cited = next(span for span in spans if "4 weeks of hands-on work" in span.text)
    return Estimate(
        id=EstimateId("EST-0001"),
        subject="the search index migration",
        owner="Nadeesha",
        work_class=UNCLASSIFIED,
        active_quantity=Decimal(4),
        blocked_quantity=Decimal(0),
        unit=Unit.WEEKS,
        confidence=0.7,
        estimated_at=AT,
        span_id=cited.id,
        created_by="EstimateExtractor",
        created_at=AT,
    )


class Allocating:
    """An id allocator that counts, so wasted allocations are visible."""

    def __init__(self) -> None:
        self.calls: list[RecordKind] = []

    def __call__(self, kind: RecordKind) -> str:
        self.calls.append(kind)
        return format_sequential_id(kind, len(self.calls))


def found_none(why: str = "nothing here reports this work finishing") -> str:
    """The answer a document that does not hold the actual gives."""
    return json.dumps(
        {
            "resolved": False,
            "passage_ordinal": None,
            "quote": None,
            "active_quantity": None,
            "blocked_quantity": None,
            "unit": None,
            "why": why,
            "confidence": 0.8,
        }
    )


def found_it(ordinal: int, quote: str = ACTUAL_QUOTE) -> str:
    """A resolving answer citing a passage that really holds the quotation.

    The quotation is a parameter because the citation gate is real here: an
    answer quoting the status update while being shown the ADR is refused as
    `UNCITED`, which is the gate working and not a fixture to route around.
    """
    return json.dumps(
        {
            "resolved": True,
            "passage_ordinal": ordinal,
            "quote": quote,
            "active_quantity": 7,
            "blocked_quantity": 0,
            "unit": "weeks",
            "why": "the same migration and the same owner, reported in the past tense",
            "confidence": 0.9,
        }
    )


class TestThePairingPhaseSixCouldNotMake:
    """The deferred case, end to end, over the fixtures that always held it."""

    def test_an_actual_in_another_document_now_resolves_the_estimate(
        self, corpus: tuple[dict[DocumentId, Document], dict[DocumentId, tuple[Span, ...]]]
    ) -> None:
        """The ADR states 4 weeks, the status update says 7, and the pair is made.

        Two calls: the home document answers "nothing here", the status update
        answers with the passage that really holds the actual.
        """
        documents, spans = corpus
        estimate = an_estimate(spans[HOME])
        provider = Answering(
            [found_none(), found_it(ordinal_of(spans[AWAY], estimate, ACTUAL_QUOTE))]
        )
        matcher = CrossDocumentMatcher(OutcomeMatcher(provider))

        result = matcher.match(estimate, spans, documents, home=HOME, allocate=Allocating(), at=AT)

        assert result.resolved
        assert result.resolved_in == AWAY
        assert result.left_home
        assert isinstance(result.result, MatchedOutcome)
        assert result.result.outcome.active_quantity == Decimal(7)
        assert result.result.outcome.match_quality is not MatchQuality.UNRESOLVED

    def test_the_single_document_matcher_alone_still_cannot_make_it(
        self, corpus: tuple[dict[DocumentId, Document], dict[DocumentId, tuple[Span, ...]]]
    ) -> None:
        """The control, and the whole reason this module exists.

        Without it, the same estimate against the same corpus resolves to
        nothing -- so the test above is measuring the new component rather than
        a fixture that was always going to pass.
        """
        documents, spans = corpus
        estimate = an_estimate(spans[HOME])
        provider = Answering([found_none()])

        result = OutcomeMatcher(provider).match(
            estimate, spans[HOME], documents[HOME], allocate=Allocating(), at=AT
        )

        assert isinstance(result, UnmatchedEstimate)
        assert result.reason is Unmatched.MODEL_FOUND_NONE

    def test_the_home_document_is_read_first_and_stops_the_walk(
        self, corpus: tuple[dict[DocumentId, Document], dict[DocumentId, tuple[Span, ...]]]
    ) -> None:
        """The common case costs exactly what it cost in Phase 6: one call.

        A component that made every estimate more expensive to buy a rare
        pairing would not be worth having, so this is the budget assertion.
        """
        documents, spans = corpus
        estimate = an_estimate(spans[HOME])
        home_quote = "Nadeesha put this at 4 weeks of hands-on work"
        provider = Answering(
            [found_it(ordinal_of(spans[HOME], estimate, home_quote), quote=home_quote)]
        )
        matcher = CrossDocumentMatcher(OutcomeMatcher(provider))

        result = matcher.match(estimate, spans, documents, home=HOME, allocate=Allocating(), at=AT)

        assert result.resolved_in == HOME
        assert not result.left_home
        assert len(result.attempts) == 1
        assert len(provider.requests) == 1


class TestTheInvariantIsNotSpent:
    """ADR 0015 costs nothing here, asserted against the code that enforces it."""

    def test_one_offering_still_refuses_two_documents(
        self, corpus: tuple[dict[DocumentId, Document], dict[DocumentId, tuple[Span, ...]]]
    ) -> None:
        """The guard this phase was warned not to weaken, still guarding.

        If cross-document matching had been built by widening the listing, this
        test is the one that would have had to be deleted. It is here so that
        deleting it is a visible act rather than a side effect.
        """
        _, spans = corpus
        mixed = [spans[HOME][0], spans[AWAY][0]]

        with pytest.raises(ValueError, match="one offering, one document"):
            offering_of(mixed)

    def test_every_call_is_shown_exactly_one_document(
        self, corpus: tuple[dict[DocumentId, Document], dict[DocumentId, tuple[Span, ...]]]
    ) -> None:
        """The claim in prose, checked against what the provider was really sent.

        Each rendered listing must contain passages from one document only, so
        no call ever had the chance to assemble a claim out of two sources.
        """
        documents, spans = corpus
        estimate = an_estimate(spans[HOME])
        provider = Answering(
            [found_none(), found_it(ordinal_of(spans[AWAY], estimate, ACTUAL_QUOTE))]
        )
        matcher = CrossDocumentMatcher(OutcomeMatcher(provider))

        matcher.match(estimate, spans, documents, home=HOME, allocate=Allocating(), at=AT)

        assert len(provider.requests) == 2
        for request, document_id in zip(provider.requests, (HOME, AWAY), strict=True):
            sent = request.messages[0].content
            others = {other for other in (HOME, AWAY, UNRELATED) if other != document_id}
            leaked = [
                other for other in others if any(span.text[:40] in sent for span in spans[other])
            ]
            assert not leaked, f"{document_id}'s listing carried {leaked}"


class TestSelection:
    """Which documents are worth a call, decided before any model sees anything."""

    def test_a_document_sharing_no_vocabulary_is_never_read(
        self, corpus: tuple[dict[DocumentId, Document], dict[DocumentId, tuple[Span, ...]]]
    ) -> None:
        """The kitchen rota is never paid for. Selection before a model."""
        documents, spans = corpus
        estimate = an_estimate(spans[HOME])

        order = documents_for(estimate, spans, home=HOME, documents=documents)

        assert order[0] == HOME
        assert AWAY in order
        assert UNRELATED not in order

    def test_home_comes_first_even_when_another_document_scores_higher(
        self, corpus: tuple[dict[DocumentId, Document], dict[DocumentId, tuple[Span, ...]]]
    ) -> None:
        """Home is never ranked and never filtered out.

        The status update mentions the migration more often than the ADR does,
        so a pure overlap ranking would read it first. Home leads anyway, for
        the same reason `candidates_for` always offers the estimate's own span:
        an estimate and its actual stated together is the commonest shape, and
        losing it to a heuristic would cost more than the ordering buys.
        """
        documents, spans = corpus
        estimate = an_estimate(spans[HOME])

        order = documents_for(estimate, spans, home=HOME, documents=documents)

        assert order[0] == HOME

    def test_the_budget_caps_how_many_documents_an_estimate_can_cost(
        self, corpus: tuple[dict[DocumentId, Document], dict[DocumentId, tuple[Span, ...]]]
    ) -> None:
        """Without a cap this is documents times estimates, per run."""
        documents, spans = corpus
        estimate = an_estimate(spans[HOME])

        order = documents_for(estimate, spans, home=HOME, documents=documents, limit=1)

        assert order == (HOME,)

    def test_the_order_is_the_same_on_a_second_call(
        self, corpus: tuple[dict[DocumentId, Document], dict[DocumentId, tuple[Span, ...]]]
    ) -> None:
        """Ties are broken by id, so two runs read the same documents in one order."""
        documents, spans = corpus
        estimate = an_estimate(spans[HOME])

        first = documents_for(estimate, spans, home=HOME, documents=documents)
        second = documents_for(estimate, spans, home=HOME, documents=documents)

        assert first == second

    def test_a_document_with_spans_but_no_record_is_not_offered(
        self, corpus: tuple[dict[DocumentId, Document], dict[DocumentId, tuple[Span, ...]]]
    ) -> None:
        """The caller must never be handed an id it would then fail to look up."""
        documents, spans = corpus
        estimate = an_estimate(spans[HOME])
        without_status = {key: value for key, value in documents.items() if key != AWAY}

        order = documents_for(estimate, spans, home=HOME, documents=without_status)

        assert AWAY not in order


class TestWhenNothingResolvesIt:
    """Every path still leaves exactly one row. ADR 0022, across documents."""

    def test_the_home_row_is_the_one_kept(
        self, corpus: tuple[dict[DocumentId, Document], dict[DocumentId, tuple[Span, ...]]]
    ) -> None:
        """Read three documents, resolve none, store the row a reader expects.

        The home document's unresolved outcome rather than the last one read:
        its notes name the stage that lost the estimate in the document somebody
        would look in first, and how far the search went is on `attempts`.
        """
        documents, spans = corpus
        estimate = an_estimate(spans[HOME])
        provider = Answering([found_none(), found_none()])
        matcher = CrossDocumentMatcher(OutcomeMatcher(provider))

        result = matcher.match(estimate, spans, documents, home=HOME, allocate=Allocating(), at=AT)

        assert not result.resolved
        assert result.resolved_in is None
        assert not result.left_home
        assert isinstance(result.result, UnmatchedEstimate)
        assert result.result.outcome.match_quality is MatchQuality.UNRESOLVED
        assert result.attempts[0].home

    def test_each_attempt_says_for_itself_whether_it_answered(
        self, corpus: tuple[dict[DocumentId, Document], dict[DocumentId, tuple[Span, ...]]]
    ) -> None:
        """Per-document, not just per-estimate.

        The store pass reports how many documents were read against how many
        answered, and that ratio is what says whether the budget is set right.
        """
        documents, spans = corpus
        estimate = an_estimate(spans[HOME])
        provider = Answering(
            [found_none(), found_it(ordinal_of(spans[AWAY], estimate, ACTUAL_QUOTE))]
        )
        matcher = CrossDocumentMatcher(OutcomeMatcher(provider))

        result = matcher.match(estimate, spans, documents, home=HOME, allocate=Allocating(), at=AT)

        assert [attempt.resolved for attempt in result.attempts] == [False, True]

    def test_the_calls_reported_are_every_call_made(
        self, corpus: tuple[dict[DocumentId, Document], dict[DocumentId, tuple[Span, ...]]]
    ) -> None:
        """The retries are the cost this module adds, so they are what it reports.

        Counting only the winning call would hide exactly the number a reader
        needs in order to judge whether the budget is set right.
        """
        documents, spans = corpus
        estimate = an_estimate(spans[HOME])
        provider = Answering([found_none(), found_none()])
        matcher = CrossDocumentMatcher(OutcomeMatcher(provider))

        result = matcher.match(estimate, spans, documents, home=HOME, allocate=Allocating(), at=AT)

        assert result.calls == sum(attempt.result.calls for attempt in result.attempts)
        assert result.calls == len(provider.requests)


class TestConstruction:
    """The budget is configuration, and bad configuration fails loudly."""

    def test_a_budget_below_one_is_refused(self) -> None:
        """Zero would refuse every estimate for a reason about configuration."""
        with pytest.raises(ValueError, match="at least one document"):
            CrossDocumentMatcher(OutcomeMatcher(Answering([])), max_documents=0)

    def test_the_default_budget_leaves_room_to_leave_home(self) -> None:
        """One would make this module identical to the one it wraps."""
        assert DEFAULT_MAX_DOCUMENTS > 1

    def test_the_driver_claims_neither_agent_set(self) -> None:
        """It makes no call and it is not deterministic, so it claims neither.

        Putting it in `NON_LLM_AGENTS` would assert that no model runs underneath
        it, which is false; giving it a route would bill it for calls
        `OutcomeMatcher` makes and is already billed for.
        """
        assert CROSSDOC_NAME not in NON_LLM_AGENTS
        assert CROSSDOC_NAME not in routed_agents()
