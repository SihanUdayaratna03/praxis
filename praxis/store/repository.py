"""The only module that writes SQL against the entity tables.

ADR 0003 claims the backend is replaceable without touching an agent. That claim
is worth exactly as much as this boundary is: every statement lives in this
package -- here, or in `graph`, `reports`, `audit` and `migrations` -- every
failure is translated by `errors`, and nothing outside it imports `sqlite3`.

The write methods are the interesting part, and there are only three of them:
`add`, `revise` and `retract`. There is no `update` and no `delete`, which is
CLAUDE.md invariant 7 expressed as an API rather than as a rule people remember.
Each one writes its record, its version row and its `AuditEvent` inside a single
transaction, so "every mutation produces exactly one audit event" is true by
construction and not by discipline -- the audit row cannot be lost without the
change it describes being lost with it.

Reads are built around "the current version of X". ADR 0008 named the mistake
this avoids: an API where the caller filters by version by hand is an API where
one caller eventually forgets, reads a superseded record, and is right about
everything except which version it was.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Final

from praxis.config.settings import Settings
from praxis.domain.base import VersionedRecord
from praxis.domain.enums import AuditAction, RecordKind
from praxis.domain.ids import (
    CONTENT_ADDRESSED_KINDS,
    AuditEventId,
    DocumentId,
    NodeId,
    format_sequential_id,
    ordinal_of,
)
from praxis.domain.links import DEPENDENCY_LINK_TYPES, LinkType
from praxis.domain.records import AuditEvent, Finding, Link
from praxis.store import audit, graph, reports
from praxis.store.connection import connect_from_settings, transaction
from praxis.store.errors import (
    RecordNotFoundError,
    StoreError,
    VersionConflictError,
    translating_sqlite_errors,
)
from praxis.store.mapping import MAPPINGS, from_row, to_row
from praxis.store.migrations import migrate

_INSERT_NODE: Final = "INSERT INTO node (id, kind, ordinal) VALUES (?, ?, ?)"
_INSERT_VERSION: Final = (
    "INSERT INTO record_version (id, version, retracted, created_at, created_by) "
    "VALUES (?, ?, ?, ?, ?)"
)
_INSERT_EVIDENCE: Final = (
    "INSERT INTO finding_evidence (finding_id, finding_version, position, span_id) "
    "VALUES (?, ?, ?, ?)"
)


class Repository:
    """Reads and writes records, and nothing else knows how.

    Holds a connection rather than owning one, so a caller that needs several
    repositories over one store -- or a test that wants an in-memory one --
    does not have to go through the settings.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an open, migrated connection.

        Args:
            connection: A connection from `praxis.store.connection.connect`.
                Opening it any other way skips the pragmas.
        """
        self._connection = connection

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying connection, for `praxis.store.graph`."""
        return self._connection

    def close(self) -> None:
        """Close the connection this repository was given."""
        self._connection.close()

    # -- allocation --------------------------------------------------------

    def next_id(self, kind: RecordKind) -> str:
        """Allocate the next sequential id for a kind, e.g. `D-0043`.

        Reads the highest ordinal rather than counting rows, so a store whose
        history includes a rolled-back write never reissues an id.

        Raises:
            StoreError: for `Span` and `Link`, whose ids are derived from their
                own coordinates and are not the store's to allocate.
        """
        if kind in CONTENT_ADDRESSED_KINDS:
            message = f"{kind.value} ids are derived from their content, not allocated"
            raise StoreError(message)
        if kind is RecordKind.AUDIT_EVENT:
            sql = "SELECT COALESCE(MAX(ordinal), 0) FROM audit_event"
            parameters: tuple[str, ...] = ()
        else:
            sql = "SELECT COALESCE(MAX(ordinal), 0) FROM node WHERE kind = ?"
            parameters = (kind.value,)
        with translating_sqlite_errors():
            row = self._connection.execute(sql, parameters).fetchone()
        return format_sequential_id(kind, int(row[0]) + 1)

    # -- writes ------------------------------------------------------------

    def add[R: VersionedRecord](
        self,
        record: R,
        *,
        actor: str,
        reason: str,
        at: datetime | None = None,
        run_id: str | None = None,
    ) -> R:
        """Write a record for the first time, with its audit event.

        The record's own `created_at` is kept -- it says when the thing was
        written, and the caller knows that better than the store does. `at` is
        the audit event's timestamp.

        Args:
            record: A version-1 record.
            actor: The agent or human responsible.
            reason: Why the write happened. Read by humans, so make it a
                sentence.
            at: When the write happened. Defaults to now.
            run_id: The orchestrator run this belongs to, once there are runs.

        Returns:
            The record, unchanged.

        Raises:
            StoreError: if the record is not at version 1.
            DuplicateRecordError: if that id is already in the store.
        """
        if record.version != 1:
            message = f"add() writes version 1; {record.id} arrived at version {record.version}"
            raise StoreError(message)
        with transaction(self._connection):
            self._insert_node(record)
            self._insert_version(record)
            self._insert_payload(record)
            self._write_audit(
                AuditAction.CREATED, record, actor=actor, reason=reason, at=at, run_id=run_id
            )
        return record

    def revise[R: VersionedRecord](
        self,
        record: R,
        *,
        actor: str,
        reason: str,
        at: datetime | None = None,
        run_id: str | None = None,
    ) -> R:
        """Write the next version of an existing record.

        The version the caller read is the version they are revising against.
        If the store has moved on, that is a conflict rather than a merge: the
        old version is still there, so nothing is lost, but a caller who decided
        something against version 3 has decided against stale facts if version 4
        already exists.

        Unlike `add`, this overwrites `created_at` with `at`. A new version
        carrying the previous version's timestamp would be claiming to have been
        written at a time it was not.

        Returns:
            The new version, with `version` and `created_at` set by the store.

        Raises:
            RecordNotFoundError: if the record has never been written.
            VersionConflictError: if the record is not at the version given.
        """
        occurred_at = _timestamp(at)
        with transaction(self._connection):
            current = self._require_current_version(record)
            revised = record.model_copy(update={"version": current + 1, "created_at": occurred_at})
            self._insert_version(revised)
            self._insert_payload(revised)
            self._write_audit(
                AuditAction.REVISED,
                revised,
                actor=actor,
                reason=reason,
                at=occurred_at,
                run_id=run_id,
            )
        return revised

    def retract[R: VersionedRecord](  # noqa: PLR0913 -- the audit fields are irreducible
        self,
        record_type: type[R],
        record_id: str,
        *,
        actor: str,
        reason: str,
        at: datetime | None = None,
        run_id: str | None = None,
    ) -> R:
        """Withdraw a record by writing a new version that is marked retracted.

        Not a delete. The withdrawn versions stay readable, so a retraction is a
        statement *about* a record rather than the disappearance of one, and the
        audit trail describing it stays true.

        Raises:
            RecordNotFoundError: if the record has never been written.
        """
        occurred_at = _timestamp(at)
        with transaction(self._connection):
            current = self.require(record_type, record_id)
            retracted = current.model_copy(
                update={
                    "version": current.version + 1,
                    "retracted": True,
                    "created_at": occurred_at,
                }
            )
            self._insert_version(retracted)
            self._insert_payload(retracted)
            self._write_audit(
                AuditAction.RETRACTED,
                retracted,
                actor=actor,
                reason=reason,
                at=occurred_at,
                run_id=run_id,
            )
        return retracted

    # -- reads -------------------------------------------------------------

    def get[R: VersionedRecord](
        self, record_type: type[R], record_id: str, version: int | None = None
    ) -> R | None:
        """Return a record, or `None` if it is not there.

        Args:
            record_type: The model class, which also names the table.
            record_id: The record's id.
            version: A specific version. Defaults to the current one, which is
                what almost every caller wants and what ADR 0008 built the read
                API around.
        """
        table = MAPPINGS[record_type.record_kind].table
        if version is None:
            sql = _select(table, current_only=True, predicate="p.id = ?")
            parameters: tuple[object, ...] = (record_id,)
        else:
            sql = _select(table, current_only=False, predicate="p.id = ? AND p.version = ?")
            parameters = (record_id, version)
        with translating_sqlite_errors():
            row = self._connection.execute(sql, parameters).fetchone()
        return None if row is None else self._to_record(record_type, row)

    def require[R: VersionedRecord](
        self, record_type: type[R], record_id: str, version: int | None = None
    ) -> R:
        """Return a record, or raise because the caller cannot continue without it.

        Raises:
            RecordNotFoundError: if there is no such record or version.
        """
        found = self.get(record_type, record_id, version)
        if found is None:
            raise RecordNotFoundError(record_type.record_kind, record_id, version)
        return found

    def current_version(self, record_id: str) -> int | None:
        """The highest version of a record, or `None` if it has none."""
        with translating_sqlite_errors():
            row = self._connection.execute(
                "SELECT version FROM record_head WHERE id = ?", (record_id,)
            ).fetchone()
        return None if row is None else int(row[0])

    def versions(self, record_id: str) -> tuple[int, ...]:
        """Every version of a record, ascending."""
        with translating_sqlite_errors():
            rows = self._connection.execute(
                "SELECT version FROM record_version WHERE id = ? ORDER BY version", (record_id,)
            ).fetchall()
        return tuple(int(row[0]) for row in rows)

    def exists(self, node_id: NodeId) -> bool:
        """Whether a node with this id has ever been written."""
        with translating_sqlite_errors():
            row = self._connection.execute("SELECT 1 FROM node WHERE id = ?", (node_id,)).fetchone()
        return row is not None

    def list_all[R: VersionedRecord](
        self, record_type: type[R], *, include_retracted: bool = False
    ) -> tuple[R, ...]:
        """Every current version of a kind, oldest first."""
        table = MAPPINGS[record_type.record_kind].table
        predicate = "1 = 1" if include_retracted else "rv.retracted = 0"
        sql = (
            f"{_select(table, current_only=True, predicate=predicate)} ORDER BY rv.created_at, p.id"
        )
        with translating_sqlite_errors():
            rows = self._connection.execute(sql).fetchall()
        return tuple(self._to_record(record_type, row) for row in rows)

    # -- edges, audit, search, stats ---------------------------------------
    #
    # Thin delegation on purpose. The edge queries and the recursive walks are
    # one subject and live in `praxis.store.graph`; search and counting are
    # another and live in `praxis.store.reports`. They are surfaced here so a
    # caller holds one object rather than a connection and three modules.

    def links_from(
        self, node_id: NodeId, types: Iterable[LinkType] | None = None
    ) -> tuple[Link, ...]:
        """Current edges whose source is this node."""
        return graph.links_from(self._connection, node_id, types)

    def links_to(
        self, node_id: NodeId, types: Iterable[LinkType] | None = None
    ) -> tuple[Link, ...]:
        """Current edges whose target is this node."""
        return graph.links_to(self._connection, node_id, types)

    def links_touching(
        self, node_id: NodeId, types: Iterable[LinkType] | None = None
    ) -> tuple[Link, ...]:
        """Current edges at either end of this node, in either direction."""
        return graph.links_touching(self._connection, node_id, types)

    def impacted_by(
        self,
        node_id: NodeId,
        *,
        types: Iterable[LinkType] = DEPENDENCY_LINK_TYPES,
        max_depth: int = graph.DEFAULT_MAX_DEPTH,
    ) -> tuple[graph.ReachedNode, ...]:
        """Everything that depends on this node. See `praxis.store.graph`."""
        return graph.impacted_by(self._connection, node_id, types=types, max_depth=max_depth)

    def depends_on(
        self,
        node_id: NodeId,
        *,
        types: Iterable[LinkType] = DEPENDENCY_LINK_TYPES,
        max_depth: int = graph.DEFAULT_MAX_DEPTH,
    ) -> tuple[graph.ReachedNode, ...]:
        """Everything this node rests on. See `praxis.store.graph`."""
        return graph.depends_on(self._connection, node_id, types=types, max_depth=max_depth)

    def audit_for(self, record_id: NodeId) -> tuple[AuditEvent, ...]:
        """Every audit event about one record, oldest first."""
        return audit.events_for(self._connection, record_id)

    def search(self, query: str, *, limit: int = 20) -> tuple[reports.SearchHit, ...]:
        """Full-text search over current, unretracted records."""
        return reports.search(self._connection, query, limit=limit)

    def document_with_content(self, content_hash: str) -> DocumentId | None:
        """The document already holding these exact bytes, if there is one."""
        return reports.document_with_content(self._connection, content_hash)

    def stats(self) -> reports.StoreStats:
        """Row and edge counts, for `praxis store stats` and `praxis doctor`."""
        return reports.stats(self._connection)

    # -- internals ---------------------------------------------------------

    def _insert_node(self, record: VersionedRecord) -> None:
        kind = record.record_kind
        ordinal = None if kind in CONTENT_ADDRESSED_KINDS else ordinal_of(record.id)
        with translating_sqlite_errors():
            self._connection.execute(_INSERT_NODE, (record.id, kind.value, ordinal))

    def _insert_version(self, record: VersionedRecord) -> None:
        with translating_sqlite_errors():
            self._connection.execute(
                _INSERT_VERSION,
                (
                    record.id,
                    record.version,
                    int(record.retracted),
                    record.created_at.isoformat(),
                    record.created_by,
                ),
            )

    def _insert_payload(self, record: VersionedRecord) -> None:
        mapping = MAPPINGS[record.record_kind]
        with translating_sqlite_errors():
            self._connection.execute(mapping.insert_sql, to_row(record))
            if isinstance(record, Finding):
                self._connection.executemany(
                    _INSERT_EVIDENCE,
                    [
                        (record.id, record.version, position, span_id)
                        for position, span_id in enumerate(record.evidence_span_ids)
                    ],
                )

    def _require_current_version(self, record: VersionedRecord) -> int:
        current = self.current_version(record.id)
        if current is None:
            raise RecordNotFoundError(record.record_kind, record.id)
        if record.version != current:
            raise VersionConflictError(
                record.record_kind, record.id, expected=record.version, found=current
            )
        return current

    def _write_audit(  # noqa: PLR0913 -- mirrors the audit row's own parts
        self,
        action: AuditAction,
        record: VersionedRecord,
        *,
        actor: str,
        reason: str,
        at: datetime | None,
        run_id: str | None,
    ) -> None:
        event = audit.build_event(
            event_id=AuditEventId(self.next_id(RecordKind.AUDIT_EVENT)),
            action=action,
            entity_kind=record.record_kind,
            entity_id=record.id,
            entity_version=record.version,
            actor=actor,
            reason=reason,
            occurred_at=_timestamp(at),
            run_id=run_id,
        )
        audit.write_event(self._connection, event)

    def _to_record[R: VersionedRecord](self, record_type: type[R], row: sqlite3.Row) -> R:
        data: dict[str, Any] = {column: row[column] for column in row.keys()}  # noqa: SIM118
        if record_type.record_kind is RecordKind.FINDING:
            data["evidence_span_ids"] = self._evidence_for(data["id"], data["version"])
        return from_row(record_type, data)

    def _evidence_for(self, finding_id: str, version: int) -> tuple[str, ...]:
        with translating_sqlite_errors():
            rows = self._connection.execute(
                "SELECT span_id FROM finding_evidence "
                "WHERE finding_id = ? AND finding_version = ? ORDER BY position",
                (finding_id, version),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)


def _select(table: str, *, current_only: bool, predicate: str) -> str:
    """Build the join every record read needs.

    A payload row on its own is not a record: `retracted`, `created_at` and
    `created_by` live on `record_version`, and "current" means the head of that
    table. Both reads assemble the same thing, so they assemble it once here.
    """
    head_join = (
        "JOIN record_head AS h ON h.id = p.id AND h.version = p.version " if current_only else ""
    )
    columns = "p.*, rv.retracted, rv.created_at, rv.created_by"
    versions = "JOIN record_version AS rv ON rv.id = p.id AND rv.version = p.version"
    # `table` comes from this package's own frozen mapping table and `predicate`
    # is a literal written at the call site; neither reaches here from a caller.
    return f"SELECT {columns} FROM {table} AS p {head_join}{versions} WHERE {predicate}"  # noqa: S608


def _timestamp(at: datetime | None) -> datetime:
    """The write time, defaulting to now in UTC."""
    return datetime.now(UTC) if at is None else at


def open_repository(settings: Settings, *, create: bool = True) -> Repository:
    """Open the configured store, migrating it to the current schema.

    Args:
        settings: Where the store lives and how it journals.
        create: Whether to bring a missing database into existence. `praxis
            init` passes true; commands that only read pass false, so a mistyped
            `PRAXIS_DATA_DIR` is reported rather than created.
    """
    if create:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    connection = connect_from_settings(settings, create=create)
    try:
        migrate(connection)
    except BaseException:
        connection.close()
        raise
    return Repository(connection)
