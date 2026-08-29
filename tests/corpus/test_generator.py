"""A corpus whose answers are known by construction, checked as if they were not.

Two families of claim. One is that generation is *reproducible* -- same seed,
same bytes, including the dates inside the documents -- because a corpus that
changed overnight would move every metric recorded against it for a reason that
is not a change. The other is that the answer key really describes the
documents: not by trusting the generator, but by ingesting what it wrote and
building a real `Span` over every ground-truth range.

That second family is the one worth having. It closes the loop between this
package and the span machinery: if the key and the adapter ever disagree about
where a byte is -- which is a live possibility for JSON, where the file and the
content are different text -- these fail.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from praxis.corpus.generator import (
    DEFAULT_DOCUMENTS,
    DEFAULT_REVISIONS,
    GENERATOR_VERSION,
    generate_corpus,
)
from praxis.corpus.groundtruth import (
    FORMAT_VERSION,
    GROUND_TRUTH_FILENAME,
    ExpectedVerdict,
    ItemKind,
    load_ground_truth,
    verify_corpus,
    write_ground_truth,
)
from praxis.domain.enums import AssumptionStatus, SourceKind
from praxis.domain.ids import DocumentId
from praxis.domain.links import LinkType
from praxis.domain.records import Span
from praxis.domain.spans import verify_span
from praxis.ingest.adapters import document_from, read_source
from praxis.ingest.blocks import blocks_of
from praxis.predicates.ast import Truth
from praxis.predicates.evaluator import evaluate
from praxis.predicates.intervals import conflict, constraints_of
from praxis.predicates.parser import parse
from praxis.predicates.world import WorldState

AT = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "corpus"
    generate_corpus(root, seed=20260809, generated_at=AT)
    return root


def files_in(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*.*"))
    }


# -- reproducibility ----------------------------------------------------------


def test_the_same_seed_produces_the_same_corpus_byte_for_byte(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    generate_corpus(first, documents=8, seed=11, generated_at=AT)
    generate_corpus(second, documents=8, seed=11, generated_at=AT)

    assert files_in(first) == files_in(second)


def test_regenerating_over_an_existing_corpus_leaves_it_identical(corpus):
    before = files_in(corpus)

    generate_corpus(corpus, seed=20260809, generated_at=AT)

    assert files_in(corpus) == before


def test_a_different_seed_produces_a_different_corpus(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    generate_corpus(first, documents=8, seed=11, generated_at=AT)
    generate_corpus(second, documents=8, seed=12, generated_at=AT)

    assert files_in(first) != files_in(second)


def test_the_dates_inside_documents_do_not_come_from_the_clock(corpus):
    """A corpus generated tomorrow must equal one generated today, so the
    documents are dated from a fixed epoch."""
    text = (corpus / load_ground_truth(corpus).documents[0].path).read_text(encoding="utf-8")

    assert "2026-01-05" in text
    assert datetime.now(UTC).date().isoformat() not in text


def test_the_key_records_what_generated_it(corpus):
    truth = load_ground_truth(corpus)

    assert truth.generator_version == GENERATOR_VERSION
    assert truth.seed == 20260809
    assert truth.generated_at == AT


# -- the key describes the documents ------------------------------------------


def test_every_ground_truth_range_is_a_span_that_resolves(corpus):
    """The loop closed: what the key says is where it says it, checked with
    the same function `VerifierAgent` uses on an extraction.

    For a JSON source the file and the content are different text, so this is
    the assertion that would fail first if the key were written in the wrong
    coordinate system.
    """
    truth = load_ground_truth(corpus)

    for index, entry in enumerate(truth.documents):
        source = read_source(corpus / entry.path)
        document = document_from(source, doc_id=DocumentId(f"DOC-{index + 1:04d}"), ingested_at=AT)
        assert document.content_hash == entry.content_sha256
        assert document.byte_length == entry.byte_length
        for item in entry.items:
            span = Span.covering(
                document, item.start_byte, item.end_byte, created_by="test", created_at=AT
            )
            assert verify_span(span, document).ok
            assert span.text == item.quote


def test_every_ground_truth_range_is_a_whole_block_of_the_grid(corpus):
    """The key is written in the same units the segmenter works in.

    Not required for grading -- byte overlap would work either way -- and worth
    holding on to: it means a perfect segmentation produces spans that line up
    with the answers exactly, so a citation score of less than one is about the
    extractor rather than about the grid.
    """
    truth = load_ground_truth(corpus)

    for entry in truth.documents:
        content = read_source(corpus / entry.path).text
        ranges = {(block.start_byte, block.end_byte) for block in blocks_of(content)}
        for item in entry.items:
            assert (item.start_byte, item.end_byte) in ranges, f"{item.item_id} is not one block"


def test_the_documents_are_named_in_the_key_and_present_on_disk(corpus):
    truth = load_ground_truth(corpus)

    # The main pass plus the revision notes, which are a second pass rather
    # than a fifth template -- a revision is a reply to a document that already
    # exists, so it cannot be scheduled alongside the thing it replies to.
    assert len(truth.documents) == DEFAULT_DOCUMENTS + DEFAULT_REVISIONS
    for entry in truth.documents:
        assert (corpus / entry.path).is_file()


def test_a_generated_corpus_verifies_against_itself(corpus):
    assert verify_corpus(corpus) == ()


# -- what the corpus contains -------------------------------------------------


def test_every_kind_a_grader_scores_is_present(corpus):
    counts = load_ground_truth(corpus).counts()

    assert all(counts[kind] > 0 for kind in ItemKind), counts


def test_all_three_source_kinds_are_represented(corpus):
    kinds = {entry.source_kind for entry in load_ground_truth(corpus).documents}

    assert kinds == set(SourceKind)


def test_the_corpus_carries_labelled_negatives(corpus):
    """Without them only recall is measurable, and a harness that measures
    recall alone rewards an agent that extracts every sentence."""
    items = load_ground_truth(corpus).items
    distractors = [item for item in items if item.is_distractor]

    assert len(distractors) >= len(load_ground_truth(corpus).documents)
    assert all(not item.fields and not item.links for item in distractors)


def test_the_fusion_relationship_is_in_the_corpus_and_labelled(corpus):
    """The product's central claim, planted in the answer key before the agent
    that has to find it exists: an assumption that is a quantified
    forward-looking claim, pointing at the estimate it really is."""
    truth = load_ground_truth(corpus)
    fused = [
        (item, link)
        for item in truth.items
        for link in item.links
        if link.link_type is LinkType.ESTIMATED_AS
    ]

    assert fused
    for item, link in fused:
        assert item.kind is ItemKind.ASSUMPTION
        target = truth.item(link.target_item_id)
        assert target is not None and target.kind is ItemKind.ESTIMATE


def test_a_decision_points_at_the_assumptions_underneath_it(corpus):
    truth = load_ground_truth(corpus)
    decisions = [item for item in truth.items if item.kind is ItemKind.DECISION and item.links]

    assert decisions
    for decision in decisions:
        for link in decision.links:
            assert link.link_type is LinkType.ASSUMES
            target = truth.item(link.target_item_id)
            assert target is not None and target.kind is ItemKind.ASSUMPTION


def test_an_outcome_names_the_estimate_it_resolves(corpus):
    truth = load_ground_truth(corpus)
    outcomes = [item for item in truth.items if item.kind is ItemKind.OUTCOME]

    assert outcomes
    for outcome in outcomes:
        resolved = truth.item(outcome.resolves_item_id or "")
        assert resolved is not None and resolved.kind is ItemKind.ESTIMATE


def test_estimates_miss_in_both_directions(corpus):
    """A corpus where every estimate ran long would let a calibration agent
    score well by always answering "over"."""
    truth = load_ground_truth(corpus)
    pairs = [
        (
            _numeric(truth.item(outcome.resolves_item_id or "")),
            _numeric(outcome),
        )
        for outcome in truth.items
        if outcome.kind is ItemKind.OUTCOME
    ]

    assert any(actual > estimated for estimated, actual in pairs)
    assert any(actual < estimated for estimated, actual in pairs)


def test_both_sides_of_an_estimate_are_graded(corpus):
    """`blocked_quantity` is graded, not only `active_quantity`.

    The field exists because `OUT-0001` proved it had to: Phase 0's wall clock
    matched its estimate almost exactly and the engineering was 2.3x
    over-estimated, with an external block absorbing the difference. A corpus
    that graded only the effort figure would let an extractor look perfect on
    exactly the case that motivated the split.
    """
    truth = load_ground_truth(corpus)
    priced = [
        item
        for item in truth.items
        if item.kind in (ItemKind.ESTIMATE, ItemKind.OUTCOME) and not item.is_distractor
    ]

    assert priced
    for item in priced:
        named = {field.name for field in item.fields}
        assert {"active_quantity", "blocked_quantity"} <= named


def test_blocked_time_is_predicted_and_missed_in_both_directions(corpus):
    """Its errors are deliberately unlike the effort errors.

    A corpus whose two quantities moved together could not express the thing
    the split exists for -- an estimate that looks right in total and is wrong
    about the work. So the set holds a topic that predicted no block and got
    one, a topic that predicted one and got more, and a topic that predicted one
    and got none.
    """
    truth = load_ground_truth(corpus)
    pairs = [
        (
            _blocked(truth.item(outcome.resolves_item_id or "")),
            _blocked(outcome),
        )
        for outcome in truth.items
        if outcome.kind is ItemKind.OUTCOME
    ]

    assert any(actual > predicted for predicted, actual in pairs)
    assert any(actual < predicted for predicted, actual in pairs)


def test_not_every_estimate_predicts_a_block(corpus):
    """Otherwise an extractor scores well on the field by always reporting one."""
    truth = load_ground_truth(corpus)
    predicted = [
        _blocked(item)
        for item in truth.items
        if item.kind is ItemKind.ESTIMATE and not item.is_distractor
    ]

    assert any(value == 0 for value in predicted)
    assert any(value > 0 for value in predicted)


def test_an_estimate_that_nothing_resolves_is_in_the_corpus(corpus):
    """The unmatched case, which is a real finding rather than a gap.

    `OutcomeMatcher`'s match rate has a ceiling below 1 by construction, and it
    is meant to: an estimate nobody ever wrote an actual for is the ordinary
    case in a real corpus and the reason an `unresolved` `Outcome` exists.
    """
    truth = load_ground_truth(corpus)
    estimates = {
        item.item_id
        for item in truth.items
        if item.kind is ItemKind.ESTIMATE and not item.is_distractor
    }
    resolved = {
        outcome.resolves_item_id for outcome in truth.items if outcome.kind is ItemKind.OUTCOME
    }

    assert estimates - resolved
    assert estimates & resolved


def test_one_subject_appears_in_more_than_one_kind_of_document(corpus):
    """What a real corpus looks like, and what makes contradiction and
    archaeology gradeable in the phases that build them."""
    slugs = [
        entry.path.split("-", 1)[1].rsplit("-", 1)[0]
        for entry in load_ground_truth(corpus).documents
    ]

    assert len(slugs) > len(set(slugs))


# -- refusals -----------------------------------------------------------------


def test_a_corpus_of_no_documents_is_refused(tmp_path):
    with pytest.raises(ValueError, match="at least one document"):
        generate_corpus(tmp_path, documents=0, seed=1, generated_at=AT)


def test_a_naive_timestamp_is_refused(tmp_path):
    with pytest.raises(ValueError, match="timezone-aware"):
        generate_corpus(tmp_path, documents=1, seed=1, generated_at=datetime(2026, 8, 17))  # noqa: DTZ001


def test_nothing_is_left_behind_when_the_key_cannot_be_written(tmp_path):
    """A corpus that failed to generate should not look like one that did."""
    with pytest.raises(ValueError, match="at least one document"):
        generate_corpus(tmp_path, documents=0, seed=1, generated_at=AT)

    assert not (tmp_path / GROUND_TRUTH_FILENAME).exists()


def _numeric(item) -> float:
    return _quantity(item, "active_quantity")


def _blocked(item) -> float:
    return _quantity(item, "blocked_quantity")


def _quantity(item, name: str) -> float:
    """One numeric expected field, as a number.

    Named apart from `_field` below, which returns a string: two helpers with
    one name would silently make every comparison here a string comparison, and
    `"11" > "6"` is false.
    """
    field = next(found for found in item.fields if found.name == name)
    return float(field.value)


class TestTheRevisionNotes:
    """The ground truth ContradictionDetector is graded against.

    Before these existed the corpus contained no contradicting pair at all, so
    a detector that found nothing and a detector that found everything scored
    identically. That is the gap these close.
    """

    def test_every_revision_note_overturns_something_that_was_written_down(self, corpus):
        # Only two of the four templates state an assumption, so revising in
        # topic order would write notes overturning assumptions nobody wrote,
        # and their edges would point at nothing.
        truth = load_ground_truth(corpus)
        assert len(_contradiction_pairs(truth)) == DEFAULT_REVISIONS

    def test_each_planted_pair_is_provable_rather_than_a_judgement(self, corpus):
        # The point of writing the reversals as expressions: the pair lands
        # inside what praxis.predicates.intervals can settle, so the corpus
        # grades the cheap deterministic path and not only the model.
        truth = load_ground_truth(corpus)
        by_id = {item.item_id: item for item in truth.items}
        for source, target in _contradiction_pairs(truth):
            first = constraints_of(parse(_field(by_id[source], "predicate")))
            second = constraints_of(parse(_field(by_id[target], "predicate")))
            assert any(conflict(one, other) is not None for one in first for other in second), (
                f"{source} and {target} are not provably incompatible"
            )

    def test_a_revision_and_the_assumption_it_overturns_are_different_documents(self, corpus):
        # A cross-document edge, which is what BACKLOG.md deferred until the
        # metric it feeds existed. It does now.
        truth = load_ground_truth(corpus)
        homes = {
            item.item_id: document.path for document in truth.documents for item in document.items
        }
        for source, target in _contradiction_pairs(truth):
            assert homes[source] != homes[target]

    def test_some_assumptions_are_never_overturned(self, corpus):
        # A detector that flagged every pair would otherwise score perfectly.
        truth = load_ground_truth(corpus)
        overturned = {target for _, target in _contradiction_pairs(truth)}
        assumptions = {
            item.item_id
            for item in truth.items
            if item.kind is ItemKind.ASSUMPTION and not item.is_distractor
        }
        assert assumptions - overturned

    def test_a_corpus_can_be_asked_for_no_revisions(self, tmp_path):
        truth = generate_corpus(
            tmp_path / "plain", documents=4, revisions=0, seed=1, generated_at=AT
        )
        assert _contradiction_pairs(truth) == []

    def test_a_negative_revision_count_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="revision notes"):
            generate_corpus(tmp_path / "bad", documents=4, revisions=-1, seed=1, generated_at=AT)


def _contradiction_pairs(truth) -> list[tuple[str, str]]:
    """Every `contradicts` edge the answer key asserts."""
    return [
        (item.item_id, link.target_item_id)
        for item in truth.items
        for link in item.links
        if link.link_type is LinkType.CONTRADICTS
    ]


def _field(item, name: str) -> str:
    """One expected field's value."""
    return next(field.value for field in item.fields if field.name == name)


class TestTheMonitoringExpectation:
    """The ground truth AssumptionMonitor is graded against.

    The property under all of it is that the key cannot contradict itself. An
    earlier draft gave each assumption a role and added whichever measurement
    the role needed, and two documents in this corpus state `index_size_gb <=
    50` -- so it wrote one fact twice and was silently wrong about one of them.
    Deriving every verdict from the assembled world makes that unrepresentable.
    """

    def test_every_verdict_follows_from_the_facts_the_corpus_supplies(self, corpus):
        truth = load_ground_truth(corpus)
        monitoring = truth.monitoring
        by_id = {item.item_id: item for item in truth.items}
        world = WorldState(
            now=monitoring.as_of,
            facts={name: Decimal(value) for name, value in monitoring.facts.items()},
        )
        for verdict in monitoring.verdicts:
            predicate = _field(by_id[verdict.item_id], "predicate")
            assert _expected_truth(verdict.status) == evaluate(parse(predicate), world).truth

    def test_all_three_verdicts_are_represented(self, corpus):
        # A corpus where everything was breached would let a monitor that always
        # cries breach score perfectly, and one where nothing was would let a
        # monitor that never does.
        statuses = {verdict.status for verdict in load_ground_truth(corpus).monitoring.verdicts}
        assert statuses == {
            AssumptionStatus.BREACHED,
            AssumptionStatus.HOLDING,
            AssumptionStatus.UNVERIFIED,
        }

    def test_the_overturned_assumptions_are_the_breached_ones(self, corpus):
        truth = load_ground_truth(corpus)
        overturned = {target for _, target in _contradiction_pairs(truth)}
        breached = {
            verdict.item_id
            for verdict in truth.monitoring.verdicts
            if verdict.status is AssumptionStatus.BREACHED
        }
        assert overturned <= breached

    def test_a_measurement_is_never_written_twice_with_two_values(self, corpus):
        # The bug this design exists to make unrepresentable.
        monitoring = load_ground_truth(corpus).monitoring
        assert len(monitoring.facts) == len(set(monitoring.facts))

    def test_every_expected_verdict_names_an_assumption_in_the_corpus(self, corpus):
        truth = load_ground_truth(corpus)
        assumptions = {item.item_id for item in truth.items if item.kind is ItemKind.ASSUMPTION}
        assert all(verdict.item_id in assumptions for verdict in truth.monitoring.verdicts)

    def test_a_verdict_naming_something_absent_is_a_problem(self, corpus):
        # The same check _edge_problems makes about edges: an expectation about
        # an item that is not here would be graded as a miss forever, and the
        # miss would look like a monitor that never found it.
        truth = load_ground_truth(corpus)
        broken = truth.model_copy(
            update={
                "monitoring": truth.monitoring.model_copy(
                    update={
                        "verdicts": (
                            ExpectedVerdict(item_id="GT-9999", status=AssumptionStatus.BREACHED),
                        )
                    }
                )
            }
        )
        write_ground_truth(corpus, broken)
        assert any("GT-9999" in problem for problem in verify_corpus(corpus))

    def test_the_facts_are_text_so_a_rate_survives_being_written_down(self, corpus):
        # JSON has one numeric type and Python reads it as a float, which is the
        # representation invariant 4 keeps out of the arithmetic.
        monitoring = load_ground_truth(corpus).monitoring
        assert all(isinstance(value, str) for value in monitoring.facts.values())

    def test_the_key_says_which_format_it_is(self, corpus):
        # A version 1 key has no monitoring section, and reading one as a key
        # with no expectations would make "the monitor found nothing" and "the
        # corpus expected nothing" the same number.
        assert load_ground_truth(corpus).format_version == FORMAT_VERSION


def _expected_truth(status: AssumptionStatus) -> Truth:
    """The truth value a status is the monitor's name for."""
    return {
        AssumptionStatus.BREACHED: Truth.FALSE,
        AssumptionStatus.HOLDING: Truth.TRUE,
        AssumptionStatus.UNVERIFIED: Truth.UNKNOWN,
    }[status]
