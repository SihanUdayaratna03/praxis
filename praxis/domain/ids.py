"""Typed, stable identifiers -- one `NewType` per record kind.

Two things this module buys, both of which are cheaper here than anywhere else.

**Type distinctness.** `SpanId` and `DecisionId` are both strings at runtime and
different types to `mypy --strict`, so passing one where the other belongs is a
compile-time error rather than a foreign key that silently matches nothing.

**Two id strategies, chosen per kind.** Most records get a sequential,
human-readable id (`D-0042`) allocated by the store. `Span` and `Link` instead
get an id derived from their own coordinates, because for those two the
coordinates *are* the identity: two agents citing the same byte range, or
asserting the same edge, must arrive at the same id without coordinating. That
makes citation de-duplication and edge idempotence structural rather than a
clean-up job, and it is why re-running an agent cannot fork the graph.

See [ADR 0008](../../docs/adr/0008-typed-ids-and-append-only-versioning.md).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, NewType

from praxis.domain.enums import RecordKind
from praxis.domain.links import LinkType

DocumentId = NewType("DocumentId", str)
SpanId = NewType("SpanId", str)
DecisionId = NewType("DecisionId", str)
AssumptionId = NewType("AssumptionId", str)
EstimateId = NewType("EstimateId", str)
OutcomeId = NewType("OutcomeId", str)
LinkId = NewType("LinkId", str)
FindingId = NewType("FindingId", str)
AuditEventId = NewType("AuditEventId", str)

NodeId = str
"""A graph endpoint whose kind is carried alongside it rather than in its type.

The traversal code is genuinely polymorphic -- it walks edges without caring
what sits at either end -- so forcing a union of nine `NewType`s through it
would cost a cast per hop and buy nothing.
"""

PREFIXES: Final[Mapping[RecordKind, str]] = MappingProxyType(
    {
        RecordKind.DOCUMENT: "DOC",
        RecordKind.SPAN: "SPAN",
        RecordKind.DECISION: "D",
        RecordKind.ASSUMPTION: "A",
        RecordKind.ESTIMATE: "EST",
        RecordKind.OUTCOME: "OUT",
        RecordKind.LINK: "L",
        RecordKind.FINDING: "F",
        RecordKind.AUDIT_EVENT: "AUD",
    }
)

_KIND_BY_PREFIX: Final[Mapping[str, RecordKind]] = MappingProxyType(
    {prefix: kind for kind, prefix in PREFIXES.items()}
)

CONTENT_ADDRESSED_KINDS: Final[frozenset[RecordKind]] = frozenset(
    {RecordKind.SPAN, RecordKind.LINK}
)
"""Kinds whose id is a function of their own fields, not of a counter."""

SEQUENTIAL_KINDS: Final[frozenset[RecordKind]] = frozenset(RecordKind) - CONTENT_ADDRESSED_KINDS

_ORDINAL_WIDTH: Final = 4
"""Zero-padding for sequential ids. Ids past 9999 simply get wider; they are
never renumbered, so the padding is cosmetic below the boundary and irrelevant
above it."""

_DIGEST_CHARS: Final = 16
"""64 bits of SQLite-friendly lower-case hex. The corpus target is thousands of
nodes, where the collision probability is far below the probability of any
other component of this system being wrong."""

_UNIT_SEPARATOR: Final = "\x1f"
"""Joins the parts of a content-addressed id before hashing. A control
character cannot occur in an id or a `LinkType`, so no combination of parts can
be made to collide with a different combination."""

_SEQUENTIAL_RE: Final = re.compile(r"^(?P<prefix>[A-Z]+)-(?P<ordinal>\d+)$")
_DIGEST_RE: Final = re.compile(rf"^(?P<prefix>[A-Z]+)-(?P<digest>[0-9a-f]{{{_DIGEST_CHARS}}})$")


class InvalidIdError(ValueError):
    """Raised when a string is not a well-formed id of the expected kind."""


def format_sequential_id(kind: RecordKind, ordinal: int) -> str:
    """Render the `ordinal`-th id of a kind, e.g. `(DECISION, 42)` -> `D-0042`.

    Raises:
        InvalidIdError: if the kind is content-addressed, or the ordinal is not
            a positive integer. Ordinals start at 1 so that "no records yet"
            and "the first record" are never the same value.
    """
    if kind in CONTENT_ADDRESSED_KINDS:
        message = f"{kind.value} ids are derived from their content, not allocated"
        raise InvalidIdError(message)
    if ordinal < 1:
        message = f"ordinals start at 1, got {ordinal}"
        raise InvalidIdError(message)
    return f"{PREFIXES[kind]}-{ordinal:0{_ORDINAL_WIDTH}d}"


def kind_of(record_id: str) -> RecordKind:
    """Return the kind a well-formed id belongs to.

    Prefixes are unambiguous because the split is on the first hyphen, so `D-`
    and `DOC-` cannot be confused for one another.

    Raises:
        InvalidIdError: if the id has no recognised prefix.
    """
    prefix, _, _ = record_id.partition("-")
    try:
        return _KIND_BY_PREFIX[prefix]
    except KeyError as exc:
        message = f"{record_id!r} has no recognised id prefix"
        raise InvalidIdError(message) from exc


def ordinal_of(record_id: str) -> int:
    """Return the counter value inside a sequential id.

    Raises:
        InvalidIdError: if the id is not a well-formed sequential id.
    """
    validate(record_id, kind_of(record_id))
    match = _SEQUENTIAL_RE.match(record_id)
    if match is None:
        message = f"{record_id!r} is content-addressed and has no ordinal"
        raise InvalidIdError(message)
    return int(match.group("ordinal"))


def is_valid(record_id: str, kind: RecordKind) -> bool:
    """Report whether `record_id` is a well-formed id of `kind`."""
    try:
        validate(record_id, kind)
    except InvalidIdError:
        return False
    return True


def validate(record_id: str, kind: RecordKind) -> str:
    """Return `record_id` unchanged, or raise if it is malformed for `kind`.

    Sequential ids are checked by re-rendering the parsed ordinal and comparing,
    not by a permissive regex. That rejects `D-00042`, which a `\\d{4,}` pattern
    would accept and which would then compare unequal to the `D-0042` the store
    allocated for the same record.

    Raises:
        InvalidIdError: on any malformed or mis-prefixed id.
    """
    expected_prefix = PREFIXES[kind]
    if kind in CONTENT_ADDRESSED_KINDS:
        match = _DIGEST_RE.match(record_id)
        if match is None or match.group("prefix") != expected_prefix:
            message = (
                f"{record_id!r} is not a valid {kind.value} id (want {expected_prefix}-<16 hex>)"
            )
            raise InvalidIdError(message)
        return record_id

    match = _SEQUENTIAL_RE.match(record_id)
    if match is None or match.group("prefix") != expected_prefix:
        message = f"{record_id!r} is not a valid {kind.value} id (want {expected_prefix}-NNNN)"
        raise InvalidIdError(message)
    ordinal = int(match.group("ordinal"))
    if ordinal < 1 or format_sequential_id(kind, ordinal) != record_id:
        message = f"{record_id!r} is not the canonical spelling of {kind.value} ordinal {ordinal}"
        raise InvalidIdError(message)
    return record_id


def _digest(kind: RecordKind, *parts: str) -> str:
    joined = _UNIT_SEPARATOR.join(parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]
    return f"{PREFIXES[kind]}-{digest}"


def span_id_for(doc_id: DocumentId, start_byte: int, end_byte: int) -> SpanId:
    """Return the one id that a byte range in a document can have.

    Deterministic across processes and runs, so two agents that cite the same
    range produce one span rather than two, and `VerifierAgent` never has to
    reconcile duplicate citations of identical text.
    """
    return SpanId(_digest(RecordKind.SPAN, doc_id, str(start_byte), str(end_byte)))


def link_id_for(link_type: LinkType, source_id: NodeId, target_id: NodeId) -> LinkId:
    """Return the one id that an edge of this type between these nodes can have.

    Asserting an edge twice therefore writes a second *version* of one edge
    rather than a duplicate, which is what makes re-running an agent idempotent
    and keeps the graph identical between two seeded runs.
    """
    return LinkId(_digest(RecordKind.LINK, link_type.value, source_id, target_id))


def check_document_id(value: str) -> str:
    """Pydantic validator for a field holding a `DocumentId`."""
    return validate(value, RecordKind.DOCUMENT)


def check_span_id(value: str) -> str:
    """Pydantic validator for a field holding a `SpanId`."""
    return validate(value, RecordKind.SPAN)


def check_estimate_id(value: str) -> str:
    """Pydantic validator for a field holding an `EstimateId`."""
    return validate(value, RecordKind.ESTIMATE)


def check_node_id(value: str) -> str:
    """Pydantic validator for a field holding any graph endpoint's id."""
    return validate(value, kind_of(value))
