"""Which pairs are worth comparing at all. Deterministic, and never a model.

`ContradictionDetector` is routed to the `reason` tier. Handing it every pair of
records would cost `n(n-1)/2` calls: 30 assumptions is 435 pairs, 300 is 44,850,
and the corpus only grows. Worse, it would be *wasteful* rather than merely
expensive -- almost every pair is two claims about unrelated things, and asking
the most capable model in the routing table whether the search index conflicts
with the CI runner budget is paying top rate for a foregone conclusion.

So candidate generation is separate, deterministic, and cheap. It answers one
question -- **could these two possibly be about the same thing** -- by indexing
every record under a set of keys and only pairing records that share one.
Building the index is `O(n·k)`; pairs are formed inside buckets, and a bucket
larger than `max_bucket` is skipped with its key recorded rather than exploded.
Cost is therefore bounded by `max_bucket²` per surviving key, and by `limit`
overall, regardless of how large the corpus gets.

**`max_bucket` is doing two jobs and that is the point.** It bounds the work,
and it is also the entire stop-word rule: a token appearing in more records than
that is by definition not a discriminating token, so there is no hand-written
list of common words to maintain and none to be wrong about in a corpus nobody
has read yet. A skipped key is reported, so "we never compared those" is a
number rather than a silence -- which matters, because a pair blocking never
proposed can never be found, and the eval table has to be able to tell that
apart from a model missing one.

Keys are namespaced because two kinds of evidence are not equally strong. One
shared `subject:` key -- two formalized predicates constraining the same
quantity -- is enough on its own. Shared `word:` keys are weaker, so several are
required. That asymmetry is why `AssumptionFormalizer` runs first: it is what
turns prose into a subject key, and a corpus that has been formalized blocks far
more precisely than one that has not.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Final

SUBJECT_PREFIX: Final = "subject:"
"""A quantity a formalized predicate constrains. One shared key is enough."""

WORD_PREFIX: Final = "word:"
"""A word from a record's own prose. Weak on its own; several are needed."""

DEFAULT_MAX_BUCKET: Final = 24
"""How many records may share one key before the key stops discriminating.

Also the stop-word rule, and the bound on the work. Set from the corpus this
project generates: eight topics across twelve documents, so a key naming a real
subject lands in a handful of records and a key like `the` lands in all of them.
"""

DEFAULT_MIN_SHARED_WORDS: Final = 2
"""Shared words required when no subject key is shared.

One is too loose -- two unrelated ADRs share `team` and `rollout` -- and three is
too strict for a decision stated in a single bullet.
"""

MIN_WORD_LENGTH: Final = 4
"""Below this a token carries no signal and inflates every bucket."""

_WORD: Final = re.compile(r"[a-z][a-z0-9_]{2,}")
"""Tokens, after case folding. Anchored to start with a letter so that dates and
version numbers -- which every document is full of -- are not keys."""


@dataclass(frozen=True, slots=True)
class Candidate:
    """Two records worth comparing, and what made them worth it.

    Attributes:
        left: The lower record id. Ordered so that one pair has one identity --
            `contradicts` is symmetric and stored once, and a `Link`'s id is
            derived from its endpoints, so an unordered pair would produce two
            different edges for one fact.
        right: The higher record id.
        shared: The keys both records carry, sorted. Namespaced, so a caller can
            see whether this pair was proposed on a subject or on prose.
    """

    left: str
    right: str
    shared: tuple[str, ...]

    @property
    def subjects(self) -> tuple[str, ...]:
        """The quantities both records constrain."""
        return tuple(
            key.removeprefix(SUBJECT_PREFIX)
            for key in self.shared
            if key.startswith(SUBJECT_PREFIX)
        )

    @property
    def strength(self) -> tuple[int, int]:
        """How strong the evidence is, for a ranking that two runs agree on.

        Subject matches first, then word matches. A tuple rather than a score,
        because collapsing the two into one number would make a pair sharing
        three words look like a pair sharing a quantity.
        """
        return len(self.subjects), len(self.shared) - len(self.subjects)


@dataclass(frozen=True, slots=True)
class Blocking:
    """What candidate generation proposed, and what it declined to.

    Attributes:
        candidates: The pairs worth comparing, strongest first.
        skipped: Keys whose bucket was too large to be discriminating, sorted.
            Reported rather than dropped: a pair these would have proposed is a
            pair nothing will ever look at, and the eval table has to be able to
            tell that from a model missing one.
        indexed: Records indexed.
        formed: Pairs formed before the cap, so the cap's effect is visible.
    """

    candidates: tuple[Candidate, ...] = ()
    skipped: tuple[str, ...] = ()
    indexed: int = 0
    formed: int = 0

    @property
    def capped(self) -> int:
        """How many formed pairs the cap withheld."""
        return max(0, self.formed - len(self.candidates))


def subject_keys(identifiers: Iterable[str]) -> frozenset[str]:
    """Index keys for the quantities a formalized predicate constrains.

    Args:
        identifiers: Names from `praxis.predicates.ast.identifiers_in`.

    Returns:
        The namespaced keys.
    """
    return frozenset(f"{SUBJECT_PREFIX}{name}" for name in identifiers)


def word_keys(*texts: str) -> frozenset[str]:
    """Index keys for the words a record's own prose uses.

    The fallback for anything with no parseable predicate, and the only keys a
    `Decision` has at all -- decisions carry no predicate, so prose is all there
    is to block on.

    Args:
        texts: Whatever the record says about itself.

    Returns:
        The namespaced keys, case-folded and deduplicated.
    """
    return frozenset(
        f"{WORD_PREFIX}{token}"
        for text in texts
        for token in _WORD.findall(text.casefold())
        if len(token) >= MIN_WORD_LENGTH
    )


@dataclass
class BlockingIndex:
    """An inverted index from key to record, and the pairs it proposes.

    Attributes:
        max_bucket: How many records may share a key before it is skipped.
        min_shared_words: Shared words required when no subject is shared.
    """

    max_bucket: int = DEFAULT_MAX_BUCKET
    min_shared_words: int = DEFAULT_MIN_SHARED_WORDS
    _buckets: dict[str, list[str]] = field(default_factory=dict, init=False, repr=False)
    _records: set[str] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        """Refuse a configuration that could not propose anything.

        Raises:
            ValueError: if either bound is below one.
        """
        if self.max_bucket < 1 or self.min_shared_words < 1:
            message = (
                f"max_bucket and min_shared_words are counts of at least one, got "
                f"{self.max_bucket} and {self.min_shared_words}"
            )
            raise ValueError(message)

    def add(self, record_id: str, keys: Iterable[str]) -> None:
        """Index one record under every key it carries.

        Args:
            record_id: The record.
            keys: Its namespaced keys, from `subject_keys` and `word_keys`.
        """
        self._records.add(record_id)
        for key in keys:
            self._buckets.setdefault(key, []).append(record_id)

    def propose(self, *, limit: int, excluding: Iterable[tuple[str, str]] = ()) -> Blocking:
        """The pairs worth comparing, strongest first and capped.

        Args:
            limit: The most pairs to return. The cost ceiling: whatever the
                corpus does, the tier above is asked about at most this many.
            excluding: Pairs already known -- an edge the store holds, in either
                direction. Ordered on the way in, so a caller does not have to.

        Returns:
            The candidates, what was skipped, and how many pairs were formed.
        """
        skipped = tuple(
            sorted(key for key, bucket in self._buckets.items() if len(bucket) > self.max_bucket)
        )
        shared = self._shared_keys(frozenset(skipped))
        known = {_ordered(left, right) for left, right in excluding}
        proposed = [
            Candidate(left, right, tuple(sorted(keys)))
            for (left, right), keys in shared.items()
            if (left, right) not in known and self._accepts(keys)
        ]
        # Ranked strongest first and then by both ids, so the ordering is total.
        # A cap applied to a ranking two runs could disagree about would make
        # which pairs got looked at depend on dictionary iteration order.
        proposed.sort(
            key=lambda candidate: (
                -candidate.strength[0],
                -candidate.strength[1],
                candidate.left,
                candidate.right,
            )
        )
        return Blocking(
            candidates=tuple(proposed[:limit]),
            skipped=skipped,
            indexed=len(self._records),
            formed=len(proposed),
        )

    def _shared_keys(self, skipped: frozenset[str]) -> dict[tuple[str, str], set[str]]:
        """Every pair that shares at least one usable key, and which keys.

        Pairs are formed inside buckets and nowhere else, which is the whole
        cost argument: no pair is ever considered unless something already says
        the two records might be about the same thing.
        """
        shared: dict[tuple[str, str], set[str]] = {}
        for key, bucket in self._buckets.items():
            if key in skipped:
                continue
            members = sorted(set(bucket))
            for index, left in enumerate(members):
                for right in members[index + 1 :]:
                    shared.setdefault((left, right), set()).add(key)
        return shared

    def _accepts(self, keys: Iterable[str]) -> bool:
        """Whether shared keys are evidence enough to spend a model call on.

        One subject is enough: two predicates constraining the same quantity are
        about the same thing by construction. Words are weaker and several are
        needed, which is the asymmetry that makes running the formalizer first
        worth doing.
        """
        found = tuple(keys)
        if any(key.startswith(SUBJECT_PREFIX) for key in found):
            return True
        words = sum(1 for key in found if key.startswith(WORD_PREFIX))
        return words >= self.min_shared_words


def _ordered(left: str, right: str) -> tuple[str, str]:
    """One pair, one identity.

    `contradicts` is symmetric and stored once, and a `Link`'s id is derived
    from its endpoints -- so an unordered pair would let two runs write two
    different edges asserting one fact.
    """
    return (left, right) if left <= right else (right, left)
