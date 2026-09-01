"""The three passes that plant nothing, or plant only negatives.

Each claim here is one a control document would silently stop making if a
template drifted: a clean control that picked up an answer stops measuring
hallucination, and an orphan that acquired a decision stops being a retirement
candidate.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from praxis.corpus.controls import orphan_predicate, orphan_statement
from praxis.corpus.generator import Controls, generate_corpus
from praxis.corpus.groundtruth import ItemKind, load_ground_truth, verify_corpus
from praxis.corpus.topics import TOPICS
from praxis.domain.links import LinkType
from praxis.predicates.parser import parses

AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """A small corpus with every control pass switched on, generated once."""
    root = tmp_path_factory.mktemp("controls") / "corpus"
    generate_corpus(
        root,
        documents=8,
        revisions=2,
        controls=Controls(clean=2, adversarial=2, orphans=2),
        seed=20260901,
        generated_at=AT,
    )
    return root


def _named(corpus, suffix):
    """The documents in the key whose path ends this way."""
    return [d for d in load_ground_truth(corpus).documents if d.path.endswith(suffix)]


class TestAskingForNone:
    """`Controls(0, 0, 0)` is how a caller asks for the Phase 3 to 9 corpus."""

    def test_no_controls_are_written(self, tmp_path):
        root = tmp_path / "plain"
        truth = generate_corpus(
            root,
            documents=8,
            revisions=2,
            controls=Controls(clean=0, adversarial=0, orphans=0),
            seed=20260901,
            generated_at=AT,
        )

        assert len(truth.documents) == 10
        assert all(document.items for document in truth.documents)

    @pytest.mark.parametrize(
        "controls",
        [Controls(clean=-1), Controls(adversarial=-1), Controls(orphans=-1)],
        ids=["clean", "adversarial", "orphans"],
    )
    def test_a_negative_count_is_refused(self, tmp_path, controls):
        """Refused rather than clamped: a negative count is a caller's bug."""
        with pytest.raises(ValueError, match="negative number of controls"):
            generate_corpus(
                tmp_path / "bad", documents=4, controls=controls, seed=1, generated_at=AT
            )


class TestCleanControls:
    """Documents with no answers in them at all."""

    def test_they_are_written(self, corpus):
        assert len(_named(corpus, "-operations.md")) == 2

    def test_they_carry_no_items_of_any_kind(self, corpus):
        """Not even distractors. An extraction here is a false positive outright."""
        for document in _named(corpus, "-operations.md"):
            assert document.items == ()

    def test_the_corpus_still_verifies_with_them_in_it(self, corpus):
        """A zero-item document is the shape `_document_problems` had never seen."""
        assert verify_corpus(corpus) == ()


class TestAdversarialMemos:
    """Documents where everything that reads like an answer is labelled as not one."""

    def test_they_are_written(self, corpus):
        assert len(_named(corpus, "-open-questions.md")) == 2

    def test_every_item_is_a_labelled_negative(self, corpus):
        for document in _named(corpus, "-open-questions.md"):
            assert document.items
            assert all(item.is_distractor for item in document.items)

    def test_each_one_plants_several_ways_of_being_wrong(self, corpus):
        """One distractor per document lets an extractor be unlucky once.

        Five different near-misses -- a conditional, a deferral, another team's
        decision, a refused proposal and a question -- is a test of precision
        rather than a coin toss.
        """
        for document in _named(corpus, "-open-questions.md"):
            assert len({item.note for item in document.items}) == 5


class TestOrphanNotes:
    """Assumptions no decision rests on -- what a curator retires."""

    def test_they_are_written(self, corpus):
        assert len(_named(corpus, "-working-note.md")) == 2

    def test_each_states_exactly_one_real_assumption(self, corpus):
        for document in _named(corpus, "-working-note.md"):
            assert len(document.items) == 1
            assert document.items[0].kind is ItemKind.ASSUMPTION
            assert not document.items[0].is_distractor

    def test_nothing_assumes_them(self, corpus):
        """The whole point: no incoming `assumes` edge anywhere in the corpus."""
        truth = load_ground_truth(corpus)
        orphans = {d.items[0].item_id for d in _named(corpus, "-working-note.md")}
        assumed = {
            link.target_item_id
            for item in truth.items
            for link in item.links
            if link.link_type is LinkType.ASSUMES
        }

        assert orphans.isdisjoint(assumed)

    @pytest.mark.parametrize("topic", TOPICS, ids=lambda topic: topic.slug)
    def test_the_orphan_predicate_parses_and_is_its_own_quantity(self, topic):
        """Sharing a quantity with the topic's real assumption would share a verdict."""
        predicate = orphan_predicate(topic)

        assert parses(predicate)
        assert predicate != topic.predicate
        assert topic.subject in orphan_statement(topic)
