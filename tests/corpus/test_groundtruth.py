"""The answer key's own rules, and every way a corpus can stop being gradeable.

`verify_corpus` is the only thing standing between a generator bug and an eval
run that grades everything against the wrong sentences -- a failure with no
symptom, because the offsets still resolve and the quotations still match, and
only the meaning is wrong. So the interesting tests here are the ones that
break a corpus on purpose and check it is caught.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from praxis.corpus.generator import generate_corpus
from praxis.corpus.groundtruth import (
    DOCUMENTS_DIRNAME,
    FORMAT_VERSION,
    GROUND_TRUTH_FILENAME,
    Comparison,
    CorpusGroundTruth,
    ExpectedField,
    ExpectedLink,
    GroundTruthItem,
    ItemKind,
    load_ground_truth,
    verify_corpus,
    write_ground_truth,
)
from praxis.domain.links import LinkType

AT = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)


@pytest.fixture
def corpus(tmp_path):
    """A small generated corpus, on disk and verified."""
    root = tmp_path / "corpus"
    generate_corpus(root, documents=4, seed=7, generated_at=AT)
    return root


def item(**overrides):
    defaults = {
        "item_id": "GT-0001",
        "kind": ItemKind.DECISION,
        "start_byte": 0,
        "end_byte": 5,
        "quote": "abcde",
    }
    return GroundTruthItem(**{**defaults, **overrides})


def rewrite(root, truth: CorpusGroundTruth) -> None:
    write_ground_truth(root, truth)


# -- what an item may say ----------------------------------------------------


def test_a_quotation_must_be_exactly_as_wide_as_its_range():
    """The same redundancy `Span` carries, for the same reason: it is what
    makes a key that has drifted detectable rather than merely wrong."""
    with pytest.raises(ValueError, match="bytes but its range"):
        item(quote="abcdef")


def test_a_reversed_range_is_refused():
    with pytest.raises(ValueError, match="empty or reversed"):
        item(start_byte=9, end_byte=4, quote="abcde")


def test_an_item_quoting_nothing_is_refused():
    """An empty range cannot even be spelled: the quotation has to be
    non-empty, and it has to be exactly as wide as the range."""
    with pytest.raises(ValueError, match="at least 1 character"):
        item(start_byte=5, end_byte=5, quote="")


def test_a_distractor_cannot_expect_anything_to_be_extracted():
    """A labelled negative that expects a field is not a negative, and the way
    that happens is a caller passing the flag and the fields together."""
    with pytest.raises(ValueError, match="distractor"):
        item(
            is_distractor=True,
            fields=(ExpectedField(name="chosen", value="SQLite"),),
        )


def test_only_an_outcome_resolves_an_estimate():
    """`resolves_item_id` mirrors `Outcome.estimate_id`, which exists because
    no edge type expresses ownership."""
    with pytest.raises(ValueError, match="does not resolve an estimate"):
        item(kind=ItemKind.DECISION, resolves_item_id="GT-0002")


def test_a_tolerance_only_means_something_on_a_number():
    with pytest.raises(ValueError, match="no use for a tolerance"):
        ExpectedField(name="chosen", value="SQLite", tolerance=Decimal("0.5"))


def test_a_tolerance_is_a_distance():
    with pytest.raises(ValueError, match="cannot be"):
        ExpectedField(
            name="weeks", value="6", comparison=Comparison.NUMERIC, tolerance=Decimal("-1")
        )


def test_an_item_kind_maps_onto_the_store_kind_so_edges_can_be_checked():
    assert ItemKind.ASSUMPTION.record_kind.value == "assumption"


# -- a corpus that is fine ---------------------------------------------------


def test_a_freshly_generated_corpus_has_nothing_wrong_with_it(corpus):
    assert verify_corpus(corpus) == ()


def test_the_key_round_trips_through_the_file_it_is_written_to(corpus):
    loaded = load_ground_truth(corpus)

    assert loaded.format_version == FORMAT_VERSION
    assert loaded.documents
    assert loaded.item(loaded.items[0].item_id) == loaded.items[0]
    assert loaded.item("GT-9999") is None


# -- corpora that are not -----------------------------------------------------


def test_a_document_edited_after_the_key_was_written_is_caught(corpus):
    """The failure the content hash exists for. Without it the corpus would
    grade against sentences that have moved."""
    document = load_ground_truth(corpus).documents[0]
    target = corpus / document.path
    target.write_text("something else entirely\n", encoding="utf-8")

    problems = verify_corpus(corpus)

    assert any("has changed since" in problem for problem in problems)


def test_a_missing_document_is_reported_rather_than_raised(corpus):
    document = load_ground_truth(corpus).documents[0]
    (corpus / document.path).unlink()

    problems = verify_corpus(corpus)

    assert any("could not be read" in problem for problem in problems)


def test_a_corpus_with_no_key_at_all_is_reported(tmp_path):
    assert verify_corpus(tmp_path)[0].startswith("the ground truth at")


def test_a_key_from_a_future_format_is_refused_rather_than_mis_scored(corpus):
    truth = load_ground_truth(corpus)
    rewrite(corpus, truth.model_copy(update={"format_version": FORMAT_VERSION + 1}))

    assert any("format version" in problem for problem in verify_corpus(corpus))


def test_an_offset_that_no_longer_quotes_its_own_text_is_caught(corpus):
    """The check that makes the generator's own bug loud.

    The item is shifted by a whole byte, both ends, so it stays internally
    consistent -- the quotation is still exactly as wide as its range. Nothing
    but re-reading the document can tell that it now points one byte to the
    right, which is precisely the class of bug this exists to catch.
    """
    truth = load_ground_truth(corpus)
    document = truth.documents[0]
    moved = document.items[0].model_copy(
        update={
            "start_byte": document.items[0].start_byte + 1,
            "end_byte": document.items[0].end_byte + 1,
        }
    )
    rewrite(
        corpus,
        truth.model_copy(
            update={
                "documents": (
                    document.model_copy(update={"items": (moved, *document.items[1:])}),
                    *truth.documents[1:],
                )
            }
        ),
    )

    problems = verify_corpus(corpus)

    assert any("where the document holds" in problem for problem in problems)


def test_an_item_id_used_twice_is_caught(corpus):
    truth = load_ground_truth(corpus)
    document = truth.documents[0]
    clashing = document.items[1].model_copy(update={"item_id": document.items[0].item_id})
    rewrite(
        corpus,
        truth.model_copy(
            update={
                "documents": (
                    document.model_copy(
                        update={"items": (document.items[0], clashing, *document.items[2:])}
                    ),
                    *truth.documents[1:],
                )
            }
        ),
    )

    assert any("more than one item" in problem for problem in verify_corpus(corpus))


def test_an_edge_pointing_at_nothing_is_caught(corpus):
    truth = load_ground_truth(corpus)
    document = truth.documents[0]
    dangling = document.items[0].model_copy(
        update={"links": (ExpectedLink(link_type=LinkType.ASSUMES, target_item_id="GT-9999"),)}
    )
    rewrite(
        corpus,
        truth.model_copy(
            update={
                "documents": (
                    document.model_copy(update={"items": (dangling, *document.items[1:])}),
                    *truth.documents[1:],
                )
            }
        ),
    )

    assert any("not in this corpus" in problem for problem in verify_corpus(corpus))


def test_an_edge_the_graph_could_not_express_is_caught(corpus):
    """The answer key is held to the same grammar as the store. A corpus that
    asserts a relationship the graph cannot hold is a corpus nothing could
    score full marks on."""
    truth = load_ground_truth(corpus)
    document = truth.documents[0]
    estimate = next(found for found in truth.items if found.kind is ItemKind.ESTIMATE)
    wrong = document.items[0].model_copy(
        update={
            "kind": ItemKind.OUTCOME,
            "links": (ExpectedLink(link_type=LinkType.ASSUMES, target_item_id=estimate.item_id),),
        }
    )
    rewrite(
        corpus,
        truth.model_copy(
            update={
                "documents": (
                    document.model_copy(update={"items": (wrong, *document.items[1:])}),
                    *truth.documents[1:],
                )
            }
        ),
    )

    assert any("is not a relationship" in problem for problem in verify_corpus(corpus))


def test_an_outcome_resolving_something_absent_is_caught(corpus):
    truth = load_ground_truth(corpus)
    document = next(
        found
        for found in truth.documents
        if any(entry.kind is ItemKind.OUTCOME for entry in found.items)
    )
    position = next(
        index for index, entry in enumerate(document.items) if entry.kind is ItemKind.OUTCOME
    )
    broken = document.items[position].model_copy(update={"resolves_item_id": "GT-9999"})
    items = (*document.items[:position], broken, *document.items[position + 1 :])
    rewrite(
        corpus,
        truth.model_copy(
            update={
                "documents": tuple(
                    document.model_copy(update={"items": items}) if found is document else found
                    for found in truth.documents
                )
            }
        ),
    )

    assert any("resolves GT-9999" in problem for problem in verify_corpus(corpus))


def test_an_outcome_resolving_the_wrong_kind_is_caught(corpus):
    truth = load_ground_truth(corpus)
    document = next(
        found
        for found in truth.documents
        if any(entry.kind is ItemKind.OUTCOME for entry in found.items)
    )
    position = next(
        index for index, entry in enumerate(document.items) if entry.kind is ItemKind.OUTCOME
    )
    decision = next(found for found in truth.items if found.kind is ItemKind.DECISION)
    broken = document.items[position].model_copy(update={"resolves_item_id": decision.item_id})
    items = (*document.items[:position], broken, *document.items[position + 1 :])
    rewrite(
        corpus,
        truth.model_copy(
            update={
                "documents": tuple(
                    document.model_copy(update={"items": items}) if found is document else found
                    for found in truth.documents
                )
            }
        ),
    )

    assert any("which is a decision" in problem for problem in verify_corpus(corpus))


# -- the file on disk ---------------------------------------------------------


def test_the_key_is_written_where_a_grader_will_look_for_it(corpus):
    assert (corpus / GROUND_TRUTH_FILENAME).is_file()
    assert (corpus / DOCUMENTS_DIRNAME).is_dir()


def test_writing_the_same_key_twice_produces_the_same_bytes(corpus):
    truth = load_ground_truth(corpus)
    first = (corpus / GROUND_TRUTH_FILENAME).read_bytes()

    write_ground_truth(corpus, truth)

    assert (corpus / GROUND_TRUTH_FILENAME).read_bytes() == first
