"""Search and counting: the two reads that are about the store, not a record.

Separate from `praxis.store.repository` because they answer questions no single
record can. `praxis store stats` and `praxis doctor` are the callers now;
`ReporterAgent` is the caller this shape is really for.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from praxis.domain.enums import RecordKind
from praxis.domain.ids import NodeId
from praxis.domain.links import LinkType
from praxis.store import audit
from praxis.store.errors import translating_sqlite_errors
from praxis.store.migrations import current_version as schema_version

_SEARCH_SQL: Final = """
    SELECT search.record_id AS record_id, search.kind AS kind, search.field AS field,
           snippet(search, 0, '[', ']', '...', 12) AS excerpt
    FROM search
    JOIN record_head AS h ON h.id = search.record_id AND h.version = search.version
    JOIN record_version AS rv ON rv.id = search.record_id AND rv.version = search.version
    WHERE search MATCH ? AND rv.retracted = 0
    ORDER BY rank
    LIMIT ?
"""
"""`search` is written out on both sides of MATCH because FTS5 does not accept
an alias there. The join to `record_head` is what keeps superseded versions out
of the results: nothing is ever removed from the index, because nothing is ever
removed from the store."""


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One full-text match, already narrowed to a current, unretracted record.

    Attributes:
        record_id: The record the text belongs to.
        kind: That record's kind.
        field: Which field matched -- `statement`, `rejected`, `content`.
        excerpt: The matching text, with the terms marked in square brackets.
    """

    record_id: NodeId
    kind: RecordKind
    field: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class StoreStats:
    """What `praxis store stats` reports.

    Attributes:
        schema_version: The migration the store is at.
        records: Current, unretracted record count per kind.
        versions: Rows in `record_version`, superseded versions included. The
            gap between this and the sum of `records` is the store's history.
        links: Current, unretracted edge count per type.
        audit_events: One per write, ever.
    """

    schema_version: int
    records: Mapping[RecordKind, int]
    versions: int
    links: Mapping[LinkType, int]
    audit_events: int


def search(connection: sqlite3.Connection, query: str, *, limit: int = 20) -> tuple[SearchHit, ...]:
    """Full-text search over the current, unretracted records.

    Args:
        connection: An open store.
        query: An FTS5 match expression, passed through unchanged -- the caller
            owns its syntax, and a malformed one arrives back as a
            `StoreQueryError`.
        limit: How many hits to return, best first.
    """
    with translating_sqlite_errors():
        rows = connection.execute(_SEARCH_SQL, (query, limit)).fetchall()
    return tuple(
        SearchHit(
            record_id=row["record_id"],
            kind=RecordKind(row["kind"]),
            field=row["field"],
            excerpt=row["excerpt"],
        )
        for row in rows
    )


def stats(connection: sqlite3.Connection) -> StoreStats:
    """Row and edge counts for a store."""
    with translating_sqlite_errors():
        records = {
            RecordKind(row["kind"]): int(row["n"])
            for row in connection.execute(
                "SELECT kind, count(*) AS n FROM current_record WHERE retracted = 0 GROUP BY kind"
            )
        }
        links = {
            LinkType(row["link_type"]): int(row["n"])
            for row in connection.execute(
                "SELECT link_type, count(*) AS n FROM current_link "
                "WHERE retracted = 0 GROUP BY link_type"
            )
        }
        versions = int(connection.execute("SELECT count(*) FROM record_version").fetchone()[0])
    return StoreStats(
        schema_version=schema_version(connection),
        records=records,
        versions=versions,
        links=links,
        audit_events=audit.event_count(connection),
    )
