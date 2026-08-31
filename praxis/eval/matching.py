"""Pairing what was extracted with what should have been, by span overlap.

Everything about *which* record answers *which* question is here, so that
`praxis.eval.metrics` can be arithmetic over pairs and nothing else. That split
is invariant 3 taken seriously: a scoring module that also decided what counted
as a match would be a scoring module whose numbers depend on a judgement, and
the judgement is the part that is hard to property-test.

Three decisions worth stating.

**A match is byte overlap, not equality.** The answer key records the byte range
of the evidence; the pipeline records the span it cited. The two are cut by
different processes -- the key by construction as the document was assembled,
the span by the block grid and the segmenter -- and requiring identical
coordinates would grade the segmenter's boundaries rather than the extraction.
Overlapping the right passage is the claim being tested.

**One record answers one item, and the pairing is greedy by overlap.** Two
records citing one passage are one hit and one false positive, which is the
honest reading: the corpus says there is one decision there. Greedy rather than
optimal because the alternative is an assignment problem whose result would
differ by tie-breaking order, and a metric that depends on tie-breaking order
is a metric two runs can disagree about.

**A record matching a distractor is counted separately.** `is_distractor` marks
text built to look extractable and known not to be -- a hypothetical under a
heading saying it was not decided. Those are false positives, and they are the
*informative* ones: without labelled negatives only recall is measurable, which
is ADR 0012's argument. Counting them apart is what makes that label pay.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from praxis.corpus.groundtruth import (
    Comparison,
    CorpusGroundTruth,
    ExpectedField,
    GroundTruthItem,
    ItemKind,
)
from praxis.domain.links import LinkType

SET_SEPARATOR: Final = "|"
"""How the answer key joins a set-valued expectation. Its own convention,
spelled here so the comparison and the generator cannot drift apart."""


@dataclass(frozen=True, slots=True)
class Claim:
    """One extracted record, reduced to what grading needs.

    A view rather than the record itself, because the records are eight
    different Pydantic models and grading cares about four things: what kind of
    claim it is, where it says it read it, and what it said. Building the view
    at the edge keeps every comparison below independent of the store's shapes.

    Attributes:
        record_id: For reporting which record answered which item.
        kind: What was extracted.
        content_hash: The document's normalised content hash, which is how the
            key and the store are joined -- `DocumentGroundTruth.content_sha256`
            is exactly `Document.content_hash`.
        start_byte: First byte of the cited span, into that content.
        end_byte: Last byte, exclusive.
        fields: The record's values, as strings, keyed as the answer key spells
            them.
        links: Edges out of this record, as (type, target record id).
    """

    record_id: str
    kind: ItemKind
    content_hash: str
    start_byte: int
    end_byte: int
    fields: Mapping[str, str]
    links: tuple[tuple[LinkType, str], ...] = ()


@dataclass(frozen=True, slots=True)
class FieldVerdict:
    """Whether one expected field was answered correctly."""

    name: str
    expected: str
    actual: str
    comparison: Comparison
    ok: bool


@dataclass(frozen=True, slots=True)
class Match:
    """One extracted record paired with the item it answers.

    Attributes:
        item: The answer key entry.
        claim: What was extracted for it.
        overlap: Bytes the two ranges share. Reported so a marginal pairing can
            be looked at rather than argued about.
        fields: One verdict per field the key expected.
    """

    item: GroundTruthItem
    claim: Claim
    overlap: int
    fields: tuple[FieldVerdict, ...]

    @property
    def fields_correct(self) -> int:
        """How many expected fields this record got right."""
        return sum(1 for verdict in self.fields if verdict.ok)

    @property
    def exact(self) -> bool:
        """Whether every expected field was right. The strictest reading."""
        return all(verdict.ok for verdict in self.fields)


@dataclass(frozen=True, slots=True)
class Pairing:
    """The whole grading of one run against one corpus.

    Attributes:
        matched: Records paired with real items -- the true positives.
        missed: Real items nothing was extracted for -- the false negatives.
        spurious: Records that overlap no item at all.
        distracted: Records that overlap an item the key marks as a distractor.
            False positives too, and the ones worth reading: the corpus put that
            text there to be resisted.
    """

    matched: tuple[Match, ...] = ()
    missed: tuple[GroundTruthItem, ...] = ()
    spurious: tuple[Claim, ...] = ()
    distracted: tuple[Match, ...] = ()

    @property
    def false_positives(self) -> int:
        """Everything extracted that should not have been."""
        return len(self.spurious) + len(self.distracted)


def pair(
    claims: Iterable[Claim], truth: CorpusGroundTruth, *, kind: ItemKind | None = None
) -> Pairing:
    """Pair extracted claims with the items they answer.

    Args:
        claims: What the run extracted.
        truth: The corpus's answer key.
        kind: Grade only this kind, or every kind when `None`. Precision and
            recall are per kind, so the caller usually asks one at a time.

    Returns:
        The matched, the missed and the two flavours of false positive.
    """
    graded = tuple(claim for claim in claims if kind is None or claim.kind is kind)
    wanted = _items_of(truth, kind)
    homes = {document.content_sha256: document.items for document in truth.documents}
    # Sorted by overlap descending, then by two ids: the tie-break has to be
    # total, or two runs over one corpus could pair the same records differently
    # and disagree about a number that is supposed to be a function of the input.
    candidates = sorted(
        (
            (_overlap(claim, item), claim, item)
            for claim in graded
            for item in homes.get(claim.content_hash, ())
            if item.kind is claim.kind
        ),
        key=lambda entry: (-entry[0], entry[2].item_id, entry[1].record_id),
    )
    matched: list[Match] = []
    distracted: list[Match] = []
    taken_items: set[str] = set()
    taken_claims: set[str] = set()
    for overlap, claim, item in candidates:
        if overlap <= 0 or item.item_id in taken_items or claim.record_id in taken_claims:
            continue
        taken_items.add(item.item_id)
        taken_claims.add(claim.record_id)
        found = Match(item=item, claim=claim, overlap=overlap, fields=grade_fields(item, claim))
        (distracted if item.is_distractor else matched).append(found)
    return Pairing(
        matched=tuple(matched),
        missed=tuple(item for item in wanted if item.item_id not in taken_items),
        spurious=tuple(claim for claim in graded if claim.record_id not in taken_claims),
        distracted=tuple(distracted),
    )


def grade_fields(item: GroundTruthItem, claim: Claim) -> tuple[FieldVerdict, ...]:
    """Check every field the key expected against what was extracted."""
    return tuple(
        FieldVerdict(
            name=expected.name,
            expected=expected.value,
            actual=claim.fields.get(expected.name, ""),
            comparison=expected.comparison,
            ok=field_matches(expected, claim.fields.get(expected.name, "")),
        )
        for expected in item.fields
    )


def field_matches(expected: ExpectedField, actual: str) -> bool:
    """Compare one field the way the answer key says to compare it.

    The comparison travels with the field rather than being inferred from its
    name, which is ADR 0012's decision and the reason this function is a
    dispatch rather than a table of field names nobody would keep in step.
    """
    if expected.comparison is Comparison.EXACT:
        return actual.strip() == expected.value.strip()
    if expected.comparison is Comparison.CONTAINS:
        return expected.value.strip().casefold() in actual.casefold()
    if expected.comparison is Comparison.SET:
        return _as_set(actual) == _as_set(expected.value)
    return _numbers_agree(expected.value, actual, expected.tolerance)


def fusion_pairs(truth: CorpusGroundTruth) -> tuple[tuple[str, str], ...]:
    """Every `estimated_as` edge the corpus asserts, as (assumption, estimate).

    The relationship the product exists to find, taken straight off the key.
    Reported separately from the other edges because it is the only one whose
    recall is a claim about the thesis rather than about extraction.
    """
    return tuple(
        (item.item_id, link.target_item_id)
        for item in truth.items
        for link in item.links
        if link.link_type is LinkType.ESTIMATED_AS
    )


def supersedes_pairs(truth: CorpusGroundTruth) -> tuple[tuple[str, str], ...]:
    """Every `supersedes` edge the corpus asserts, as (newer, older).

    Planted by Phase 9 on the revision notes, where the source says in words
    that an earlier assumption no longer stands. That is what makes it a real
    answer key rather than a planted answer: the relation is *stated* in the
    document, so labelling it is reading the corpus, not deciding what the
    curator should conclude before anyone has asked.

    The direction matters and is the key's, not this function's -- newer to
    older, matching `LinkType.SUPERSEDES` and the ADR convention. Reversed, the
    key would grade a curator that retired the claim the organisation had just
    adopted.
    """
    return tuple(
        (item.item_id, link.target_item_id)
        for item in truth.items
        for link in item.links
        if link.link_type is LinkType.SUPERSEDES
    )


def _items_of(truth: CorpusGroundTruth, kind: ItemKind | None) -> tuple[GroundTruthItem, ...]:
    """The real items a run is expected to find. Distractors are not answers."""
    return tuple(
        item
        for item in truth.items
        if not item.is_distractor and (kind is None or item.kind is kind)
    )


def _overlap(claim: Claim, item: GroundTruthItem) -> int:
    """Bytes shared by a cited span and an item's evidence, or zero.

    Both ranges are into the same document's normalised content -- the caller
    only ever pairs a claim with items from the document its content hash
    names, which is the only coordinate system in which these two numbers mean
    the same thing.
    """
    return max(0, min(claim.end_byte, item.end_byte) - max(claim.start_byte, item.start_byte))


def _as_set(value: str) -> frozenset[str]:
    """Split a set-valued field, so the order an extractor listed it in is free."""
    return frozenset(part.strip().casefold() for part in value.split(SET_SEPARATOR) if part.strip())


def _numbers_agree(expected: str, actual: str, tolerance: Decimal) -> bool:
    """Compare two quantities as `Decimal`, never as float.

    Invariant 4 reaches the grader too: these are compared against each other
    and summed across a whole eval run, and binary floating point would make
    those sums depend on their order.
    """
    try:
        difference = abs(Decimal(expected.strip()) - Decimal(actual.strip()))
    except (InvalidOperation, ArithmeticError, ValueError):
        return False
    return difference <= tolerance
