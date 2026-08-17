"""The answer key: what a corpus contains, where, and how to compare it.

Designed for the grader in Phase 10 rather than for the generator in Phase 3,
because the generator can be made to produce whatever the grader needs and not
the other way round. ADR 0012 argues the four decisions this file encodes:

- **Byte offsets and the quotation at them.** An extraction is scored by how
  much of the right range it cited, so "found the decision and quoted three
  words too few" is a different result from "found nothing". Grading on
  extracted text alone could not tell them apart, and the span mechanism exists
  precisely so that a claim points at a place.
- **Labelled negatives.** `is_distractor` marks text that looks extractable and
  must not be extracted. Without them only recall is measurable, and a harness
  that measures recall alone rewards an agent that extracts every sentence.
- **Typed edges between items.** The fusion link -- an assumption that is an
  estimate in disguise -- is the product's central claim, so it is graded
  rather than assumed. Edges are checked here against the same grammar
  `praxis.domain.links` enforces, so an answer key cannot assert a relationship
  the graph could not express.
- **Values as text plus a comparison mode.** A union of typed shapes per record
  kind would need revising every time a record gains a field. The format stays
  stable and the grader owns the semantics.

`verify_corpus` is the check that keeps all of it honest. It re-reads every
offset against the document on disk and refuses a corpus whose key has drifted
-- the same thing `VerifierAgent` does to an extraction, turned on the answers.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Final, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from praxis.domain.base import NonEmptyStr
from praxis.domain.enums import RecordKind, SourceKind
from praxis.domain.links import LinkType, endpoints_are_valid
from praxis.ingest.adapters import read_source
from praxis.ingest.errors import IngestionError

FORMAT_VERSION: Final = 1
"""The shape of this file. A grader reads it before anything else and refuses a
corpus it does not understand, rather than mis-scoring one."""

GROUND_TRUTH_FILENAME: Final = "ground_truth.json"
DOCUMENTS_DIRNAME: Final = "documents"


class ItemKind(StrEnum):
    """What an item in the answer key is an instance of.

    A subset of `RecordKind`: only the four kinds an extractor is graded on.
    Documents and spans are not in the key because they are not extracted --
    they are the coordinate system the key is written in.
    """

    DECISION = "decision"
    ASSUMPTION = "assumption"
    ESTIMATE = "estimate"
    OUTCOME = "outcome"

    @property
    def record_kind(self) -> RecordKind:
        """The store's kind for this item, so edges can be grammar-checked."""
        return RecordKind(self.value)


class Comparison(StrEnum):
    """How a grader should compare an extracted field to the expected one.

    Carried per field rather than inferred from the field's name, because the
    right comparison is a property of what the field means and the grader
    should not have to keep a table of names in step with the records.
    """

    EXACT = "exact"
    """Equal after stripping surrounding whitespace."""

    CONTAINS = "contains"
    """The expected text occurs in the extracted value. For prose an extractor
    is expected to capture the substance of and not the wording."""

    NUMERIC = "numeric"
    """Both parse as numbers and agree within `tolerance`. `Decimal`, never
    float -- invariant 4 holds for the answer key too."""

    SET = "set"
    """Both split on `|` and compare as sets, so the order an extractor listed
    rejected options in is not graded."""


class ExpectedField(BaseModel):
    """One field an extractor should have produced, and how to check it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: NonEmptyStr
    """The field on the record, spelled as `praxis.domain.records` spells it."""

    value: str
    comparison: Comparison = Comparison.EXACT
    tolerance: Decimal = Decimal(0)
    """Allowed absolute difference for a `NUMERIC` comparison."""

    @model_validator(mode="after")
    def _tolerance_belongs_to_a_number(self) -> Self:
        if self.tolerance < 0:
            message = f"a tolerance is a distance, so it cannot be {self.tolerance}"
            raise ValueError(message)
        if self.tolerance and self.comparison is not Comparison.NUMERIC:
            message = f"a {self.comparison.value} comparison has no use for a tolerance"
            raise ValueError(message)
        return self


class ExpectedLink(BaseModel):
    """An edge the corpus asserts between two items in the answer key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    link_type: LinkType
    target_item_id: NonEmptyStr


class GroundTruthItem(BaseModel):
    """One thing a corpus document contains, or convincingly appears to.

    Attributes:
        item_id: Stable across regeneration with the same seed, and unique
            across the corpus, so edges can name their target.
        kind: What should be extracted here.
        is_distractor: True when nothing should be extracted here at all.
        start_byte: First byte of the evidence, into the document's content.
        end_byte: Last byte, exclusive.
        quote: Exactly the text at those offsets. Redundant on purpose, in the
            same way and for the same reason `Span` carries its text.
        fields: What an extractor should have produced.
        links: Edges to other items.
        note: Why this is here. Read by a human looking at a failed grading,
            and the only field in the format that exists for people.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: NonEmptyStr
    kind: ItemKind
    is_distractor: bool = False
    start_byte: int = Field(ge=0)
    end_byte: int = Field(ge=1)
    quote: NonEmptyStr
    fields: tuple[ExpectedField, ...] = ()
    links: tuple[ExpectedLink, ...] = ()
    resolves_item_id: NonEmptyStr | None = None
    """For an outcome, the estimate it resolves.

    Not an edge, for the same reason `Outcome.estimate_id` is not one: no link
    type expresses "resolves", because that is 1:1 ownership rather than a
    graph relationship. The answer key mirrors the records rather than
    inventing a seventh edge type to hold it.
    """

    note: str = ""

    @model_validator(mode="after")
    def _is_a_coherent_answer(self) -> Self:
        """Reject a key entry that could not describe anything real."""
        if self.resolves_item_id is not None and self.kind is not ItemKind.OUTCOME:
            message = f"a {self.kind.value} does not resolve an estimate"
            raise ValueError(message)
        if self.end_byte <= self.start_byte:
            message = f"empty or reversed range: [{self.start_byte}, {self.end_byte})"
            raise ValueError(message)
        width = self.end_byte - self.start_byte
        actual = len(self.quote.encode("utf-8"))
        if actual != width:
            message = f"the quotation is {actual} bytes but its range is {width} bytes wide"
            raise ValueError(message)
        if self.is_distractor and (self.fields or self.links or self.resolves_item_id):
            message = (
                f"{self.item_id} is a distractor, so nothing should be extracted from it -- "
                f"expected fields and edges say the opposite"
            )
            raise ValueError(message)
        return self


class DocumentGroundTruth(BaseModel):
    """The answer key for one document, and enough to prove it still fits.

    **Every offset here is an offset into the document's normalised content --
    what `praxis.ingest.adapters` produces -- and not into the file on disk.**
    That is the only coordinate system in which the key and a `Span` mean the
    same thing, and for a JSON source the two are genuinely different: the
    adapter renders it leaf by leaf, so a byte position in the file addresses
    nothing a citation could point at.

    Consequently `content_sha256` and `byte_length` are exactly
    `Document.content_hash` and `Document.byte_length` for the ingested
    document, which also makes the key joinable against the store.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: NonEmptyStr
    """Relative to the corpus root, POSIX-spelled so the file reads the same on
    every platform."""

    source_kind: SourceKind
    content_sha256: NonEmptyStr
    """SHA-256 of the *normalised content*, so a document edited after the key
    was written fails `verify_corpus` loudly instead of grading wrong."""

    byte_length: int = Field(ge=1)
    items: tuple[GroundTruthItem, ...] = ()


class CorpusGroundTruth(BaseModel):
    """Every document in a corpus and everything known to be in them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format_version: int = FORMAT_VERSION
    generator_version: int
    seed: int
    generated_at: AwareDatetime
    documents: tuple[DocumentGroundTruth, ...] = ()

    @property
    def items(self) -> tuple[GroundTruthItem, ...]:
        """Every item in the corpus, in document order."""
        return tuple(item for document in self.documents for item in document.items)

    def item(self, item_id: str) -> GroundTruthItem | None:
        """One item by id, or `None` if the corpus has no such item."""
        return next((found for found in self.items if found.item_id == item_id), None)

    def counts(self) -> dict[ItemKind, int]:
        """How many real items of each kind the corpus holds, distractors aside."""
        counted = dict.fromkeys(ItemKind, 0)
        for item in self.items:
            if not item.is_distractor:
                counted[item.kind] += 1
        return counted


def write_ground_truth(root: Path, truth: CorpusGroundTruth) -> Path:
    """Write the answer key beside the documents it describes.

    Rendered with sorted keys and a trailing newline so that regenerating an
    unchanged corpus produces a byte-identical file, and a diff of two corpora
    is a diff of their content.
    """
    path = root / GROUND_TRUTH_FILENAME
    payload = json.loads(truth.model_dump_json())
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def load_ground_truth(root: Path) -> CorpusGroundTruth:
    """Read the answer key for a corpus.

    Raises:
        OSError: if there is no key at that path.
        ValueError: if it is not a well-formed key.
    """
    raw = (root / GROUND_TRUTH_FILENAME).read_text(encoding="utf-8")
    return CorpusGroundTruth.model_validate_json(raw)


def verify_corpus(root: Path) -> tuple[str, ...]:
    """Return every problem with a corpus on disk; empty means it is gradeable.

    Problems rather than an exception, and all of them rather than the first:
    the caller is a person or a test looking at a corpus that has drifted, and
    a list of what is wrong is worth more than the earliest thing that is.

    Args:
        root: The corpus directory, holding the key and the documents.

    Returns:
        One sentence per problem, in the order the corpus was walked.
    """
    try:
        truth = load_ground_truth(root)
    except (OSError, ValueError) as exc:
        return (f"the ground truth at {root} could not be read: {exc}",)

    problems: list[str] = []
    if truth.format_version != FORMAT_VERSION:
        problems.append(
            f"ground truth is format version {truth.format_version}, "
            f"this build reads {FORMAT_VERSION}"
        )
    seen: set[str] = set()
    for document in truth.documents:
        problems.extend(_document_problems(root, document, seen))
    problems.extend(_edge_problems(truth))
    return tuple(problems)


def _document_problems(root: Path, document: DocumentGroundTruth, seen: set[str]) -> list[str]:
    """Check one document against its own key, in the coordinates spans use.

    The document is normalised through its adapter first, exactly as ingestion
    would. Checking the file's raw bytes instead would pass for markdown and
    quietly certify a JSON key whose offsets address nothing.
    """
    path = root / document.path
    try:
        raw = read_source(path).text.encode("utf-8")
    except (OSError, IngestionError) as exc:
        return [f"{document.path} could not be read: {exc}"]

    problems: list[str] = []
    if content_hash(raw) != document.content_sha256:
        problems.append(f"{document.path} has changed since the ground truth was written")
    if len(raw) != document.byte_length:
        problems.append(f"{document.path} is {len(raw)} bytes, the key says {document.byte_length}")
    for item in document.items:
        if item.item_id in seen:
            problems.append(f"{item.item_id} is used by more than one item")
        seen.add(item.item_id)
        problems.extend(_item_problems(document, item, raw))
    return problems


def _item_problems(document: DocumentGroundTruth, item: GroundTruthItem, raw: bytes) -> list[str]:
    """Re-read one item's offsets against the document's real bytes."""
    where = f"{item.item_id} in {document.path}"
    if item.end_byte > len(raw):
        return [f"{where} ends at {item.end_byte}, past the end of a {len(raw)}-byte document"]
    try:
        found = raw[item.start_byte : item.end_byte].decode("utf-8")
    except UnicodeDecodeError:
        return [f"{where} has offsets that fall inside a character"]
    if found != item.quote:
        return [f"{where} quotes {item.quote!r} where the document holds {found!r}"]
    return []


def _edge_problems(truth: CorpusGroundTruth) -> list[str]:
    """Check every edge points at something, and says something the graph can."""
    by_id = {item.item_id: item for item in truth.items}
    problems: list[str] = []
    for item in truth.items:
        problems.extend(_resolution_problems(item, by_id))
        for link in item.links:
            target = by_id.get(link.target_item_id)
            if target is None:
                problems.append(
                    f"{item.item_id} {link.link_type.value} {link.target_item_id}, "
                    f"which is not in this corpus"
                )
            elif not endpoints_are_valid(
                link.link_type, item.kind.record_kind, target.kind.record_kind
            ):
                problems.append(
                    f"{item.kind.value} --{link.link_type.value}--> {target.kind.value} "
                    f"is not a relationship the graph expresses ({item.item_id})"
                )
    return problems


def _resolution_problems(item: GroundTruthItem, by_id: Mapping[str, GroundTruthItem]) -> list[str]:
    """Check that an outcome resolves an estimate that is really there."""
    if item.resolves_item_id is None:
        return []
    resolved = by_id.get(item.resolves_item_id)
    if resolved is None:
        return [f"{item.item_id} resolves {item.resolves_item_id}, which is not in this corpus"]
    if resolved.kind is not ItemKind.ESTIMATE:
        return [f"{item.item_id} resolves {resolved.item_id}, which is a {resolved.kind.value}"]
    return []


def content_hash(raw: bytes) -> str:
    """SHA-256 of a document's bytes, as the key records it."""
    return hashlib.sha256(raw).hexdigest()


def generated_at_default() -> datetime:
    """The timestamp a corpus carries when the caller does not supply one.

    Deliberately not exported as a default argument anywhere: a corpus should
    record when it was generated, and a caller that wants reproducible bytes
    passes a fixed time rather than being silently given one.
    """
    return datetime.now().astimezone()
