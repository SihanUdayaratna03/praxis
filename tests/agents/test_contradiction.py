"""ContradictionDetector: what arithmetic settles, what a model is asked, and what neither asserts.

Two claims run through this file.

The first is that **the cheap stages do the work**. A pair whose conflict is a
matter of arithmetic never reaches the `reason` tier, and the tests assert that
by counting calls rather than by inspecting a flag -- a detector that produced
the right edges while paying for every one of them would pass a weaker test and
fail the cost story the phase report has to tell.

The second is that **a false contradiction is the expensive direction to be
wrong in**. `TestWhatIsNotAContradiction` is the longer class on purpose: a
stronger claim is not a contradiction of a weaker one, a claim about a different
quantity is not a contradiction at all, and a judgement nobody was confident
about is dropped rather than written.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from praxis.agents.contradiction import (
    CERTAIN,
    DEFAULT_MIN_CONFIDENCE,
    ContradictionDetector,
    claim_of,
)
from praxis.agents.results import Settlement
from praxis.domain.enums import Impact
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Decision, Span
from praxis.ingest.adapters import MARKDOWN_ADAPTER, document_from

from tests.agents.conftest import Answering, Refusing
from tests.monitor.conftest import make_assumption, make_decision

AT = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

BODY = "# ADR\n\nA passage long enough for one span to cover the whole document.\n"


@pytest.fixture
def span() -> Span:
    """A real span through the real adapter, so a cited coordinate is a real one."""
    source = MARKDOWN_ADAPTER.normalise(BODY.encode("utf-8"), source_uri="adr.md")
    document = document_from(source, doc_id="DOC-0001", ingested_at=AT)
    return Span.covering(
        document, 0, len(document.content.encode("utf-8")), created_by="test", created_at=AT
    )


def assumption(span: Span, number: int, predicate: str, statement: str) -> Assumption:
    """One assumption.

    `predicate` may be prose rather than an expression -- the record requires
    the field to be non-empty and says nothing about it parsing, which is
    exactly the state Phase 4's extractor leaves an assumption in and what
    `AssumptionFormalizer` exists to improve on.
    """
    return make_assumption(
        span, assumption_id=f"A-{number:04d}", predicate=predicate, statement=statement
    )


def judgements(
    *entries: tuple[int, bool], confidence: float = 0.9, rationale: str = "conflict"
) -> str:
    """A judging answer as the structured layer would receive it."""
    return json.dumps(
        {
            "judgements": [
                {
                    "pair_ordinal": ordinal,
                    "contradicts": verdict,
                    "rationale": rationale if verdict else None,
                    "confidence": confidence,
                }
                for ordinal, verdict in entries
            ]
        }
    )


def detect(records, provider=None, **kwargs):
    """Run the detector over a handful of records."""
    return ContradictionDetector(provider, **kwargs).detect(records, at=AT)


class TestWhatArithmeticSettles:
    def test_two_disjoint_ranges_contradict_without_a_model(self, span):
        # The reason the reason tier is not asked: a model has no privileged
        # access to the fact that these permit no common value.
        result = detect(
            [
                assumption(span, 1, "index_size_gb <= 50", "the index stays under 50 GB"),
                assumption(span, 2, "index_size_gb > 50", "the index will pass 50 GB"),
            ]
        )
        assert len(result.by_arithmetic) == 1
        assert result.calls == 0

    def test_an_arithmetic_contradiction_is_certain(self, span):
        result = detect(
            [
                assumption(span, 1, "index_size_gb <= 50", "the index stays under 50 GB"),
                assumption(span, 2, "index_size_gb >= 60", "the index passes 60 GB"),
            ]
        )
        assert result.contradictions[0].link.confidence == CERTAIN

    def test_the_rationale_names_both_requirements(self, span):
        result = detect(
            [
                assumption(span, 1, "index_size_gb <= 50", "under 50"),
                assumption(span, 2, "index_size_gb > 50", "over 50"),
            ]
        )
        assert "at most 50" in result.contradictions[0].rationale
        assert "over 50" in result.contradictions[0].rationale

    def test_two_booleans_that_disagree_contradict(self, span):
        result = detect(
            [
                assumption(span, 1, "all_routed_models_valid == true", "every model is valid"),
                assumption(span, 2, "all_routed_models_valid == false", "a model is invalid"),
            ]
        )
        assert len(result.by_arithmetic) == 1

    def test_a_settled_pair_is_never_sent_to_a_model(self, span):
        provider = Answering([judgements((0, True))])
        result = detect(
            [
                assumption(span, 1, "index_size_gb <= 50", "under 50"),
                assumption(span, 2, "index_size_gb > 50", "over 50"),
            ],
            provider,
        )
        assert result.judged == 0
        assert result.calls == 0


class TestWhatIsNotAContradiction:
    def test_a_stronger_claim_does_not_contradict_a_weaker_one(self, span):
        # Anything satisfying `<= 40` satisfies `<= 50`. Only claims with no
        # possible world in common conflict.
        result = detect(
            [
                assumption(span, 1, "index_size_gb <= 50", "under 50 GB"),
                assumption(span, 2, "index_size_gb <= 40", "under 40 GB"),
            ]
        )
        assert result.by_arithmetic == ()

    def test_claims_about_different_quantities_are_never_even_compared(self, span):
        result = detect(
            [
                assumption(span, 1, "index_size_gb <= 50", "the index stays small"),
                assumption(span, 2, "query_latency_ms <= 200", "queries answer quickly"),
            ]
        )
        assert result.blocking.candidates == ()
        assert result.contradictions == ()

    def test_a_shared_endpoint_is_not_a_conflict(self, span):
        result = detect(
            [
                assumption(span, 1, "index_size_gb <= 50", "at most 50"),
                assumption(span, 2, "index_size_gb >= 50", "at least 50"),
            ]
        )
        assert result.by_arithmetic == ()

    def test_a_judgement_below_the_threshold_is_dropped(self, span):
        # A missed contradiction is a gap; a false one sends a person to
        # re-read a decision that was fine, and every later finding pays.
        provider = Answering([judgements((0, True), confidence=DEFAULT_MIN_CONFIDENCE - 0.1)])
        result = detect(_prose_pair(span), provider)
        assert result.contradictions == ()

    def test_a_judgement_of_no_conflict_writes_nothing(self, span):
        provider = Answering([judgements((0, False))])
        assert detect(_prose_pair(span), provider).contradictions == ()

    def test_an_ordinal_that_was_never_offered_is_ignored(self, span):
        # The same rule ADR 0015 makes about a span offering: a mis-addressed
        # judgement would assert a contradiction between the wrong two records.
        provider = Answering([judgements((9, True))])
        assert detect(_prose_pair(span), provider).contradictions == ()

    def test_the_same_ordinal_twice_is_one_edge(self, span):
        provider = Answering([judgements((0, True), (0, True))])
        assert len(detect(_prose_pair(span), provider).contradictions) == 1

    def test_a_refused_batch_asserts_nothing(self, span):
        # Reported as a gap rather than as contradictions nobody asserted.
        result = detect(_prose_pair(span), Refusing([]))
        assert result.contradictions == ()
        assert result.calls == 1


class TestWhatTheModelIsAsked:
    def test_only_the_residue_reaches_it(self, span):
        provider = Answering([judgements((0, False))])
        result = detect(
            [
                assumption(span, 1, "index_size_gb <= 50", "under 50"),
                assumption(span, 2, "index_size_gb > 50", "over 50"),
                *_prose_pair(span),
            ],
            provider,
        )
        assert len(result.by_arithmetic) == 1
        assert result.judged >= 1
        assert result.calls == 1

    def test_a_model_contradiction_carries_its_confidence(self, span):
        provider = Answering([judgements((0, True), confidence=0.75)])
        found = detect(_prose_pair(span), provider).contradictions[0]
        assert found.settled_by is Settlement.MODEL
        assert found.link.confidence == pytest.approx(0.75)

    def test_the_pairs_are_numbered_and_both_sides_shown(self, span):
        provider = Answering([judgements((0, False))])
        detect(_prose_pair(span), provider)
        listing = provider.requests[0].messages[0].content
        assert "[0]" in listing
        assert "  A. " in listing
        assert "  B. " in listing

    def test_a_predicate_is_shown_beside_the_statement(self, span):
        provider = Answering([judgements((0, False))])
        detect(_prose_pair(span), provider)
        assert "in predicate form" in provider.requests[0].messages[0].content

    def test_pairs_are_batched(self, span):
        records = [
            assumption(
                span,
                number,
                f"the search index rollout plan number {number}",
                f"the search index rollout plan number {number}",
            )
            for number in range(1, 6)
        ]
        provider = Answering([judgements((0, False))] * 20)
        result = detect(records, provider, batch=2)
        assert result.calls == pytest.approx((result.judged + 1) // 2, abs=1)

    def test_the_call_names_the_prompt_behind_it(self, span):
        provider = Answering([judgements((0, False))])
        detect(_prose_pair(span), provider)
        assert provider.requests[0].prompt_id == "judge_contradiction@v1"
        assert provider.requests[0].agent == "ContradictionDetector"

    def test_a_contradiction_asserted_with_no_reason_still_says_something(self, span):
        # A Link.rationale may not be empty, so the alternative to a fallback
        # sentence is losing the finding over a missing explanation.
        provider = Answering([judgements((0, True), rationale="")])
        found = detect(_prose_pair(span), provider).contradictions[0]
        assert found.rationale
        assert "no further explanation" in found.rationale

    def test_no_provider_means_no_calls_and_still_finds_the_certain_ones(self, span):
        result = detect(
            [
                assumption(span, 1, "index_size_gb <= 50", "under 50"),
                assumption(span, 2, "index_size_gb > 50", "over 50"),
                *_prose_pair(span),
            ]
        )
        assert result.calls == 0
        assert len(result.by_arithmetic) == 1


class TestTheEdgeItWrites:
    def test_the_pair_is_ordered_so_one_fact_is_one_edge(self, span):
        # A Link's id is derived from its endpoints, so an unordered pair would
        # let two runs write two different edges asserting one thing.
        forwards = detect(
            [
                assumption(span, 1, "index_size_gb <= 50", "under 50"),
                assumption(span, 2, "index_size_gb > 50", "over 50"),
            ]
        )
        backwards = detect(
            [
                assumption(span, 2, "index_size_gb > 50", "over 50"),
                assumption(span, 1, "index_size_gb <= 50", "under 50"),
            ]
        )
        assert forwards.contradictions[0].link.id == backwards.contradictions[0].link.id

    def test_the_edge_is_a_contradicts_edge(self, span):
        result = detect(
            [
                assumption(span, 1, "index_size_gb <= 50", "under 50"),
                assumption(span, 2, "index_size_gb > 50", "over 50"),
            ]
        )
        assert result.contradictions[0].link.link_type is LinkType.CONTRADICTS

    def test_the_edge_cites_a_span(self, span):
        result = detect(
            [
                assumption(span, 1, "index_size_gb <= 50", "under 50"),
                assumption(span, 2, "index_size_gb > 50", "over 50"),
            ]
        )
        assert result.contradictions[0].link.span_id == span.id

    def test_a_known_pair_is_not_reconsidered(self, span):
        records = [
            assumption(span, 1, "index_size_gb <= 50", "under 50"),
            assumption(span, 2, "index_size_gb > 50", "over 50"),
        ]
        found = detect(records)
        again = ContradictionDetector().detect(records, at=AT, known=[found.contradictions[0].pair])
        assert again.contradictions == ()


class TestReadingARecord:
    def test_an_assumption_carries_its_predicate(self, span):
        claim = claim_of(assumption(span, 1, "index_size_gb <= 50", "under 50"))
        assert claim.predicate == "index_size_gb <= 50"
        assert "subject:index_size_gb" in claim.keys

    def test_a_decision_has_no_predicate_and_that_is_not_a_special_case(self, span):
        # It simply carries no subject key and is blocked on prose alone.
        claim = claim_of(make_decision(span))
        assert claim.predicate == ""
        assert not any(key.startswith("subject:") for key in claim.keys)

    def test_a_decision_says_what_it_chose(self, span):
        assert "OpenSearch" in claim_of(make_decision(span)).statement

    def test_an_unparseable_predicate_contributes_no_subject_key(self, span):
        claim = claim_of(assumption(span, 1, "the team stays motivated", "morale holds"))
        assert not any(key.startswith("subject:") for key in claim.keys)

    def test_a_decision_and_an_assumption_can_be_compared(self, span):
        # `contradicts` runs between any two claims, so splitting the pass would
        # mean two indexes that never see each other's records.
        provider = Answering([judgements((0, True))])
        records: list[Assumption | Decision] = [
            assumption(
                span,
                1,
                "we are going with OpenSearch on managed nodes",
                "we are going with OpenSearch on managed nodes",
            ),
            make_decision(span, impact=Impact.HIGH),
        ]
        found = detect(records, provider)
        assert len(found.contradictions) == 1


class TestConstruction:
    @pytest.mark.parametrize(("pairs", "batch"), [(-1, 12), (10, 0)])
    def test_a_bound_outside_what_it_means_is_refused(self, pairs, batch):
        with pytest.raises(ValueError, match="max_pairs is a count"):
            ContradictionDetector(None, max_pairs=pairs, batch=batch)

    def test_a_threshold_outside_zero_to_one_is_refused(self):
        with pytest.raises(ValueError, match="between 0 and 1"):
            ContradictionDetector(None, min_confidence=2.0)

    def test_the_pair_cap_bounds_what_is_considered(self, span):
        records = [
            assumption(span, number, "shared_quantity <= 1", f"a claim numbered {number}")
            for number in range(1, 8)
        ]
        assert len(detect(records, max_pairs=3).blocking.candidates) == 3

    def test_no_records_is_a_run(self, span):
        result = detect([])
        assert result.contradictions == ()
        assert result.blocking.indexed == 0


def _prose_pair(span: Span) -> list[Assumption]:
    """Two claims about one subject that arithmetic cannot separate.

    Numbered from three so a test can hold these beside an arithmetic pair
    without one overwriting the other.

    Both predicates are prose, so `constraints_of` reads nothing from either and
    the pair is proposed on shared words alone -- which is exactly the residue
    the model exists to judge.
    """
    return [
        assumption(
            span,
            3,
            "the search index rollout finishes this quarter",
            "the search index rollout finishes this quarter",
        ),
        assumption(
            span,
            4,
            "the search index rollout slips past this quarter",
            "the search index rollout slips past this quarter",
        ),
    ]
