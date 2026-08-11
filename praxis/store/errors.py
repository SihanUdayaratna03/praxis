"""The store's exception vocabulary, and the one place `sqlite3` errors stop.

`praxis/store/__init__.py` states the boundary: no module outside this package
may import `sqlite3`. That boundary is only real if it also holds for failures.
A caller forced to write `except sqlite3.IntegrityError` has imported the driver
just as surely as one that writes a query, and ADR 0003's claim that the backend
is replaceable dies quietly at that `except` clause. So every `sqlite3.Error`
leaving this package is translated into one of the classes below first.

The translation is by result code, never by matching the message text. SQLite's
messages are prose and change between versions; the extended result codes are a
documented, stable contract -- see https://www.sqlite.org/rescode.html. Python
surfaces them as `sqlite_errorcode` and `sqlite_errorname` on any exception the
driver itself raised (3.11+):
https://docs.python.org/3/library/sqlite3.html#sqlite3.Error.sqlite_errorcode

The distinctions drawn here are the ones a caller would act on differently:
retry, warn the owner about their filesystem, fix a bug, or re-run `praxis init`.
Two failures that call for the same response do not get two classes.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
from typing import Final

from praxis.domain.enums import RecordKind


class StoreError(Exception):
    """Base class for every failure raised out of `praxis.store`.

    Catching this catches the whole storage layer without naming the backend,
    which is the entire point of the module.
    """


class StoreNotInitialisedError(StoreError):
    """Raised when the database exists but carries no schema Praxis recognises.

    Kept distinct from a query failing with "no such table" because the remedy
    is a command the owner can run, not a bug report.

    Attributes:
        db_path: Where the store was expected.
    """

    def __init__(self, db_path: Path) -> None:
        """Record the path and build a message naming the fix.

        Args:
            db_path: The database file that has no schema.
        """
        self.db_path = db_path
        super().__init__(f"no Praxis schema at {db_path}; run `praxis init` to create it")


class StoreLockedError(StoreError):
    """Raised when a write could not acquire its lock before `busy_timeout`.

    On a developer machine this almost always means something outside Praxis
    has the file open -- a sync client uploading it, or a virus scanner reading
    it. ADR 0010 sets `busy_timeout` precisely so a brief external lock retries
    instead of surfacing; reaching this exception means the lock outlasted it,
    so the message points at the cause rather than suggesting another retry.
    """


class StoreCorruptError(StoreError):
    """Raised when SQLite reports the file is damaged or is not a database.

    This is the failure ADR 0003 assumption 5 counts (`db_corruption_events ==
    0`, expiring at `phases_completed >= 3`). It is a distinct class so that
    counting those events is a matter of catching a type rather than grepping
    log messages, and so the assumption's predicate has something honest to
    read. If this is ever raised, the assumption is breached -- record it.
    """


class StoreUnavailableError(StoreError):
    """Raised when the environment denied the store, not the data.

    Missing directory, read-only file, full disk, I/O error. Grouped because
    the response is identical for all of them: none is retryable in-process,
    and every one is repaired outside Praxis.
    """


class StoreQueryError(StoreError):
    """Raised when SQLite rejected the statement itself.

    Bad syntax, an unknown table or column, the wrong number of bindings. This
    is always a bug in Praxis rather than a condition in the data or the
    environment, and it is separated for that reason: it should fail a test,
    never be handled at runtime.
    """


class IntegrityViolationError(StoreError):
    """Raised when a write would have broken a constraint the schema enforces.

    The base covers the constraints with no interesting story -- `NOT NULL`,
    `CHECK` -- while the subclasses name the two the graph depends on.
    """


class DanglingEdgeError(IntegrityViolationError):
    """Raised when an edge references a node that is not in the store.

    Referential integrity is asserted in the tests *and* enforced by SQLite's
    foreign keys, because a graph whose edges can point at nothing makes every
    traversal a source of silent wrong answers rather than a loud failure.
    """


class DuplicateRecordError(IntegrityViolationError):
    """Raised when a write collides with a primary key or unique constraint.

    For a content-addressed id this means the same span or edge was derived
    twice, which is expected and is handled by writing a new version -- see
    `praxis.domain.ids`. Reaching this exception means that path was bypassed.
    """


class AppendOnlyViolationError(IntegrityViolationError):
    """Raised when a statement tried to update or delete an existing row.

    CLAUDE.md invariant 7: a change is a new version plus an `AuditEvent`.
    The schema enforces it with `BEFORE UPDATE`/`BEFORE DELETE` triggers that
    `RAISE(ABORT, ...)`, which SQLite reports as `SQLITE_CONSTRAINT_TRIGGER`,
    so the invariant holds against hand-written SQL and not only against the
    repository's own methods.
    """


class RecordNotFoundError(StoreError):
    """Raised when a lookup that the caller required to succeed found nothing.

    Read methods that tolerate absence return `None` instead. This exists for
    the callers that would otherwise dereference that `None` one line later.

    Attributes:
        kind: The record kind looked up.
        record_id: The id that matched no row.
        version: The version asked for, or `None` when the current one was.
    """

    def __init__(self, kind: RecordKind, record_id: str, version: int | None = None) -> None:
        """Record what was missing.

        Args:
            kind: The record kind looked up.
            record_id: The id that matched no row.
            version: The specific version requested, if it was not the current.
        """
        self.kind = kind
        self.record_id = record_id
        self.version = version
        at_version = "" if version is None else f" at version {version}"
        super().__init__(f"no {kind.value} {record_id!r}{at_version} in the store")


class VersionConflictError(StoreError):
    """Raised when a caller revised a version that is no longer the current one.

    Append-only storage removes the lost-update problem for the *data* -- the
    old version is still there -- but not for intent: a caller that read
    version 3, decided something, and wrote version 4 has decided against
    stale facts if version 4 already exists. Detecting that is cheaper than
    reconciling it afterwards.

    Attributes:
        kind: The record kind being revised.
        record_id: The record being revised.
        expected: The version the caller read.
        found: The version the store actually holds.
    """

    def __init__(self, kind: RecordKind, record_id: str, expected: int, found: int) -> None:
        """Record both versions.

        Args:
            kind: The record kind being revised.
            record_id: The record being revised.
            expected: The version the caller believed was current.
            found: The version that is current.
        """
        self.kind = kind
        self.record_id = record_id
        self.expected = expected
        self.found = found
        super().__init__(
            f"{kind.value} {record_id!r} moved on: revision was written against "
            f"version {expected}, the store is at {found}"
        )


class MigrationError(StoreError):
    """Raised when the schema could not be brought to the version Praxis needs."""


class MigrationSequenceError(MigrationError):
    """Raised when the migration files are not a usable forward-only sequence.

    A gap, a duplicate number, or a file whose contents changed after it was
    applied. ADR 0009 chose numbered files over a framework, and this is the
    check that buys back what the framework would have provided: without it,
    two machines can report the same `schema_version` with different schemas.
    """


class SchemaTooNewError(MigrationError):
    """Raised when the store is at a schema version this build does not know.

    Migrations are forward-only, so there is nothing to do but say so plainly.
    Attempting a best-effort downgrade against an unknown schema is how a
    store that merely could not be read becomes a store that cannot be
    recovered.

    Attributes:
        found: The `schema_version` in the database.
        known: The highest migration this build ships.
    """

    def __init__(self, found: int, known: int) -> None:
        """Record both versions.

        Args:
            found: The schema version the database reports.
            known: The highest migration number this build ships.
        """
        self.found = found
        self.known = known
        super().__init__(
            f"store is at schema version {found}, this build knows up to {known}; "
            f"migrations are forward-only, so upgrade Praxis rather than the store"
        )


_PRIMARY_CODE_MASK: Final = 0xFF
"""Extended result codes are `primary | (subcode << 8)`, so the low byte is the
primary code. https://www.sqlite.org/rescode.html#extrc"""

_BY_EXTENDED_CODE: Final[Mapping[int, type[StoreError]]] = MappingProxyType(
    {
        sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY: DanglingEdgeError,
        sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY: DuplicateRecordError,
        sqlite3.SQLITE_CONSTRAINT_UNIQUE: DuplicateRecordError,
        sqlite3.SQLITE_CONSTRAINT_TRIGGER: AppendOnlyViolationError,
    }
)
"""Extended codes worth a class of their own. Checked before the primary map,
which is what lets a foreign key failure and a `CHECK` failure -- both plain
`SQLITE_CONSTRAINT` -- land in different places."""

_BY_PRIMARY_CODE: Final[Mapping[int, type[StoreError]]] = MappingProxyType(
    {
        sqlite3.SQLITE_BUSY: StoreLockedError,
        sqlite3.SQLITE_LOCKED: StoreLockedError,
        sqlite3.SQLITE_CORRUPT: StoreCorruptError,
        sqlite3.SQLITE_NOTADB: StoreCorruptError,
        sqlite3.SQLITE_CANTOPEN: StoreUnavailableError,
        sqlite3.SQLITE_READONLY: StoreUnavailableError,
        sqlite3.SQLITE_IOERR: StoreUnavailableError,
        sqlite3.SQLITE_FULL: StoreUnavailableError,
        sqlite3.SQLITE_PERM: StoreUnavailableError,
        sqlite3.SQLITE_NOMEM: StoreUnavailableError,
        sqlite3.SQLITE_CONSTRAINT: IntegrityViolationError,
        sqlite3.SQLITE_ERROR: StoreQueryError,
        sqlite3.SQLITE_MISUSE: StoreQueryError,
    }
)


def translate_sqlite_error(exc: sqlite3.Error) -> StoreError:
    """Return the `StoreError` that corresponds to a driver exception.

    Args:
        exc: The exception raised by `sqlite3`.

    Returns:
        A `StoreError` subclass carrying SQLite's own message. The message is
        reused verbatim because it names the table, column or constraint that
        failed, which no wording invented here would improve on.

    Note:
        `sqlite_errorcode` is absent on exceptions the Python layer raises
        rather than SQLite -- `ProgrammingError` for a wrong binding count is
        the common one -- so its absence is treated as "a bad statement",
        which is what those cases are.
    """
    code = getattr(exc, "sqlite_errorcode", None)
    if code is None:
        return StoreQueryError(str(exc))
    error_class = _BY_EXTENDED_CODE.get(code)
    if error_class is None:
        error_class = _BY_PRIMARY_CODE.get(code & _PRIMARY_CODE_MASK, StoreError)
    return error_class(str(exc))


@contextmanager
def translating_sqlite_errors() -> Iterator[None]:
    """Re-raise any `sqlite3.Error` from the block as a `StoreError`.

    Wrap every statement the store executes in this. The original exception is
    kept as `__cause__`, so the driver's traceback survives for debugging while
    the type callers see stays inside Praxis's own vocabulary.

    Yields:
        Nothing. The block runs unchanged unless it raises.
    """
    try:
        yield
    except sqlite3.Error as exc:
        raise translate_sqlite_error(exc) from exc
