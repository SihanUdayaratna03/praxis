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

import pytest
from praxis.corpus.generator import DEFAULT_DOCUMENTS, GENERATOR_VERSION, generate_corpus
from praxis.corpus.groundtruth import (
    GROUND_TRUTH_FILENAME,
    ItemKind,
    load_ground_truth,
    verify_corpus,
)
from praxis.domain.enums import SourceKind
from praxis.domain.ids import DocumentId
from praxis.domain.links import LinkType
from praxis.domain.records import Span
from praxis.domain.spans import verify_span
from praxis.ingest.adapters import document_from, read_source
from praxis.ingest.blocks import blocks_of

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

    assert len(truth.documents) == DEFAULT_DOCUMENTS
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
    field = next(found for found in item.fields if found.name == "active_quantity")
    return float(field.value)
