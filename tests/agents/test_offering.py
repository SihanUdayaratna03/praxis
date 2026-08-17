"""The numbered listing every extraction cites through.

Two properties carry ADR 0015, and they are different claims:

- an ordinal an agent answers with either resolves to a span it was really
  shown, or is refused -- there is no third outcome, which is what removes the
  fabricated citation *target*; and
- an agent can still cite one passage and quote another, which is not removed,
  and is told apart from inventing a quotation entirely.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.agents.offering import (
    MAX_OFFERED_CHARS,
    Offered,
    Offering,
    QuoteVerdict,
    Rejection,
    around,
    offering_of,
    windows_of,
)
from praxis.domain.records import Span
from praxis.llm.synthesis import ordinals_in

from tests.agents.conftest import make_document, spans_of


class TestBuilding:
    def test_ordinals_start_at_zero_and_run_in_order(self, spans):
        listing = offering_of(spans)
        assert [entry.ordinal for entry in listing.entries] == list(range(len(spans)))

    def test_the_spans_come_back_in_the_order_they_were_offered(self, spans):
        assert offering_of(spans).spans == tuple(spans)

    def test_an_empty_offering_is_refused(self):
        # An agent shown nothing cannot cite anything, and a call that produces
        # no candidates for that reason is indistinguishable from one that read
        # the material and found none.
        with pytest.raises(ValueError, match="nothing to cite"):
            offering_of([])

    def test_an_offering_never_crosses_documents(self, spans):
        other = spans_of(make_document(doc_id="DOC-0002"))
        with pytest.raises(ValueError, match="one offering, one document"):
            offering_of([spans[0], other[0]])

    def test_the_offering_reports_its_document(self, spans):
        assert offering_of(spans).doc_id == spans[0].doc_id

    def test_ordinals_out_of_order_are_refused(self, spans):
        with pytest.raises(ValueError, match="ordinals must run"):
            Offering(entries=(Offered(ordinal=1, span=spans[0]),))


class TestRendering:
    def test_every_span_is_labelled_with_its_ordinal(self, offering):
        rendered = offering.render()
        for entry in offering.entries:
            assert f"[{entry.ordinal}]" in rendered

    def test_the_label_shape_matches_what_the_offline_provider_recognises(self, offering):
        # praxis.llm.synthesis draws its offline ordinals from bracketed labels
        # the prompt really presented. A different shape here would make every
        # offline answer cite a passage that was never offered, which is a
        # systematic failure rather than a realistic one.
        assert ordinals_in(offering.render()) == tuple(range(len(offering)))

    def test_a_long_span_is_elided_and_says_so(self):
        # The block grid admits whole tables and whole fenced code, and one of
        # those can be most of a window's tokens.
        long_document = make_document("x" * (MAX_OFFERED_CHARS + 500) + "\n")
        span = Span.covering(
            long_document,
            0,
            len(long_document.content_bytes),
            created_by="test",
            created_at=long_document.ingested_at,
        )
        rendered = offering_of([span]).render()
        assert "[...]" in rendered
        assert len(rendered) < len(span.text)

    def test_elision_does_not_change_the_span(self, document):
        span = Span.covering(
            document,
            0,
            len(document.content_bytes),
            created_by="test",
            created_at=document.ingested_at,
        )
        listing = offering_of([span])
        # The offsets are the whole span whatever was shown, so a claim about
        # the elided tail is refused rather than accepted against unread text.
        assert listing.span_for(0).end_byte == span.end_byte


class TestResolution:
    def test_an_offered_ordinal_resolves_to_the_span_it_names(self, offering):
        for entry in offering.entries:
            assert offering.resolve(entry.ordinal) == entry.span

    @pytest.mark.parametrize("ordinal", [-1, 999, 10_000])
    def test_an_ordinal_that_was_not_offered_is_refused(self, offering, ordinal):
        refused = offering.resolve(ordinal)
        assert isinstance(refused, Rejection)
        assert refused.ordinal == ordinal
        assert "were offered" in refused.reason

    def test_span_for_answers_none_rather_than_raising(self, offering):
        assert offering.span_for(len(offering)) is None

    @given(ordinal=st.integers(min_value=-10_000, max_value=10_000))
    def test_resolution_has_exactly_two_outcomes(self, ordinal):
        # The ADR 0015 property: an ordinal is either a span that was really
        # offered, or a refusal. There is no well-formed-but-fabricated target.
        listing = offering_of(spans_of(make_document()))
        resolved = listing.resolve(ordinal)
        assert isinstance(resolved, Span | Rejection)
        if isinstance(resolved, Span):
            assert resolved in listing.spans


class TestWhereAQuotationCameFrom:
    def test_a_quotation_from_the_cited_span_is_recognised(self, offering):
        entry = offering.entries[0]
        assert offering.where_quoted(entry.span.text, entry.span.id) is QuoteVerdict.IN_CITED_SPAN

    def test_a_quotation_from_another_offered_span_is_a_mis_attribution(self, offering):
        # The model read the material and pointed at the wrong part of it. A
        # different fault from inventing, and refused as a different one.
        quoted, cited = offering.entries[0], offering.entries[1]
        assert (
            offering.where_quoted(quoted.span.text, cited.span.id)
            is QuoteVerdict.IN_ANOTHER_OFFERED_SPAN
        )

    def test_a_quotation_in_none_of_the_offered_spans_is_nowhere(self, offering):
        assert (
            offering.where_quoted("we chose a carrier pigeon", offering.entries[0].span.id)
            is QuoteVerdict.NOWHERE
        )

    def test_an_empty_quotation_is_nowhere(self, offering):
        assert offering.where_quoted("   ", offering.entries[0].span.id) is QuoteVerdict.NOWHERE

    def test_a_rewrapped_quotation_still_counts(self, offering):
        # A sentence that wrapped across two lines is quoted back with a space.
        # That is a fact about rendering, and the same latitude VerifierAgent
        # gives; refusing it would train everyone to route around the gate.
        entry = offering.entries[0]
        rewrapped = "  ".join(entry.span.text.split())
        assert offering.where_quoted(rewrapped, entry.span.id) is QuoteVerdict.IN_CITED_SPAN

    def test_a_changed_word_is_not_whitespace(self, offering):
        entry = next(e for e in offering.entries if "OpenSearch" in e.span.text)
        altered = entry.span.text.replace("OpenSearch", "Elasticsearch")
        assert offering.where_quoted(altered, entry.span.id) is QuoteVerdict.NOWHERE


class TestWindows:
    def test_windows_cover_every_span_exactly_once(self, spans):
        seen = [span for window in windows_of(spans, 3) for span in window.spans]
        assert seen == list(spans)

    def test_a_window_is_no_wider_than_asked_for(self, spans):
        assert all(len(window) <= 3 for window in windows_of(spans, 3))

    def test_each_window_numbers_from_zero(self, spans):
        for window in windows_of(spans, 3):
            assert window.entries[0].ordinal == 0

    def test_a_window_of_one_is_allowed(self, spans):
        assert len(windows_of(spans, 1)) == len(spans)

    @pytest.mark.parametrize("size", [0, -1])
    def test_a_window_must_hold_something(self, spans, size):
        with pytest.raises(ValueError, match="at least one span"):
            windows_of(spans, size)


class TestContextAroundACandidate:
    def test_the_candidate_and_its_neighbours_are_offered(self, spans):
        listing = around(spans, 4, reach=1)
        assert listing.spans == tuple(spans[3:6])

    def test_reach_is_clamped_at_the_start(self, spans):
        assert around(spans, 0, reach=2).spans == tuple(spans[0:3])

    def test_reach_is_clamped_at_the_end(self, spans):
        last = len(spans) - 1
        assert around(spans, last, reach=2).spans == tuple(spans[last - 2 :])

    def test_reach_of_zero_offers_the_candidate_alone(self, spans):
        assert around(spans, 2, reach=0).spans == (spans[2],)

    def test_the_candidate_keeps_no_special_ordinal(self, spans):
        # Deliberate. An agent that has to pick the decision out of its
        # neighbours is being asked the question the live run asks.
        listing = around(spans, 4, reach=1)
        assert [entry.ordinal for entry in listing.entries] == [0, 1, 2]

    def test_a_negative_reach_is_refused(self, spans):
        with pytest.raises(ValueError, match="cannot be -1"):
            around(spans, 1, reach=-1)

    @pytest.mark.parametrize("index", [-1, 10_000])
    def test_an_index_outside_the_spans_is_refused(self, spans, index):
        with pytest.raises(ValueError, match="not a position"):
            around(spans, index, reach=1)
