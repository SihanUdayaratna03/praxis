"""The shared citation gate, tested where it lives rather than through an agent.

Both extraction agents route every claim through this, so its contract is the
one thing in `praxis/agents/` that two callers can disagree about. Testing it
only through `DecisionStructurer` would mean a change that broke the extractor's
half went green.

The order of the checks is the part worth pinning: each test below that names
two possible failures is asserting which one wins, and every one of those
choices is a row in the eval table that would otherwise be filed wrong.
"""

from __future__ import annotations

import pytest
from praxis.agents.citation import CitationGate, Cited, Uncited
from praxis.agents.errors import Refusal
from praxis.agents.offering import offering_of
from praxis.ingest.verifier import ClaimMatch, VerifierAgent

from tests.agents.conftest import ADR_BODY, make_document

DECISION_INDEX = 3
EXACT_QUOTE = "We are going with OpenSearch on managed nodes"
WRAPPED_QUOTE = "managed nodes, rather than Postgres full-text search"
NEIGHBOUR_QUOTE = "Decided 2026-01-05 by the Discovery team."
INVENTED_QUOTE = "We are going with Cassandra on bare metal in the Colombo rack."


@pytest.fixture
def gate(spans, document) -> CitationGate:
    """A gate over the whole fixture ADR, so every ordinal is a real passage."""
    return CitationGate(
        offering=offering_of(spans),
        document=document,
        verifier=VerifierAgent(),
        subject="decision",
    )


class TestACitationThatHolds:
    def test_an_exact_quotation_comes_back_with_its_span(self, gate, spans):
        cited = gate.check(DECISION_INDEX, EXACT_QUOTE)
        assert isinstance(cited, Cited)
        assert cited.span.id == spans[DECISION_INDEX].id
        assert cited.match is ClaimMatch.EXACT
        assert cited.quote == EXACT_QUOTE

    def test_a_quotation_that_only_wrapped_is_accepted_and_said_to_have(self, gate):
        cited = gate.check(DECISION_INDEX, WRAPPED_QUOTE)
        assert isinstance(cited, Cited)
        assert cited.match is ClaimMatch.WHITESPACE

    def test_one_gate_checks_every_claim_in_one_answer(self, gate, spans):
        # Why this is a gate and not a function: an answer carries several
        # claims and all of them cite the same listing.
        first = gate.check(DECISION_INDEX, EXACT_QUOTE)
        second = gate.check(1, NEIGHBOUR_QUOTE)
        assert isinstance(first, Cited)
        assert isinstance(second, Cited)
        assert first.span.id != second.span.id


class TestACitationThatDoesNot:
    def test_no_ordinal_at_all_is_an_unoffered_span(self, gate):
        refused = gate.check(None, EXACT_QUOTE)
        assert isinstance(refused, Uncited)
        assert refused.refusal is Refusal.UNOFFERED_SPAN
        assert refused.ordinal is None

    @pytest.mark.parametrize("ordinal", [-1, 99, 1000])
    def test_an_ordinal_outside_the_listing_is_refused_and_carried(self, gate, ordinal):
        refused = gate.check(ordinal, EXACT_QUOTE)
        assert refused.refusal is Refusal.UNOFFERED_SPAN
        assert refused.ordinal == ordinal

    def test_a_quotation_from_another_offered_passage_is_a_mis_attribution(self, gate):
        refused = gate.check(DECISION_INDEX, NEIGHBOUR_QUOTE)
        assert refused.refusal is Refusal.MIS_ATTRIBUTED_QUOTE
        assert refused.ordinal == DECISION_INDEX

    def test_a_quotation_from_nowhere_is_a_fabrication(self, gate):
        refused = gate.check(DECISION_INDEX, INVENTED_QUOTE)
        assert refused.refusal is Refusal.FABRICATED_QUOTE

    @pytest.mark.parametrize("quote", [None, "", "   "])
    def test_a_claim_with_no_quotation_is_a_fabrication(self, gate, quote):
        refused = gate.check(DECISION_INDEX, quote)
        assert refused.refusal is Refusal.FABRICATED_QUOTE
        assert "quotes nothing" in refused.detail

    def test_every_refusal_carries_the_evidence_against_it(self, gate):
        # A verdict reported without this is a verdict nobody can check.
        for ordinal, quote in [(None, EXACT_QUOTE), (99, EXACT_QUOTE), (3, INVENTED_QUOTE)]:
            assert gate.check(ordinal, quote).detail


class TestTheOrderOfTheChecks:
    def test_a_moved_document_beats_a_fabricated_quotation(self, spans):
        # Both are true of this call: the span will not re-read *and* the
        # quotation is in none of the passages. The document moving is the
        # cause, and reporting the other would blame the model for it.
        rewritten = make_document(text=ADR_BODY.replace("OpenSearch", "Solr"))
        gate = CitationGate(
            offering=offering_of(spans),
            document=rewritten,
            verifier=VerifierAgent(),
            subject="decision",
        )
        refused = gate.check(DECISION_INDEX, INVENTED_QUOTE)
        assert refused.refusal is Refusal.SPAN_DOES_NOT_RESOLVE

    def test_an_unoffered_ordinal_beats_everything_downstream(self, spans):
        # Nothing can be re-read or quoted against a passage that was never
        # shown, so this has to be asked first or the later checks are asked
        # about nothing.
        rewritten = make_document(text=ADR_BODY.replace("OpenSearch", "Solr"))
        gate = CitationGate(
            offering=offering_of(spans),
            document=rewritten,
            verifier=VerifierAgent(),
            subject="decision",
        )
        assert gate.check(99, INVENTED_QUOTE).refusal is Refusal.UNOFFERED_SPAN


def test_the_subject_appears_in_the_refusals_it_reads_in(spans, document):
    """Prose only, and the reason the gate is not agent-specific."""
    gate = CitationGate(
        offering=offering_of(spans),
        document=document,
        verifier=VerifierAgent(),
        subject="assumption",
    )
    assert "assumption" in gate.check(None, EXACT_QUOTE).detail
    assert "assumption" in gate.check(DECISION_INDEX, "").detail


def test_a_gate_is_a_value(spans, document):
    """Frozen, so a caller cannot re-point one halfway through an answer."""
    gate = CitationGate(
        offering=offering_of(spans),
        document=document,
        verifier=VerifierAgent(),
        subject="decision",
    )
    with pytest.raises(AttributeError):
        gate.subject = "assumption"
