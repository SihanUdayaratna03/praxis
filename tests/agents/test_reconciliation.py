"""The arithmetic half of outcome matching, with no provider anywhere in sight.

These are the tests that matter most in this phase, and they are deliberately in
a file that imports no `LLMProvider`. `quality_for` decides `match_quality`,
which is a field a model would happily fill in and invariant 3 says it must not
-- so the function lives in a module that cannot reach a model, and it is tested
here the way every other piece of arithmetic in this repository is tested: by
example, by property, and against this project's own history.

Five of the six hand-scored dogfood outcomes are reproduced. The sixth is
asserted as a *difference* rather than fixed, because it was scored on failed
conditions rather than on the ratio, and widening a band to make it agree would
be tuning the measurement to flatter the measurer.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.agents.classifier import UNCLASSIFIED
from praxis.agents.reconciliation import (
    CLOSE_BELOW,
    EXACT_BELOW,
    PARTIAL_BELOW,
    WORKING_DAY_HOURS,
    WORKING_WEEK_DAYS,
    candidates_for,
    converted,
    quality_for,
)
from praxis.domain.enums import MatchQuality, Unit
from praxis.domain.ids import EstimateId
from praxis.domain.records import Document, Estimate, Span

from tests.agents.conftest import AT, make_document, spans_of

STATUS_BODY = """# Discovery status — week 9

## What shipped

The product search index migration is live in production.

## How it went

Nadeesha put the search index migration at 4 weeks of hands-on work.

The search index migration actually took 7 weeks of hands-on work, Nadeesha
confirmed, with no time lost waiting on anybody.

Separately, the billing outbox rollout came in at 3 weeks against the 3 we
sized it at.

## Next up

Nothing decided about hosted search yet.
"""

ACTUAL_QUOTE = "The search index migration actually took 7 weeks of hands-on work"

DOGFOOD = [
    ("OUT-0001", "2.5", "1.1", MatchQuality.PARTIAL),
    ("OUT-0003", "4.5", "5.6", MatchQuality.CLOSE),
    ("OUT-0004", "5.5", "4.2", MatchQuality.CLOSE),
    ("OUT-0005", "6.0", "2.75", MatchQuality.PARTIAL),
    ("OUT-0006", "6.5", "4.6", MatchQuality.CLOSE),
]
"""Five of this project's own outcomes, and the band a hand scored them at.

`OUT-0002` is deliberately absent and has a test of its own: it is the one the
arithmetic disagrees with, and folding it in here would hide that.
"""


@pytest.fixture
def status() -> Document:
    return make_document(STATUS_BODY, doc_id="DOC-0009")


@pytest.fixture
def status_spans(status: Document) -> tuple[Span, ...]:
    return spans_of(status)


def an_estimate(
    spans: tuple[Span, ...],
    *,
    active: str = "4",
    unit: Unit = Unit.WEEKS,
    subject: str = "the search index migration",
) -> Estimate:
    """An estimate as the extractor leaves one, citing a passage really there."""
    cited = next(span for span in spans if "put the search index migration" in span.text)
    return Estimate(
        id=EstimateId("EST-0001"),
        subject=subject,
        owner="Nadeesha",
        work_class=UNCLASSIFIED,
        active_quantity=Decimal(active),
        blocked_quantity=Decimal(0),
        unit=unit,
        confidence=0.7,
        estimated_at=AT,
        span_id=cited.id,
        created_by="EstimateExtractor",
        created_at=AT,
    )


class TestCandidates:
    """Deterministic selection. A pair lost here is lost measurably."""

    def test_offers_the_passage_reporting_the_actual(self, status_spans: tuple[Span, ...]) -> None:
        chosen = candidates_for(an_estimate(status_spans), status_spans)

        assert any(ACTUAL_QUOTE in span.text for span in chosen)

    def test_always_offers_the_span_the_estimate_itself_was_read_from(
        self, status_spans: tuple[Span, ...]
    ) -> None:
        """A status update states both in one passage often enough to matter."""
        estimate = an_estimate(status_spans)

        chosen = candidates_for(estimate, status_spans, min_shared_words=99)

        assert [span.id for span in chosen] == [estimate.span_id]

    def test_is_stable_across_runs(self, status_spans: tuple[Span, ...]) -> None:
        estimate = an_estimate(status_spans)

        assert candidates_for(estimate, status_spans) == candidates_for(estimate, status_spans)

    def test_keeps_document_order(self, status_spans: tuple[Span, ...]) -> None:
        chosen = candidates_for(an_estimate(status_spans), status_spans)

        positions = [status_spans.index(span) for span in chosen]
        assert positions == sorted(positions)

    def test_never_offers_more_than_the_ceiling(self, status_spans: tuple[Span, ...]) -> None:
        chosen = candidates_for(
            an_estimate(status_spans), status_spans, limit=2, min_shared_words=0
        )

        assert len(chosen) == 2

    def test_a_subject_sharing_no_vocabulary_offers_nothing(
        self, status_spans: tuple[Span, ...]
    ) -> None:
        """Reported as `NO_CANDIDATES` rather than blamed on the model afterwards."""
        estimate = an_estimate(status_spans, subject="quantum refrigeration")
        stranger = estimate.model_copy(
            update={"owner": "Zzyzx", "span_id": "SPAN-000000000000dead"}
        )

        assert candidates_for(stranger, status_spans, min_shared_words=1) == ()


class TestConversion:
    """Converted at a stated convention, or refused. Never fudged."""

    def test_the_same_unit_is_unchanged(self) -> None:
        assert converted(Decimal(7), Unit.WEEKS, Unit.WEEKS) == Decimal(7)

    @pytest.mark.parametrize(
        ("quantity", "source", "target", "expected"),
        [
            (Decimal(40), Unit.HOURS, Unit.WEEKS, Decimal(1)),
            (Decimal(1), Unit.WEEKS, Unit.HOURS, Decimal(40)),
            (Decimal(1), Unit.WEEKS, Unit.DAYS, Decimal(5)),
            (Decimal(16), Unit.HOURS, Unit.DAYS, Decimal(2)),
        ],
    )
    def test_time_converts_at_the_recorded_convention(
        self, quantity: Decimal, source: Unit, target: Unit, expected: Decimal
    ) -> None:
        assert converted(quantity, source, target) == expected

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (Unit.POINTS, Unit.WEEKS),
            (Unit.WEEKS, Unit.POINTS),
            (Unit.USD, Unit.HOURS),
            (Unit.COUNT, Unit.DAYS),
            (Unit.POINTS, Unit.COUNT),
        ],
    )
    def test_across_families_there_is_no_honest_rate(self, source: Unit, target: Unit) -> None:
        """Points are a team's own scale and usd needs a labour rate nobody stated."""
        assert converted(Decimal(8), source, target) is None

    @given(
        st.decimals(min_value=0, max_value=10_000, allow_nan=False, allow_infinity=False, places=2),
        st.sampled_from([Unit.HOURS, Unit.DAYS, Unit.WEEKS]),
        st.sampled_from([Unit.HOURS, Unit.DAYS, Unit.WEEKS]),
    )
    def test_converting_there_and_back_is_the_original(
        self, quantity: Decimal, source: Unit, target: Unit
    ) -> None:
        """The rates are exact ratios, so a round trip must not drift."""
        there = converted(quantity, source, target)
        assert there is not None
        back = converted(there, target, source)
        assert back == quantity

    def test_the_convention_is_stated_rather_than_implied(self) -> None:
        assert Decimal(8) == WORKING_DAY_HOURS
        assert Decimal(5) == WORKING_WEEK_DAYS


class TestQuality:
    """The band is arithmetic and only arithmetic."""

    @pytest.mark.parametrize(
        ("estimated", "actual", "expected"),
        [
            ("4", "4", MatchQuality.EXACT),
            ("4", "4.3", MatchQuality.EXACT),
            ("4", "5", MatchQuality.CLOSE),
            ("4", "7", MatchQuality.PARTIAL),
            ("4", "12", MatchQuality.MISS),
            ("12", "4", MatchQuality.MISS),
        ],
    )
    def test_bands_by_ratio(self, estimated: str, actual: str, expected: MatchQuality) -> None:
        quality, _ = quality_for(Decimal(estimated), Decimal(actual))
        assert quality is expected

    @given(
        st.decimals(min_value=1, max_value=1000, allow_nan=False, allow_infinity=False, places=2),
        st.decimals(min_value=1, max_value=1000, allow_nan=False, allow_infinity=False, places=2),
    )
    def test_is_symmetric(self, estimated: Decimal, actual: Decimal) -> None:
        """Over by 2x and under by 2x are the same distance. Direction is a separate question."""
        assert quality_for(estimated, actual)[0] is quality_for(actual, estimated)[0]

    @given(
        st.decimals(min_value=0, max_value=1000, allow_nan=False, allow_infinity=False, places=2),
        st.decimals(min_value=0, max_value=1000, allow_nan=False, allow_infinity=False, places=2),
    )
    def test_always_produces_a_band_the_record_accepts(
        self, estimated: Decimal, actual: Decimal
    ) -> None:
        """Total, like every other piece of arithmetic in this repository."""
        quality, ratio = quality_for(estimated, actual)
        assert quality in set(MatchQuality)
        assert quality is not MatchQuality.UNRESOLVED
        assert ratio is None or ratio >= 1

    def test_a_zero_against_a_number_is_a_miss_with_no_ratio(self) -> None:
        """Not "infinitely wrong": that is a ratio nobody could have computed."""
        assert quality_for(Decimal(0), Decimal(4)) == (MatchQuality.MISS, None)
        assert quality_for(Decimal(4), Decimal(0)) == (MatchQuality.MISS, None)

    def test_two_zeros_agree_exactly(self) -> None:
        assert quality_for(Decimal(0), Decimal(0)) == (MatchQuality.EXACT, None)

    def test_the_bands_are_ordered(self) -> None:
        assert EXACT_BELOW < CLOSE_BELOW < PARTIAL_BELOW

    @pytest.mark.parametrize(("name", "estimated", "actual", "hand"), DOGFOOD)
    def test_reproduces_this_projects_own_hand_scored_outcomes(
        self, name: str, estimated: str, actual: str, hand: MatchQuality
    ) -> None:
        quality, _ = quality_for(Decimal(estimated), Decimal(actual))
        assert quality is hand, name

    def test_out_0002_is_a_known_and_deliberate_disagreement(self) -> None:
        """The one row the arithmetic does not reproduce, asserted as a difference.

        `OUT-0002` predicted 2.0h of active work against 4.2h spent -- a ratio of
        2.10, which lands in `partial`. It was hand-scored `miss` because the
        estimate's stated *conditions* also failed, which is a judgement this
        function cannot make and must not pretend to. Pinned here so that
        widening a band to make it agree would fail rather than pass quietly.
        """
        quality, ratio = quality_for(Decimal("2.0"), Decimal("4.2"))

        assert quality is MatchQuality.PARTIAL
        assert quality is not MatchQuality.MISS
        assert ratio is not None
        assert Decimal("2.09") < ratio < Decimal("2.11")
