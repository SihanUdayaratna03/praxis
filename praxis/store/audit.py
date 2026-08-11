"""The audit trail, written in the same transaction as the change it describes.

That sentence is the whole design. An audit row that can be lost independently
of its change is worse than no audit row, because it looks trustworthy: a trail
with a hole in it reads exactly like a trail of everything that happened. So
nothing here opens a transaction of its own. Every function takes a connection
that is *already* inside one -- `Repository` guarantees it, and
`praxis.store.connection.transaction` joins rather than nests precisely so that
the guarantee costs nothing to keep.

The trail is not part of the graph, either. `AuditEvent` is excluded from
`GRAPH_KINDS`, no edge may point at one, and its table has no `version` column.
A log that participates in the graph it audits stops being independent evidence,
and a log that can be revised stops being evidence at all.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from praxis.domain.enums import AuditAction, RecordKind
from praxis.domain.ids import AuditEventId, NodeId, ordinal_of
from praxis.domain.records import AuditEvent
from praxis.store.errors import translating_sqlite_errors
from praxis.store.mapping import MAPPINGS, from_row, to_row

_MAPPING = MAPPINGS[RecordKind.AUDIT_EVENT]


def build_event(  # noqa: PLR0913 -- an audit row has this many irreducible parts
    *,
    event_id: AuditEventId,
    action: AuditAction,
    entity_kind: RecordKind,
    entity_id: NodeId,
    entity_version: int,
    actor: str,
    reason: str,
    occurred_at: datetime,
    run_id: str | None = None,
) -> AuditEvent:
    """Assemble the event describing one write.

    A separate step from writing it so the repository can validate the whole
    row before any of it reaches the database, and so a caller can see what
    would be recorded without recording it.
    """
    return AuditEvent(
        id=event_id,
        occurred_at=occurred_at,
        actor=actor,
        action=action,
        entity_kind=entity_kind,
        entity_id=entity_id,
        entity_version=entity_version,
        reason=reason,
        run_id=run_id,
    )


def write_event(connection: sqlite3.Connection, event: AuditEvent) -> None:
    """Insert an audit row into the caller's open transaction.

    Args:
        connection: A connection already inside a transaction. This function
            deliberately does not open one: an audit row that commits on its
            own is the failure mode this module exists to rule out.
        event: The event to record.
    """
    row = to_row(event, ordinal=ordinal_of(event.id))
    with translating_sqlite_errors():
        connection.execute(_MAPPING.insert_sql, row)


def events_for(connection: sqlite3.Connection, entity_id: NodeId) -> tuple[AuditEvent, ...]:
    """Return every audit event about one record, oldest first.

    Ordered by version and then by ordinal rather than by `occurred_at`: two
    writes in the same run can share a timestamp, and allocation order is the
    only total order the store actually knows.
    """
    with translating_sqlite_errors():
        rows = connection.execute(
            "SELECT * FROM audit_event WHERE entity_id = ? ORDER BY entity_version, ordinal",
            (entity_id,),
        ).fetchall()
    return tuple(from_row(AuditEvent, row) for row in rows)


def events_in_run(connection: sqlite3.Connection, run_id: str) -> tuple[AuditEvent, ...]:
    """Return every audit event written during one orchestrator run.

    Phase 4 gives runs an identity; this is what makes "what did that run
    change" a query rather than a log grep.
    """
    with translating_sqlite_errors():
        rows = connection.execute(
            "SELECT * FROM audit_event WHERE run_id = ? ORDER BY ordinal", (run_id,)
        ).fetchall()
    return tuple(from_row(AuditEvent, row) for row in rows)


def event_count(connection: sqlite3.Connection) -> int:
    """How many writes the store has recorded."""
    with translating_sqlite_errors():
        row = connection.execute("SELECT count(*) FROM audit_event").fetchone()
    return int(row[0])
