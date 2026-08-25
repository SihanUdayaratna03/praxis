"""Candidate generation: what gets compared, and what the corpus never pays for.

The tests worth reading twice are in `TestTheCostCeiling`. Everything else here
is about precision; those are about the claim that makes the detector affordable
at all -- that the number of model calls does not grow with the square of the
corpus, because pairs are only ever formed inside a bucket and a bucket that
grows too large is skipped rather than exploded.

`test_a_key_in_too_many_records_is_a_stop_word_by_construction` is the one that
justifies there being no hand-written list of common words anywhere in this
package.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.agents.blocking import (
    DEFAULT_MIN_SHARED_WORDS,
    MIN_WORD_LENGTH,
    BlockingIndex,
    subject_keys,
    word_keys,
)

INDEX_ASSUMPTION = "the index stays under 50 GB for the next year"
INDEX_REVERSAL = "the index will pass 50 GB well inside the year"
RUNNER_ASSUMPTION = "self hosted runners cover eighty percent of every job"


def indexed(*records: tuple[str, frozenset[str]], **kwargs) -> BlockingIndex:
    """An index holding exactly what a test names."""
    index = BlockingIndex(**kwargs)
    for record_id, keys in records:
        index.add(record_id, keys)
    return index


def pairs(index: BlockingIndex, *, limit: int = 50) -> list[tuple[str, str]]:
    """The pairs an index proposes, as plain tuples."""
    return [
        (candidate.left, candidate.right) for candidate in index.propose(limit=limit).candidates
    ]


class TestWhatMakesAPairWorthComparing:
    def test_one_shared_subject_is_enough(self):
        # Two formalized predicates constraining the same quantity are about the
        # same thing by construction, so no further evidence is needed.
        index = indexed(
            ("A-0001", subject_keys(["index_size_gb"])),
            ("A-0002", subject_keys(["index_size_gb"])),
        )
        assert pairs(index) == [("A-0001", "A-0002")]

    def test_different_subjects_are_never_paired(self):
        index = indexed(
            ("A-0001", subject_keys(["index_size_gb"])),
            ("A-0002", subject_keys(["self_hosted_job_share"])),
        )
        assert pairs(index) == []

    def test_one_shared_word_is_not_enough(self):
        # Two unrelated ADRs share `team` and `rollout`. Proposing on one word
        # would spend the reason tier on the entire corpus.
        index = indexed(
            ("D-0001", word_keys("the search index rollout")),
            ("D-0002", word_keys("the billing rollout")),
        )
        assert pairs(index) == []

    def test_enough_shared_words_are(self):
        index = indexed(
            ("D-0001", word_keys("the search index rollout")),
            ("D-0002", word_keys("another search index change")),
        )
        assert pairs(index) == [("D-0001", "D-0002")]

    def test_the_word_threshold_is_configurable(self):
        index = indexed(
            ("D-0001", word_keys("the search index rollout")),
            ("D-0002", word_keys("the billing rollout")),
            min_shared_words=1,
        )
        assert pairs(index) == [("D-0001", "D-0002")]

    def test_a_subject_beats_words_in_the_ranking(self):
        # A tuple rather than a score: collapsing the two would make a pair
        # sharing three words look like a pair sharing a quantity.
        index = indexed(
            ("A-0001", subject_keys(["index_size_gb"])),
            ("A-0002", subject_keys(["index_size_gb"])),
            ("D-0001", word_keys("the search index rollout plan")),
            ("D-0002", word_keys("the search index rollout schedule")),
        )
        assert pairs(index)[0] == ("A-0001", "A-0002")

    def test_a_short_token_is_not_a_key(self):
        short = "a" * (MIN_WORD_LENGTH - 1)
        assert word_keys(f"{short} {short}") == frozenset()

    def test_a_version_number_is_not_a_key(self):
        # Anchored to start with a letter, so the dates and version numbers
        # every document is full of do not become blocking keys.
        assert word_keys("2026-01-05 and 4.12.0") == frozenset()

    def test_case_does_not_matter(self):
        assert word_keys("OpenSearch") == word_keys("opensearch")


class TestTheCostCeiling:
    def test_a_key_in_too_many_records_is_a_stop_word_by_construction(self):
        # The whole stop-word rule, and the reason there is no list of common
        # words in this package to maintain or to be wrong about.
        index = indexed(
            *((f"A-{n:04d}", word_keys("the rollout")) for n in range(1, 6)),
            max_bucket=3,
        )
        blocking = index.propose(limit=50)
        assert blocking.candidates == ()
        assert "word:rollout" in blocking.skipped

    def test_a_skipped_key_is_reported_rather_than_silent(self):
        # A pair blocking never proposed can never be found, and the eval table
        # has to tell that apart from a model missing one.
        index = indexed(
            *((f"A-{n:04d}", subject_keys(["shared"])) for n in range(1, 6)), max_bucket=2
        )
        assert index.propose(limit=50).skipped == ("subject:shared",)

    def test_a_bucket_at_the_limit_still_pairs(self):
        index = indexed(
            *((f"A-{n:04d}", subject_keys(["shared"])) for n in range(1, 4)), max_bucket=3
        )
        assert len(index.propose(limit=50).candidates) == 3

    def test_the_cap_bounds_what_the_tier_above_is_asked(self):
        index = indexed(*((f"A-{n:04d}", subject_keys(["shared"])) for n in range(1, 6)))
        blocking = index.propose(limit=4)
        assert len(blocking.candidates) == 4
        assert blocking.formed == 10
        assert blocking.capped == 6

    def test_nothing_is_capped_when_the_limit_is_generous(self):
        index = indexed(("A-0001", subject_keys(["shared"])), ("A-0002", subject_keys(["shared"])))
        assert index.propose(limit=50).capped == 0

    def test_a_record_sharing_nothing_costs_nothing(self):
        index = indexed(
            ("A-0001", subject_keys(["one"])),
            ("A-0002", subject_keys(["two"])),
            ("A-0003", subject_keys(["three"])),
        )
        blocking = index.propose(limit=50)
        assert blocking.indexed == 3
        assert blocking.formed == 0

    @given(records=st.integers(min_value=2, max_value=40))
    def test_pairs_formed_never_exceed_the_bucket_bound(self, records):
        # The cost argument, generated rather than argued: whatever the corpus
        # does, one key can contribute at most max_bucket-choose-two pairs, and
        # a bucket past that contributes none at all.
        bound = 3
        index = indexed(
            *((f"A-{n:04d}", subject_keys(["shared"])) for n in range(1, records + 1)),
            max_bucket=bound,
        )
        formed = index.propose(limit=10_000).formed
        assert formed <= bound * (bound - 1) // 2


class TestOnePairOneIdentity:
    def test_a_pair_is_ordered_by_id(self):
        # `contradicts` is symmetric and stored once, and a Link's id is derived
        # from its endpoints -- so an unordered pair would let two runs write two
        # different edges asserting one fact.
        index = indexed(("A-0009", subject_keys(["shared"])), ("A-0002", subject_keys(["shared"])))
        assert pairs(index) == [("A-0002", "A-0009")]

    def test_a_known_pair_is_excluded_in_either_direction(self):
        index = indexed(("A-0001", subject_keys(["shared"])), ("A-0002", subject_keys(["shared"])))
        assert index.propose(limit=50, excluding=[("A-0002", "A-0001")]).candidates == ()
        assert index.propose(limit=50, excluding=[("A-0001", "A-0002")]).candidates == ()

    def test_a_record_is_never_paired_with_itself(self):
        index = indexed(("A-0001", subject_keys(["shared"])))
        index.add("A-0001", subject_keys(["shared"]))
        assert pairs(index) == []

    def test_the_same_key_added_twice_does_not_double_a_pair(self):
        index = indexed(("A-0001", subject_keys(["shared"])))
        index.add("A-0002", subject_keys(["shared"]))
        index.add("A-0002", subject_keys(["shared"]))
        assert pairs(index) == [("A-0001", "A-0002")]


class TestDeterminism:
    def test_two_identical_indexes_propose_identically(self):
        # A cap applied to a ranking two runs could disagree about would make
        # which pairs got looked at depend on dictionary iteration order.
        def build() -> BlockingIndex:
            return indexed(
                ("A-0001", subject_keys(["a"]) | word_keys(INDEX_ASSUMPTION)),
                ("A-0002", subject_keys(["a"]) | word_keys(INDEX_REVERSAL)),
                ("A-0003", subject_keys(["b"]) | word_keys(RUNNER_ASSUMPTION)),
                ("D-0001", word_keys(INDEX_ASSUMPTION)),
            )

        assert build().propose(limit=3).candidates == build().propose(limit=3).candidates

    def test_the_order_records_arrive_in_does_not_matter(self):
        forwards = indexed(
            ("A-0001", subject_keys(["a"])),
            ("A-0002", subject_keys(["a"])),
            ("A-0003", subject_keys(["a"])),
        )
        backwards = indexed(
            ("A-0003", subject_keys(["a"])),
            ("A-0002", subject_keys(["a"])),
            ("A-0001", subject_keys(["a"])),
        )
        assert pairs(forwards) == pairs(backwards)

    def test_the_shared_keys_are_reported_sorted(self):
        index = indexed(
            ("A-0001", subject_keys(["a"]) | word_keys(INDEX_ASSUMPTION)),
            ("A-0002", subject_keys(["a"]) | word_keys(INDEX_ASSUMPTION)),
        )
        shared = index.propose(limit=1).candidates[0].shared
        assert list(shared) == sorted(shared)


class TestConstruction:
    @pytest.mark.parametrize(("bucket", "words"), [(0, 2), (2, 0), (-1, 1)])
    def test_a_bound_below_one_is_refused(self, bucket, words):
        with pytest.raises(ValueError, match="counts of at least one"):
            BlockingIndex(max_bucket=bucket, min_shared_words=words)

    def test_the_defaults_are_the_ones_the_corpus_was_read_for(self):
        index = BlockingIndex()
        assert index.min_shared_words == DEFAULT_MIN_SHARED_WORDS

    def test_an_empty_index_proposes_nothing(self):
        assert BlockingIndex().propose(limit=10) == BlockingIndex().propose(limit=10)
        assert BlockingIndex().propose(limit=10).candidates == ()
