"""Grading Half B, over fixtures that each pose one ambiguity.

`tests/eval/test_harness.py` runs the real corpus end to end and asserts no
number. This file does the opposite: every store here is a handful of records
wide and every assertion is about a number, because the questions worth asking
are about the *join* and about which denominator a rate is over.

Three claims carry the file.

**A classified rate and a class accuracy are different claims, and the higher
one is not the better one.** An agent that classifies everything wrongly scores
1.0 on the first and 0.0 on the second, and the tests hold both numbers at once
so that a change moving them together would fail.

**An outcome against an estimate the key does not name is not a false
positive.** The corpus labels the resolutions it planted, not every one that
could truthfully be asserted, so scoring an unlabelled pairing against it would
report the corpus's silence as the matcher's error.

**The match rate is over every estimate asked about, and the unresolved rows
are in the denominator.** That is the whole point of writing an `unresolved`
outcome rather than nothing: two matches out of two and two out of forty are
different facts, and only one of them is good news.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from praxis.agents.classifier import UNCLASSIFIED
from praxis.agents.estimation import EstimationRun
from praxis.agents.matcher import Unmatched, UnmatchedEstimate
from praxis.corpus.generator import GENERATOR_VERSION
from praxis.corpus.groundtruth import (
    Comparison,
    CorpusGroundTruth,
    DocumentGroundTruth,
    ExpectedField,
    GroundTruthItem,
    ItemKind,
)
from praxis.domain.enums import MatchQuality, SourceKind, Unit
from praxis.domain.ids import EstimateId, OutcomeId
from praxis.domain.records import Estimate, Outcome, Span
from praxis.eval.estimation import (
    EstimationResult,
    classification_score,
    expected_classes,
    expected_resolutions,
    grade_estimation,
    match_score,
    names_in,
    resolved_in,
    unclassified_in,
)
from praxis.eval.matching import Claim, Match, Pairing
from praxis.ingest.adapters import MARKDOWN_ADAPTER, document_from
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

AT = datetime(2026, 8, 22, 9, 0, tzinfo=UTC)
ACTOR = "test-suite"
HASH = "0" * 64

BODY = """\
# Discovery status

Nadeesha put the search index migration at 4 weeks of hands-on work.

It actually took 7 weeks of hands-on work.
"""

BODY_BYTES = len(BODY.encode("utf-8"))

ESTIMATE_QUOTE = "Nadeesha put the search index migration at 4 weeks of hands-on work."
ACTUAL_QUOTE = "It actually took 7 weeks of hands-on work."


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


@pytest.fixture
def span(store: Repository) -> Span:
    """One real document and one span, so a foreign key has something to hold."""
    source = MARKDOWN_ADAPTER.normalise(BODY.encode("utf-8"), source_uri="status.md")
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


def write_estimate(
    store: Repository, span: Span, record_id: str, *, work_class: str = UNCLASSIFIED
) -> Estimate:
    """One estimate in the store, in whatever class a test needs."""
    return store.add(
        Estimate(
            id=EstimateId(record_id),
            subject="the search index migration",
            owner="Nadeesha",
            work_class=work_class,
            active_quantity=Decimal(4),
            blocked_quantity=Decimal(0),
            unit=Unit.WEEKS,
            confidence=0.7,
            estimated_at=AT,
            span_id=span.id,
            created_by="EstimateExtractor",
            created_at=AT,
        ),
        actor=ACTOR,
        reason="fixture",
    )


def write_outcome(
    store: Repository, record_id: str, estimate: Estimate, *, resolved: bool = True
) -> Outcome:
    """The row standing against an estimate -- resolved, or explicitly not."""
    return store.add(
        Outcome(
            id=OutcomeId(record_id),
            estimate_id=estimate.id,
            active_quantity=Decimal(7) if resolved else None,
            blocked_quantity=Decimal(0) if resolved else None,
            unit=Unit.WEEKS,
            match_quality=MatchQuality.PARTIAL if resolved else MatchQuality.UNRESOLVED,
            resolved_at=AT if resolved else None,
            notes="fixture",
            span_id=span_id_of(estimate) if resolved else None,
            created_by="OutcomeMatcher",
            created_at=AT,
        ),
        actor=ACTOR,
        reason="fixture",
    )


def span_id_of(estimate: Estimate) -> str:
    """A resolved outcome cites a span; the fixture reuses the estimate's."""
    return estimate.span_id


def key_item(item_id: str, *, work_class: str = "migration") -> GroundTruthItem:
    """One estimate in the answer key, with the class it should carry.

    Offsets are derived from the quotation rather than written down, because
    `GroundTruthItem` checks that the two agree -- the same redundancy `Span`
    carries, and it catches a fixture that has drifted from its own text.
    """
    return GroundTruthItem(
        item_id=item_id,
        kind=ItemKind.ESTIMATE,
        start_byte=0,
        end_byte=len(ESTIMATE_QUOTE.encode("utf-8")),
        quote=ESTIMATE_QUOTE,
        fields=(ExpectedField(name="work_class", value=work_class, comparison=Comparison.EXACT),),
    )


def key_outcome(item_id: str, resolves: str) -> GroundTruthItem:
    """One outcome in the answer key, naming the estimate it resolves."""
    start = len(ESTIMATE_QUOTE.encode("utf-8")) + 1
    return GroundTruthItem(
        item_id=item_id,
        kind=ItemKind.OUTCOME,
        start_byte=start,
        end_byte=start + len(ACTUAL_QUOTE.encode("utf-8")),
        quote=ACTUAL_QUOTE,
        resolves_item_id=resolves,
    )


def a_key(*items: GroundTruthItem) -> CorpusGroundTruth:
    """An answer key holding exactly these items."""
    return CorpusGroundTruth(
        generator_version=GENERATOR_VERSION,
        seed=1,
        generated_at=AT,
        documents=(
            DocumentGroundTruth(
                path="documents/status.md",
                source_kind=SourceKind.MARKDOWN,
                content_sha256=HASH,
                byte_length=BODY_BYTES,
                items=items,
            ),
        ),
    )


def paired(record_id: str, item: GroundTruthItem) -> Match:
    """One record matched to one key item, which is the join everything uses."""
    return Match(
        claim=Claim(
            record_id=record_id,
            kind=ItemKind.ESTIMATE,
            content_hash=HASH,
            start_byte=item.start_byte,
            end_byte=item.end_byte,
            fields={},
        ),
        item=item,
        overlap=item.end_byte - item.start_byte,
        fields=(),
    )


class TestClassification:
    """Two rates that are easy to confuse and mean opposite things."""

    def test_an_unclassified_store_is_on_the_axis_nowhere(
        self, store: Repository, span: Span
    ) -> None:
        write_estimate(store, span, "EST-0001")

        score = classification_score(store.list_all(Estimate), {}, {})

        assert score.total == 1
        assert score.classified == 0
        assert score.classified_rate == Decimal(0)

    def test_a_classified_store_is(self, store: Repository, span: Span) -> None:
        write_estimate(store, span, "EST-0001", work_class="migration")

        score = classification_score(store.list_all(Estimate), {}, {})

        assert score.classified == 1
        assert score.classified_rate == Decimal(1)

    def test_classifying_everything_wrongly_scores_one_and_zero(
        self, store: Repository, span: Span
    ) -> None:
        """The number this pair of rates exists to keep visible."""
        write_estimate(store, span, "EST-0001", work_class="backend")
        item = key_item("GT-0001", work_class="migration")

        score = classification_score(
            store.list_all(Estimate), {"EST-0001": "GT-0001"}, expected_classes(a_key(item))
        )

        assert score.classified_rate == Decimal(1)
        assert score.accuracy == Decimal(0)

    def test_classifying_nothing_scores_zero_and_zero(self, store: Repository, span: Span) -> None:
        """The safer failure, and the tests hold both numbers so it reads as one."""
        write_estimate(store, span, "EST-0001")
        item = key_item("GT-0001")

        score = classification_score(
            store.list_all(Estimate), {"EST-0001": "GT-0001"}, expected_classes(a_key(item))
        )

        assert score.classified_rate == Decimal(0)
        assert score.accuracy == Decimal(0)

    def test_the_right_class_agrees(self, store: Repository, span: Span) -> None:
        write_estimate(store, span, "EST-0001", work_class="migration")
        item = key_item("GT-0001", work_class="migration")

        score = classification_score(
            store.list_all(Estimate), {"EST-0001": "GT-0001"}, expected_classes(a_key(item))
        )

        assert score.agreed == 1
        assert score.accuracy == Decimal(1)

    def test_the_accuracy_denominator_is_what_the_key_could_name(
        self, store: Repository, span: Span
    ) -> None:
        """An accuracy over two estimates and over twenty are not the same evidence."""
        write_estimate(store, span, "EST-0001", work_class="migration")
        write_estimate(store, span, "EST-0002", work_class="migration")
        item = key_item("GT-0001", work_class="migration")

        score = classification_score(
            store.list_all(Estimate), {"EST-0001": "GT-0001"}, expected_classes(a_key(item))
        )

        assert score.total == 2
        assert score.identified == 1
        assert score.accuracy == Decimal(1)

    def test_a_vocabulary_invented_wholesale_is_visible_as_a_number(
        self, store: Repository, span: Span
    ) -> None:
        """Classifying everything and grouping nothing is invisible per row."""
        for index, name in enumerate(("alpha", "beta", "gamma"), start=1):
            write_estimate(store, span, f"EST-000{index}", work_class=name)
        item = key_item("GT-0001", work_class="migration")

        score = classification_score(store.list_all(Estimate), {}, expected_classes(a_key(item)))

        assert score.classified_rate == Decimal(1)
        assert score.vocabulary == 3
        assert score.proposed == 3

    def test_unclassified_rows_are_countable_on_their_own(
        self, store: Repository, span: Span
    ) -> None:
        write_estimate(store, span, "EST-0001")
        write_estimate(store, span, "EST-0002", work_class="migration")

        assert unclassified_in(store.list_all(Estimate)) == 1


class TestMatching:
    """The rate, its denominator, and why a pairing was lost."""

    def test_the_denominator_is_every_estimate_asked_about(
        self, store: Repository, span: Span
    ) -> None:
        first = write_estimate(store, span, "EST-0001")
        second = write_estimate(store, span, "EST-0002")
        write_outcome(store, "OUT-0001", first)
        write_outcome(store, "OUT-0002", second, resolved=False)

        score = match_score(store.list_all(Outcome), None)

        assert score.asked == 2
        assert score.resolved == 1
        assert score.unresolved == 1
        assert score.match_rate == Decimal("0.5")

    def test_an_empty_store_matched_nothing_rather_than_everything(self) -> None:
        """A starved pipeline must not sit at the top of the table."""
        score = match_score((), None)

        assert score.asked == 0
        assert score.match_rate == Decimal(0)

    def test_the_split_of_why_comes_from_the_run(self, store: Repository, span: Span) -> None:
        """The outcome row is identical whichever stage lost the pairing."""
        estimate = write_estimate(store, span, "EST-0001")
        write_outcome(store, "OUT-0001", estimate, resolved=False)
        run = EstimationRun(
            unmatched=(
                UnmatchedEstimate(
                    outcome=store.require(Outcome, "OUT-0001"),
                    estimate=estimate,
                    reason=Unmatched.MODEL_FOUND_NONE,
                    detail="no passage reports an actual",
                ),
            )
        )

        score = match_score(store.list_all(Outcome), run)

        assert score.by_reason == {"model_found_none": 1}

    def test_no_run_reports_no_split_rather_than_zeroes(
        self, store: Repository, span: Span
    ) -> None:
        """ "The causes were not reported" and "nothing was lost" differ."""
        estimate = write_estimate(store, span, "EST-0001")
        write_outcome(store, "OUT-0001", estimate, resolved=False)

        assert match_score(store.list_all(Outcome), None).by_reason == {}


class TestResolution:
    """Which estimates were resolved, against which the key says have an actual."""

    def test_resolving_the_estimate_the_key_names_is_a_hit(
        self, store: Repository, span: Span
    ) -> None:
        estimate = write_estimate(store, span, "EST-0001")
        write_outcome(store, "OUT-0001", estimate)

        found = resolved_in(store.list_all(Outcome), {"EST-0001": "GT-0001"})

        assert found == {"GT-0001"}

    def test_an_unresolved_outcome_is_not_a_resolution(self, store: Repository, span: Span) -> None:
        estimate = write_estimate(store, span, "EST-0001")
        write_outcome(store, "OUT-0001", estimate, resolved=False)

        assert resolved_in(store.list_all(Outcome), {"EST-0001": "GT-0001"}) == frozenset()

    def test_an_outcome_the_key_cannot_name_is_left_out_rather_than_scored(
        self, store: Repository, span: Span
    ) -> None:
        """The corpus labels the resolutions it planted, not every true one."""
        estimate = write_estimate(store, span, "EST-0001")
        write_outcome(store, "OUT-0001", estimate)

        assert resolved_in(store.list_all(Outcome), {}) == frozenset()

    def test_the_expectation_is_keyed_on_the_estimate(self) -> None:
        """What the store can be asked about: does this estimate have an actual."""
        truth = a_key(key_item("GT-0001"), key_outcome("GT-0002", "GT-0001"))

        assert expected_resolutions(truth) == {"GT-0001"}

    def test_an_outcome_resolving_nothing_is_not_an_expectation(self) -> None:
        truth = a_key(key_item("GT-0001"))

        assert expected_resolutions(truth) == frozenset()


class TestGrading:
    """The whole thing, over one small store."""

    def test_grades_both_halves_against_the_key(self, store: Repository, span: Span) -> None:
        estimate = write_estimate(store, span, "EST-0001", work_class="migration")
        write_outcome(store, "OUT-0001", estimate)
        item = key_item("GT-0001", work_class="migration")
        truth = a_key(item, key_outcome("GT-0002", "GT-0001"))
        pairing = Pairing(matched=(paired("EST-0001", item),), missed=(), spurious=())

        result = grade_estimation(store, truth, pairing)

        assert result.classification.accuracy == Decimal(1)
        assert result.matching.match_rate == Decimal(1)
        assert result.resolution.recall == Decimal(1)
        assert result.expected_resolutions == 1
        assert result.identified == 1

    def test_a_missed_resolution_is_a_false_negative(self, store: Repository, span: Span) -> None:
        estimate = write_estimate(store, span, "EST-0001", work_class="migration")
        write_outcome(store, "OUT-0001", estimate, resolved=False)
        item = key_item("GT-0001", work_class="migration")
        truth = a_key(item, key_outcome("GT-0002", "GT-0001"))
        pairing = Pairing(matched=(paired("EST-0001", item),), missed=(), spurious=())

        result = grade_estimation(store, truth, pairing)

        assert result.resolution.false_negatives == 1
        assert result.resolution.recall == Decimal(0)

    def test_an_empty_store_grades_to_zeroes_without_failing(self, store: Repository) -> None:
        truth = a_key()
        pairing = Pairing(matched=(), missed=(), spurious=())

        result = grade_estimation(store, truth, pairing)

        assert result == EstimationResult(
            classification=result.classification,
            matching=result.matching,
            resolution=result.resolution,
        )
        assert result.identified == 0
        assert result.expected_resolutions == 0

    def test_the_join_runs_through_the_pairing_and_nothing_else(self) -> None:
        """Store ids and key ids are allocated independently."""
        item = key_item("GT-0001")
        pairing = Pairing(matched=(paired("EST-0001", item),), missed=(), spurious=())

        assert names_in(pairing) == {"EST-0001": "GT-0001"}
