"""Properties every topic has to hold, checked rather than reviewed by eye.

The interval disjointness is the one that matters: a topic whose reversal
predicate overlaps its predicate plants a contradiction nothing can settle, and
the corpus would still generate.
"""

from __future__ import annotations

import pytest
from praxis.corpus.measurements import satisfying, violating
from praxis.corpus.templates import TEMPLATES
from praxis.corpus.topics import TOPICS, Topic
from praxis.predicates.parser import parses


@pytest.mark.parametrize("topic", TOPICS, ids=lambda topic: topic.slug)
class TestEachTopic:
    """One instance of these per topic, so a failure names the topic."""

    def test_both_predicates_parse(self, topic: Topic) -> None:
        """A predicate the grammar cannot read is a document nothing can grade."""
        assert parses(topic.predicate)
        assert parses(topic.reversal_predicate)

    def test_the_predicate_can_be_measured_either_way(self, topic: Topic) -> None:
        """The generator needs a witness for holding and for breached."""
        assert satisfying(topic.predicate) is not None
        assert violating(topic.predicate) is not None

    def test_the_reversal_really_reverses(self, topic: Topic) -> None:
        """A value satisfying the reversal must be one the predicate forbids.

        Both witnesses name the same quantity, so the two ranges are disjoint --
        which is what puts the planted contradiction inside what interval
        arithmetic can settle without a model.
        """
        reversed_witness = satisfying(topic.reversal_predicate)
        breaching = violating(topic.predicate)

        assert reversed_witness is not None
        assert breaching is not None
        assert reversed_witness[0] == breaching[0]

    def test_the_estimate_is_positive(self, topic: Topic) -> None:
        """A zero-week estimate divides by zero everywhere calibration looks."""
        assert topic.estimate_weeks > 0
        assert topic.actual_weeks > 0


class TestTheSet:
    """Claims about the topics together rather than one at a time."""

    def test_slugs_are_unique(self) -> None:
        """Slugs become file names, so a duplicate silently overwrites a document."""
        assert len({topic.slug for topic in TOPICS}) == len(TOPICS)

    def test_there_are_enough_topics_for_sixty_distinct_documents(self) -> None:
        """Fewer than this and a sixty-document corpus repeats shapes.

        A repeated topic-and-template pair differs only in its date and a few
        random word choices, so the extra documents add length without adding
        anything a metric can move on.
        """
        assert len(TOPICS) * len(TEMPLATES) >= 60

    def test_the_estimates_miss_in_both_directions(self) -> None:
        """Otherwise an agent scores well by always answering 'over'."""
        over = [t for t in TOPICS if t.actual_weeks > t.estimate_weeks]
        under = [t for t in TOPICS if t.actual_weeks < t.estimate_weeks]

        assert over
        assert under

    def test_most_topics_carry_no_blocked_time(self) -> None:
        """A corpus where every estimate was blocked would teach the wrong prior."""
        unblocked = [topic for topic in TOPICS if topic.blocked_weeks == 0]

        assert len(unblocked) > len(TOPICS) // 2
