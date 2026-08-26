"""Grading the three memory passes, over fixtures that each pose one ambiguity.

`tests/eval/test_harness.py` runs the real corpus end to end and asserts no
number. This file does the opposite: every store here is three records wide and
every assertion is about a number, because the questions worth asking are about
the *join* -- the store allocates its ids and the answer key allocates its own,
and nothing relates them but the passage both point at.

Three claims carry the file.

**A contradiction between two records the key does not name is not a false
positive.** The corpus labels the contradictions it planted, not every one that
could truthfully be asserted, so scoring an unlabelled pair against it would
report the corpus's silence as the detector's error.

**A pair has one identity in both vocabularies.** `contradicts` is symmetric and
the two sides order their pairs independently, so the key saying (B, A) and the
store saying (A, B) is one fact and has to count once.

**The verdict graded is the status on the record**, not the verdict the run
returned. A run reports what it concluded; the store holds what survived being
written, and the second is what anyone reads afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from praxis.agents.blocking import Blocking, Candidate
from praxis.agents.detection import DetectionRun
from praxis.agents.results import Contradiction, DetectionResult, Settlement
from praxis.corpus.groundtruth import (
    CorpusGroundTruth,
    DocumentGroundTruth,
    ExpectedLink,
    ExpectedVerdict,
    GroundTruthItem,
    ItemKind,
    MonitoringExpectation,
)
from praxis.domain.enums import AssumptionStatus, SourceKind
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Link, Span
from praxis.eval.matching import Claim, Match, Pairing
from praxis.eval.memory import (
    MemoryResult,
    expected_pairs,
    expected_verdicts,
    formalization_score,
    found_pairs,
    grade_memory,
    names_in,
    proposed_pairs,
    reached_verdicts,
)
from praxis.ingest.adapters import MARKDOWN_ADAPTER, document_from
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

from tests.monitor.conftest import ACTOR, AT, BODY, make_assumption

HASH = "0" * 64
"""One document's content hash. The join between key and store elsewhere; here
every claim is placed by hand, so it only has to be consistent."""


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


@pytest.fixture
def span(store: Repository) -> Span:
    """One real document and one span over it, so a foreign key has something to hold."""
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


def write(  # noqa: PLR0913 -- every field a test varies, named
    store: Repository,
    span: Span,
    record_id: str,
    *,
    predicate: str = "index_size_gb <= 50",
    expiry: str = 'on_event("the index is re-sharded")',
    status: AssumptionStatus = AssumptionStatus.UNVERIFIED,
) -> Assumption:
    """One assumption in the store, in whatever state a test needs.

    A status other than `UNVERIFIED` carries `last_evaluated_at`, because the
    schema refuses a verdict with no evaluation time -- one is indistinguishable
    from a monitor that never ran, and telling those apart is the field's whole
    value.
    """
    record = make_assumption(span, assumption_id=record_id, predicate=predicate, expiry=expiry)
    evaluated = None if status is AssumptionStatus.UNVERIFIED else AT
    return store.add(
        record.model_copy(update={"status": status, "last_evaluated_at": evaluated}),
        actor=ACTOR,
        reason="fixture",
    )


def contradicts(store: Repository, span: Span, left: str, right: str) -> Link:
    """A `contradicts` edge between two stored records."""
    return store.add(
        Link.between(
            LinkType.CONTRADICTS,
            left,
            right,
            rationale="the two predicates permit no common value",
            confidence=1.0,
            created_by=ACTOR,
            created_at=AT,
            span_id=span.id,
        ),
        actor=ACTOR,
        reason="fixture",
    )


def item(item_id: str, *, overturns: str | None = None) -> GroundTruthItem:
    """One assumption in the answer key, optionally overturning another."""
    return GroundTruthItem(
        item_id=item_id,
        kind=ItemKind.ASSUMPTION,
        start_byte=0,
        end_byte=4,
        quote="ABCD",
        links=(
            (ExpectedLink(link_type=LinkType.CONTRADICTS, target_item_id=overturns),)
            if overturns is not None
            else ()
        ),
    )


def key(*items: GroundTruthItem, verdicts: tuple[ExpectedVerdict, ...] = ()) -> CorpusGroundTruth:
    """An answer key holding one document and whatever items a test needs."""
    return CorpusGroundTruth(
        generator_version=1,
        seed=1,
        generated_at=AT,
        documents=(
            DocumentGroundTruth(
                path="adr.md",
                source_kind=SourceKind.MARKDOWN,
                content_sha256=HASH,
                byte_length=4,
                items=items,
            ),
        ),
        monitoring=MonitoringExpectation(verdicts=verdicts),
    )


def named(*pairs: tuple[str, str]) -> Pairing:
    """A pairing that names each record id as an item id, and nothing else.

    Built directly rather than through `praxis.eval.matching.pair`, because what
    is under test here is what the grading does with a join and not how the join
    was arrived at.
    """
    return Pairing(
        matched=tuple(
            Match(
                item=item(item_id),
                claim=Claim(
                    record_id=record_id,
                    kind=ItemKind.ASSUMPTION,
                    content_hash=HASH,
                    start_byte=0,
                    end_byte=4,
                    fields={},
                ),
                overlap=4,
                fields=(),
            )
            for record_id, item_id in pairs
        )
    )


def detection(
    *,
    arithmetic: tuple[tuple[str, str], ...] = (),
    model: tuple[tuple[str, str], ...] = (),
    candidates: tuple[tuple[str, str], ...] = (),
) -> DetectionRun:
    """A detection run reporting what each stage did, without running one."""
    found = tuple(
        Contradiction(
            link=Link.between(
                LinkType.CONTRADICTS,
                left,
                right,
                rationale="fixture",
                confidence=1.0,
                created_by=ACTOR,
                created_at=AT,
                span_id=None,
            ),
            settled_by=settlement,
            rationale="fixture",
        )
        for settlement, pairs in ((Settlement.ARITHMETIC, arithmetic), (Settlement.MODEL, model))
        for left, right in pairs
    )
    return DetectionRun(
        result=DetectionResult(
            contradictions=found,
            blocking=Blocking(
                candidates=tuple(
                    Candidate(left=left, right=right, shared=("subject:index_size_gb",))
                    for left, right in candidates
                ),
                indexed=2,
                formed=len(candidates),
            ),
        )
    )


class TestTheJoin:
    def test_a_record_is_named_by_the_item_it_was_paired_with(self):
        assert names_in(named(("A-0001", "I-1"))) == {"A-0001": "I-1"}

    def test_a_record_nothing_paired_is_not_named(self):
        assert names_in(Pairing()) == {}


class TestFormalization:
    def test_a_compiled_assumption_counts_as_checkable(self, store, span):
        write(store, span, "A-0001")
        result = formalization_score(store.list_all(Assumption))
        assert result.total == 1
        assert result.predicates_parsed == 1
        assert result.checkable == 1

    def test_prose_in_the_predicate_parses_as_nothing(self, store, span):
        write(store, span, "A-0001", predicate="the index stays small")
        result = formalization_score(store.list_all(Assumption))
        assert result.predicates_parsed == 0
        assert result.checkable == 0

    def test_a_predicate_that_parses_beside_a_prose_expiry_is_not_checkable(self, store, span):
        # Both halves have to parse: the monitor needs the predicate to reach a
        # verdict and the condition to know whether the verdict is stale.
        write(store, span, "A-0001", expiry="sometime next year")
        result = formalization_score(store.list_all(Assumption))
        assert result.predicates_parsed == 1
        assert result.checkable == 0

    def test_it_counts_every_assumption_and_not_only_the_paired_ones(self, store, span):
        # Whether a predicate parses is a property of the text. It needs no
        # answer-key entry to be true, which is also why it is not a recall.
        write(store, span, "A-0001")
        write(store, span, "A-0002", predicate="morale is fine")
        assert formalization_score(store.list_all(Assumption)).total == 2

    def test_an_empty_store_reports_the_stated_convention(self, store):
        result = formalization_score(store.list_all(Assumption))
        assert result.total == 0
        assert result.parse_rate == 1


class TestVerdicts:
    def test_the_status_graded_is_the_one_on_the_record(self, store, span):
        write(store, span, "A-0001", status=AssumptionStatus.BREACHED)
        reached = reached_verdicts(store.list_all(Assumption), {"A-0001": "I-1"})
        assert reached == {"I-1": AssumptionStatus.BREACHED}

    def test_a_record_the_key_cannot_name_is_left_out(self, store, span):
        # Not reported as UNVERIFIED: there is no item to report it against, and
        # inventing one would put a verdict in the matrix nothing expected.
        write(store, span, "A-0001", status=AssumptionStatus.BREACHED)
        assert reached_verdicts(store.list_all(Assumption), {}) == {}

    def test_the_expectations_come_off_the_key(self):
        truth = key(
            item("I-1"),
            verdicts=(ExpectedVerdict(item_id="I-1", status=AssumptionStatus.HOLDING),),
        )
        assert expected_verdicts(truth) == {"I-1": AssumptionStatus.HOLDING}

    def test_an_expectation_nothing_evaluated_is_counted_as_unverified(self, store, span):
        # What an assumption nothing measured really is. The alternative is
        # dropping it, and then a monitor that never ran would score perfectly.
        truth = key(
            item("I-1"),
            verdicts=(ExpectedVerdict(item_id="I-1", status=AssumptionStatus.BREACHED),),
        )
        result = grade_memory(store, truth, Pairing())
        assert result.monitoring.total == 1
        assert result.monitoring.agreed == 0

    def test_a_correct_verdict_agrees(self, store, span):
        write(store, span, "A-0001", status=AssumptionStatus.BREACHED)
        truth = key(
            item("I-1"),
            verdicts=(ExpectedVerdict(item_id="I-1", status=AssumptionStatus.BREACHED),),
        )
        result = grade_memory(store, truth, named(("A-0001", "I-1")))
        assert result.monitoring.accuracy == 1
        assert result.monitoring.aged_misreported_as_breached == 0

    def test_an_expired_assumption_reported_breached_is_named_as_such(self, store, span):
        # The number this phase exists to report. A monitor that cannot tell a
        # violated predicate from one that merely aged is one nobody can act on.
        write(store, span, "A-0001", status=AssumptionStatus.BREACHED)
        truth = key(
            item("I-1"),
            verdicts=(ExpectedVerdict(item_id="I-1", status=AssumptionStatus.EXPIRED),),
        )
        result = grade_memory(store, truth, named(("A-0001", "I-1")))
        assert result.monitoring.aged_misreported_as_breached == 1


class TestContradictionPairs:
    def test_a_planted_pair_is_read_off_the_key(self):
        truth = key(item("I-1"), item("I-2", overturns="I-1"))
        assert expected_pairs(truth) == frozenset({("I-1", "I-2")})

    def test_the_key_and_the_store_agree_about_a_pair_written_either_way(self, store, span):
        # `contradicts` is symmetric and the two sides order independently, so
        # one fact must have one identity or it would count twice.
        write(store, span, "A-0001")
        write(store, span, "A-0002")
        contradicts(store, span, "A-0002", "A-0001")
        truth = key(item("I-1"), item("I-2", overturns="I-1"))
        found, _ = found_pairs(store, {"A-0001": "I-1", "A-0002": "I-2"})
        assert found == expected_pairs(truth)

    def test_a_found_pair_scores_as_a_hit(self, store, span):
        write(store, span, "A-0001")
        write(store, span, "A-0002")
        contradicts(store, span, "A-0001", "A-0002")
        truth = key(item("I-1"), item("I-2", overturns="I-1"))
        result = grade_memory(store, truth, named(("A-0001", "I-1"), ("A-0002", "I-2")))
        assert result.contradictions.true_positives == 1
        assert result.contradictions.recall == 1

    def test_a_planted_pair_nothing_found_is_a_miss(self, store, span):
        write(store, span, "A-0001")
        write(store, span, "A-0002")
        truth = key(item("I-1"), item("I-2", overturns="I-1"))
        result = grade_memory(store, truth, named(("A-0001", "I-1"), ("A-0002", "I-2")))
        assert result.contradictions.false_negatives == 1
        assert result.contradictions.recall == 0

    def test_an_edge_between_records_the_key_does_not_name_is_not_a_false_positive(
        self, store, span
    ):
        # The claim this file exists for. The corpus labels the contradictions
        # it planted, so an unlabelled pair is unjudgeable rather than wrong.
        write(store, span, "A-0001")
        write(store, span, "A-0002")
        contradicts(store, span, "A-0001", "A-0002")
        result = grade_memory(store, key(item("I-1")), named(("A-0001", "I-1")))
        assert result.unnamed == 1
        assert result.contradictions.false_positives == 0

    def test_a_named_pair_the_key_did_not_plant_is_a_false_positive(self, store, span):
        # Both records are labelled and the key says nothing joins them, which
        # is a claim the key really made and this really contradicts.
        write(store, span, "A-0001")
        write(store, span, "A-0002")
        contradicts(store, span, "A-0001", "A-0002")
        truth = key(item("I-1"), item("I-2"))
        result = grade_memory(store, truth, named(("A-0001", "I-1"), ("A-0002", "I-2")))
        assert result.contradictions.false_positives == 1
        assert result.unnamed == 0


class TestTheStagesReportApart:
    def test_an_arithmetic_settlement_counts_in_its_own_row(self, store, span):
        write(store, span, "A-0001")
        write(store, span, "A-0002")
        contradicts(store, span, "A-0001", "A-0002")
        truth = key(item("I-1"), item("I-2", overturns="I-1"))
        result = grade_memory(
            store,
            truth,
            named(("A-0001", "I-1"), ("A-0002", "I-2")),
            detection=detection(arithmetic=(("A-0001", "A-0002"),)),
        )
        assert result.by_arithmetic.recall == 1
        assert result.by_model.recall == 0

    def test_a_model_judgement_counts_in_the_other(self, store, span):
        write(store, span, "A-0001")
        write(store, span, "A-0002")
        contradicts(store, span, "A-0001", "A-0002")
        truth = key(item("I-1"), item("I-2", overturns="I-1"))
        result = grade_memory(
            store,
            truth,
            named(("A-0001", "I-1"), ("A-0002", "I-2")),
            detection=detection(model=(("A-0001", "A-0002"),)),
        )
        assert result.by_model.recall == 1
        assert result.by_arithmetic.recall == 0

    def test_blocking_recall_is_the_ceiling_the_others_sit_under(self, store, span):
        # A pair blocking never proposed is a pair nothing will ever look at, so
        # a low detector recall means something different when this is low too.
        write(store, span, "A-0001")
        write(store, span, "A-0002")
        truth = key(item("I-1"), item("I-2", overturns="I-1"))
        result = grade_memory(
            store,
            truth,
            named(("A-0001", "I-1"), ("A-0002", "I-2")),
            detection=detection(candidates=(("A-0001", "A-0002"),)),
        )
        assert result.proposed == 1
        assert result.proposed_recall == 1
        assert result.contradictions.recall == 0

    def test_a_pair_blocking_never_proposed_reads_as_zero(self, store, span):
        write(store, span, "A-0001")
        write(store, span, "A-0002")
        truth = key(item("I-1"), item("I-2", overturns="I-1"))
        result = grade_memory(
            store, truth, named(("A-0001", "I-1"), ("A-0002", "I-2")), detection=detection()
        )
        assert result.proposed_recall == 0

    def test_without_a_detection_run_the_split_is_empty_rather_than_wrong(self, store, span):
        # The settlement and the candidates are the two things no store holds,
        # so a grading with no run must say nothing about them rather than guess.
        write(store, span, "A-0001")
        write(store, span, "A-0002")
        contradicts(store, span, "A-0001", "A-0002")
        truth = key(item("I-1"), item("I-2", overturns="I-1"))
        result = grade_memory(store, truth, named(("A-0001", "I-1"), ("A-0002", "I-2")))
        assert result.contradictions.true_positives == 1
        assert result.by_arithmetic.true_positives == 0
        assert result.by_model.true_positives == 0
        assert proposed_pairs(None, {}) == frozenset()


class TestConventions:
    def test_a_corpus_that_planted_nothing_reports_a_perfect_blocking_recall(self, store):
        # Recall with nothing expected is 1: there was nothing to find and
        # nothing was missed, and the corpus owns that denominator.
        result = grade_memory(store, key(item("I-1")), Pairing())
        assert result.expected == 0
        assert result.proposed_recall == 1

    def test_an_empty_grading_is_the_default_result(self, store):
        result = grade_memory(store, key(), Pairing())
        assert result == MemoryResult()

    def test_the_identified_count_is_the_denominator_a_reader_needs(self, store, span):
        write(store, span, "A-0001")
        result = grade_memory(store, key(item("I-1")), named(("A-0001", "I-1")))
        assert result.identified == 1
