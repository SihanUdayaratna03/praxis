"""Opening the database, and the transaction boundary every write sits inside.

Three decisions live here, and each one closes a hole that would otherwise be
discovered much later and much more expensively.

**Pragmas are set per connection, not once per database.** `foreign_keys` and
`busy_timeout` are connection state that SQLite resets to its own defaults on
every new handle -- `foreign_keys` defaults to *off*. A store whose referential
integrity depends on a pragma nobody re-applied is a store where
`DanglingEdgeError` never fires and every traversal quietly returns a shorter
answer than the truth. So `connect` is the only way this package opens a handle,
and it verifies the pragmas took rather than assuming they did.

**Transactions begin `IMMEDIATE`.** SQLite's default `DEFERRED` takes the write
lock at the first write statement, not at `BEGIN`, and a lock upgrade that finds
a competing writer raises `SQLITE_BUSY` *without* honouring `busy_timeout` --
retrying inside a transaction could break snapshot isolation, so SQLite refuses
to. ADR 0010 set `busy_timeout` precisely so a sync client's brief lock retries
instead of surfacing; `DEFERRED` would have quietly exempted every write from
it. See https://www.sqlite.org/lang_transaction.html#immediate.

**Python's implicit transaction handling is off** (`isolation_level=None`), so
the `BEGIN` and `COMMIT` in this module are the only ones. The audit trail must
commit in the same transaction as the change it describes -- an audit row that
can be lost independently of its change is worse than no audit row, because it
looks trustworthy -- and that guarantee is unwritable while a driver is free to
commit at moments of its own choosing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from praxis.config.settings import JournalMode, Settings
from praxis.obs.logging import get_logger
from praxis.store.errors import (
    StoreError,
    StoreNotInitialisedError,
    translating_sqlite_errors,
)

_log = get_logger(__name__)

MEMORY: Final = Path(":memory:")
"""A throwaway database that exists only for the life of the connection.

Used by the property tests, where a real file per example would make the
hypothesis budget disappear into the filesystem. Journalling does not apply to
it, which is why the journal-mode check below reports rather than insists.
"""

SYNCHRONOUS: Final = "FULL"
"""Fsync on every commit.

The usual advice pairs WAL with `NORMAL`, which trades the durability of the
last few commits for speed. This store is an audit trail measured in thousands
of rows, not millions: the throughput that trade buys is worth nothing here, and
the thing it spends is the property the trail exists to have.
"""


def _dsn(db_path: Path) -> tuple[str, bool]:
    """Return the connect string for a path, and whether it is a URI.

    URI form is used for real files because it is the only way to ask SQLite
    *not* to create a missing database. The plain form silently creates one, so
    a typo in `PRAXIS_DATA_DIR` would produce an empty store and the error
    "no such table" rather than "that store does not exist".
    """
    if db_path == MEMORY:
        return str(MEMORY), False
    return db_path.resolve().as_uri(), True


def connect(
    db_path: Path,
    *,
    journal_mode: JournalMode = JournalMode.WAL,
    busy_timeout_ms: int = 5_000,
    create: bool = True,
    cross_thread: bool = False,
) -> sqlite3.Connection:
    """Open a configured connection to the store.

    Args:
        db_path: The database file, or `MEMORY` for a transient one.
        journal_mode: WAL unless ADR 0010's synced-path escape hatch is in use.
        busy_timeout_ms: How long a blocked write waits before raising.
        create: When false, refuse to bring a database into existence. The
            reading commands pass false so that a mistyped path is reported as
            a missing store instead of being created as an empty one.
        cross_thread: Allow the handle to be used from a thread other than the
            one that opened it. Only the read-only dashboard passes true, and
            it serialises every read behind a lock. Never pass it for anything
            that writes.

    Returns:
        A connection in manual transaction mode, with `sqlite3.Row` rows.

    Raises:
        StoreNotInitialisedError: if `create` is false and there is no file.
        StoreUnavailableError: if the environment denied the file.
        StoreError: if a pragma that the store's guarantees rest on did not
            take effect.
    """
    if not create and db_path != MEMORY and not db_path.exists():
        raise StoreNotInitialisedError(db_path)

    dsn, is_uri = _dsn(db_path)
    if is_uri:
        dsn = f"{dsn}?mode={'rwc' if create else 'rw'}"

    with translating_sqlite_errors():
        # isolation_level=None: see the module docstring. check_same_thread is
        # on by default -- this is a single-writer store, and a handle that
        # quietly works from two threads is a race. `cross_thread` turns it off
        # only where a caller has said why in writing: the dashboard's server
        # hands requests to a threadpool and holds a lock across each one.
        connection = sqlite3.connect(
            dsn, uri=is_uri, isolation_level=None, check_same_thread=not cross_thread
        )
    connection.row_factory = sqlite3.Row
    try:
        _apply_pragmas(connection, journal_mode=journal_mode, busy_timeout_ms=busy_timeout_ms)
    except BaseException:
        connection.close()
        raise
    return connection


def connect_from_settings(settings: Settings, *, create: bool = True) -> sqlite3.Connection:
    """Open the store this process is configured to use."""
    return connect(
        settings.db_path,
        journal_mode=settings.journal_mode,
        busy_timeout_ms=settings.busy_timeout_ms,
        create=create,
    )


def _apply_pragmas(
    connection: sqlite3.Connection, *, journal_mode: JournalMode, busy_timeout_ms: int
) -> None:
    """Set the connection-scoped pragmas and confirm the load-bearing ones."""
    with translating_sqlite_errors():
        connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms:d}")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA synchronous = {SYNCHRONOUS}")
        effective = connection.execute(f"PRAGMA journal_mode = {journal_mode.value}").fetchone()[0]

    if not _pragma_int(connection, "foreign_keys"):
        message = (
            "PRAGMA foreign_keys did not take effect, so the store cannot enforce "
            "that an edge points at rows that exist"
        )
        raise StoreError(message)

    if effective.lower() != journal_mode.value:
        # Not an error. An in-memory database is always in 'memory' mode, and a
        # filesystem that cannot support WAL leaves the safer rollback journal
        # in place. Both are worth knowing and neither is worth refusing.
        _log.warning("journal_mode_not_applied", requested=journal_mode.value, effective=effective)


def _pragma_int(connection: sqlite3.Connection, name: str) -> int:
    """Read back a boolean or integer pragma."""
    with translating_sqlite_errors():
        row = connection.execute(f"PRAGMA {name}").fetchone()
    return int(row[0])


def effective_journal_mode(connection: sqlite3.Connection) -> str:
    """Return the journal mode the database is actually in.

    Read back rather than assumed, because `praxis doctor` reporting the mode
    that was requested would defeat the point of reporting it at all.
    """
    with translating_sqlite_errors():
        row = connection.execute("PRAGMA journal_mode").fetchone()
    mode: str = row[0]
    return mode


def fts5_available(connection: sqlite3.Connection) -> bool:
    """Report whether this SQLite build has the FTS5 extension compiled in.

    ADR 0003 puts full-text search in the store rather than beside it, so a
    build without FTS5 cannot run the migrations. Checked by asking SQLite to
    prepare a statement against a virtual table it would have to create --
    `compile_options` is not available on every build, and a capability test
    that is itself optional is not a capability test.
    """
    try:
        with translating_sqlite_errors():
            connection.execute("CREATE VIRTUAL TABLE temp.praxis_fts5_probe USING fts5(x)")
            connection.execute("DROP TABLE temp.praxis_fts5_probe")
    except StoreError:
        return False
    return True


@contextmanager
def transaction(connection: sqlite3.Connection, *, immediate: bool = True) -> Iterator[None]:
    """Run a block inside one transaction, committing it or rolling it all back.

    Re-entrant by joining rather than nesting: a block opened while a
    transaction is already running becomes part of it, and does not commit on
    its own. That is deliberate rather than a simplification of savepoints. The
    repository writes a record and its `AuditEvent` through two calls that each
    want a transaction, and the invariant is that those two either both land or
    neither does -- a savepoint the inner block could release independently
    would be a way to commit half of that.

    Args:
        connection: The connection to run on.
        immediate: Take the write lock at `BEGIN`. Pass false only for a
            read-only block that wants a consistent snapshot across several
            queries, such as a graph walk.

    Yields:
        Nothing. The block runs, and its statements commit together.

    Raises:
        StoreError: whatever the block raised, translated, after the rollback.
    """
    if connection.in_transaction:
        with translating_sqlite_errors():
            yield
        return

    with translating_sqlite_errors():
        connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        with translating_sqlite_errors():
            yield
    except BaseException:
        # Rollback failure must not replace the exception that caused it: the
        # original is the one that says what went wrong.
        try:
            connection.rollback()
        except sqlite3.Error:  # pragma: no cover -- needs a broken handle
            _log.exception("rollback_failed")
        raise
    with translating_sqlite_errors():
        connection.commit()
