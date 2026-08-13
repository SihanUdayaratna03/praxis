"""How a record becomes a row, and a row becomes a record again.

One table drives both directions. The alternative -- eighteen hand-written
functions -- would have been eighteen places for a column to be added on the
write side and forgotten on the read side, and the failure that produces is a
field that silently reverts to its default on the way out of the store. The
round-trip property test in `tests/store/test_properties.py` is the check that
this table is complete; keeping the table small is what keeps that check honest.

Three conversions are not automatic and are named per column:

- **Quantities are text.** `Decimal` goes to the exact decimal string and comes
  back through `Decimal(...)`, never through `float`. Invariant 4. Pydantic's
  `Quantity` is strict, so it would reject the string anyway -- which is the
  annotation doing its job rather than an inconvenience.
- **Sequences are JSON.** A decision's rejected options and an estimate's
  conditions are prose with no referential meaning, so a JSON column costs
  nothing. A finding's evidence spans are *ids of rows that must exist*, which
  is why those live in `finding_evidence` and not in a JSON array -- see
  `praxis/store/schema/001_core.sql`.
- **Derived columns.** `content_hash` and `byte_length` are properties of
  `Document` rather than fields, because a stored copy can drift from the
  content it describes. They are written so they can be indexed and ignored on
  the way back in.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Final

from praxis.domain.base import Record
from praxis.domain.enums import RecordKind
from praxis.domain.records import RECORD_TYPES

VERSION_COLUMNS: Final[tuple[str, ...]] = ("retracted", "created_at", "created_by")
"""Columns that live on `record_version` rather than on a payload table."""


@dataclass(frozen=True, slots=True)
class TableMapping:
    """How one record kind is spread across its payload table.

    Attributes:
        table: The payload table name.
        columns: Every column of that table, in declaration order.
        json_columns: Columns holding a JSON-encoded sequence.
        decimal_columns: Columns holding an exact decimal string.
        derived: Columns computed from the record rather than read off a field.
    """

    table: str
    columns: tuple[str, ...]
    json_columns: frozenset[str] = frozenset()
    decimal_columns: frozenset[str] = frozenset()
    derived: Mapping[str, Callable[[Any], object]] = field(default_factory=dict)

    @property
    def insert_sql(self) -> str:
        """The parameterised INSERT for this table, built once per kind."""
        placeholders = ", ".join(f":{column}" for column in self.columns)
        return f"INSERT INTO {self.table} ({', '.join(self.columns)}) VALUES ({placeholders})"  # noqa: S608 -- table and column names come from this module's own frozen table, never from a caller


MAPPINGS: Final[Mapping[RecordKind, TableMapping]] = MappingProxyType(
    {
        RecordKind.DOCUMENT: TableMapping(
            table="document",
            columns=(
                "id",
                "version",
                "source_uri",
                "source_kind",
                "title",
                "content",
                "content_hash",
                "byte_length",
                "ingested_at",
            ),
            derived={
                "content_hash": lambda record: record.content_hash,
                "byte_length": lambda record: record.byte_length,
            },
        ),
        RecordKind.SPAN: TableMapping(
            table="span",
            columns=("id", "version", "doc_id", "start_byte", "end_byte", "text"),
        ),
        RecordKind.DECISION: TableMapping(
            table="decision",
            columns=(
                "id",
                "version",
                "title",
                "chosen",
                "rejected",
                "decision_maker",
                "decided_at",
                "scope",
                "impact",
                "status",
                "span_id",
                "confidence",
            ),
            json_columns=frozenset({"rejected"}),
        ),
        RecordKind.ASSUMPTION: TableMapping(
            table="assumption",
            columns=(
                "id",
                "version",
                "statement",
                "predicate",
                "expiry_condition",
                "status",
                "last_evaluated_at",
                "span_id",
                "confidence",
            ),
        ),
        RecordKind.ESTIMATE: TableMapping(
            table="estimate",
            columns=(
                "id",
                "version",
                "subject",
                "owner",
                "work_class",
                "active_quantity",
                "blocked_quantity",
                "unit",
                "confidence",
                "conditions",
                "estimated_at",
                "span_id",
            ),
            json_columns=frozenset({"conditions"}),
            decimal_columns=frozenset({"active_quantity", "blocked_quantity"}),
        ),
        RecordKind.OUTCOME: TableMapping(
            table="outcome",
            columns=(
                "id",
                "version",
                "estimate_id",
                "active_quantity",
                "blocked_quantity",
                "unit",
                "match_quality",
                "resolved_at",
                "notes",
                "span_id",
            ),
            decimal_columns=frozenset({"active_quantity", "blocked_quantity"}),
        ),
        RecordKind.LINK: TableMapping(
            table="link",
            columns=(
                "id",
                "version",
                "link_type",
                "source_id",
                "source_kind",
                "target_id",
                "target_kind",
                "confidence",
                "rationale",
                "span_id",
            ),
        ),
        RecordKind.FINDING: TableMapping(
            table="finding",
            columns=(
                "id",
                "version",
                "kind",
                "subject_id",
                "subject_kind",
                "prosecution",
                "challenge",
                "verdict",
                "severity",
                "confidence",
                "detected_at",
            ),
        ),
        RecordKind.AUDIT_EVENT: TableMapping(
            table="audit_event",
            columns=(
                "id",
                "ordinal",
                "occurred_at",
                "actor",
                "action",
                "entity_id",
                "entity_kind",
                "entity_version",
                "reason",
                "run_id",
            ),
        ),
    }
)


def to_row(record: Record, **extra: object) -> dict[str, object]:
    """Render a record as the parameter dictionary its INSERT expects.

    Args:
        record: The record to write.
        extra: Values for columns that are not fields of the record --
            `ordinal` on an audit event is the only one.

    Returns:
        One entry per column of the payload table.
    """
    mapping = MAPPINGS[record.record_kind]
    # mode="json" is what turns enums into their values, datetimes into
    # ISO-8601 with the offset intact, and nested models into dictionaries.
    dumped = record.model_dump(mode="json")
    row: dict[str, object] = {}
    for column in mapping.columns:
        if column in mapping.derived:
            row[column] = mapping.derived[column](record)
        elif column in extra:
            row[column] = extra[column]
        elif column in mapping.json_columns:
            row[column] = json.dumps(dumped[column])
        else:
            row[column] = dumped[column]
    return row


def from_row[R: Record](record_type: type[R], row: Mapping[str, Any]) -> R:
    """Rebuild a record from a payload row joined to its version row.

    Columns the model does not declare -- `content_hash`, `ordinal`, and
    anything the query selected for its own use -- are ignored rather than
    rejected, so a caller can hand over a wider row than the record needs.

    Args:
        record_type: The record class to build.
        row: Column values, typically a `sqlite3.Row`.

    Returns:
        The validated record. Validation is not skipped on the way out of the
        store: a row that no longer satisfies the model is a corrupted row, and
        finding that out at the read is the whole reason the models are strict.
    """
    mapping = MAPPINGS[record_type.record_kind]
    payload: dict[str, Any] = {}
    for column in record_type.model_fields:
        if column not in row.keys():  # noqa: SIM118 -- sqlite3.Row has keys() but no __contains__
            continue
        value = row[column]
        if value is not None:
            if column in mapping.json_columns:
                value = json.loads(value)
            elif column in mapping.decimal_columns:
                # Never float. The string is the exact value that was written.
                value = Decimal(value)
        payload[column] = value
    return record_type.model_validate(payload)


def record_type_for(kind: RecordKind) -> type[Record]:
    """Return the model class a record kind maps to."""
    return RECORD_TYPES[kind]
