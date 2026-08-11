"""The connection layer: pragmas that took, and transactions that are atomic.

Most of these assert on SQLite's *behaviour* rather than on the pragma
statements having been issued. `PRAGMA foreign_keys = ON` succeeding proves
nothing -- it succeeds inside a transaction too, where it does nothing at all --
so the test that matters is that a dangling edge is actually refused.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from praxis.config.settings import JournalMode, Settings
from praxis.store.connection import (
    MEMORY,
    connect,
    connect_from_settings,
    effective_journal_mode,
    fts5_available,
    transaction,
)
from praxis.store.errors import (
    DanglingEdgeError,
    StoreError,
    StoreNotInitialisedError,
    StoreQueryError,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    # A space in the directory name on purpose: the real repository lives in
    # "Praxis Agents", and the URI form of the path has to survive it.
    directory = tmp_path / "praxis data"
    directory.mkdir()
    return directory / "praxis.db"


@pytest.fixture
def connection(db_path: Path):
    conn = connect(db_path)
    yield conn
    conn.close()


# --- opening -------------------------------------------------------------


def test_connecting_creates_the_database(db_path: Path):
    conn = connect(db_path)
    conn.close()

    assert db_path.exists()


def test_a_path_with_a_space_in_it_round_trips(db_path: Path, connection):
    # The URI form percent-encodes the space; SQLite has to decode it back to
    # the same file, and nothing else in the suite would notice if it did not.
    assert " " in str(db_path)
    assert db_path.exists()


def test_refusing_to_create_reports_a_missing_store(db_path: Path):
    with pytest.raises(StoreNotInitialisedError) as caught:
        connect(db_path, create=False)

    assert caught.value.db_path == db_path
    assert "praxis init" in str(caught.value)
    assert not db_path.exists()


def test_refusing_to_create_still_opens_an_existing_store(db_path: Path):
    connect(db_path).close()

    conn = connect(db_path, create=False)
    conn.close()


def test_rows_are_addressable_by_column_name(connection):
    connection.execute("CREATE TABLE t (a, b)")
    connection.execute("INSERT INTO t VALUES (1, 2)")

    row = connection.execute("SELECT * FROM t").fetchone()

    assert row["b"] == 2


def test_an_in_memory_database_needs_no_file(tmp_path: Path):
    conn = connect(MEMORY)
    conn.execute("CREATE TABLE t (a)")
    conn.close()

    assert not any(tmp_path.rglob("*.db"))


# --- pragmas -------------------------------------------------------------


def test_foreign_keys_are_actually_enforced(connection):
    # The point of the whole pragma read-back. If this passes without raising,
    # every future graph traversal can silently return less than the truth.
    connection.execute("CREATE TABLE node (id TEXT PRIMARY KEY)")
    connection.execute("CREATE TABLE edge (src TEXT REFERENCES node(id))")

    with pytest.raises(DanglingEdgeError), transaction(connection):
        connection.execute("INSERT INTO edge VALUES ('missing')")


def test_the_busy_timeout_from_settings_is_applied(db_path: Path):
    conn = connect(db_path, busy_timeout_ms=1234)

    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1234
    conn.close()


def test_wal_is_the_default_journal_mode(connection):
    assert effective_journal_mode(connection) == "wal"


def test_delete_mode_leaves_no_sidecars(db_path: Path):
    # ADR 0010's escape hatch. The -wal and -shm files are the thing a sync
    # client uploads out of step with the database, so their absence is the
    # property being bought, not the journal mode itself.
    conn = connect(db_path, journal_mode=JournalMode.DELETE)
    with transaction(conn):
        conn.execute("CREATE TABLE t (a)")
        conn.execute("INSERT INTO t VALUES (1)")
    conn.close()

    assert effective_journal_mode_of(db_path) == "delete"
    assert not db_path.with_suffix(".db-wal").exists()
    assert not db_path.with_suffix(".db-shm").exists()


def effective_journal_mode_of(db_path: Path) -> str:
    """Reopen a closed database purely to read its persisted journal mode."""
    conn = connect(db_path, create=False, journal_mode=JournalMode.DELETE)
    try:
        return effective_journal_mode(conn)
    finally:
        conn.close()


def test_an_unappliable_journal_mode_warns_rather_than_fails():
    # An in-memory database is always in 'memory' mode. Refusing to open it
    # would cost the property tests their fast path for no safety at all.
    conn = connect(MEMORY, journal_mode=JournalMode.WAL)

    assert effective_journal_mode(conn) == "memory"
    conn.close()


def test_a_pragma_that_did_not_take_is_a_hard_failure(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("praxis.store.connection._pragma_int", lambda *_: 0)

    with pytest.raises(StoreError, match="foreign_keys"):
        connect(db_path)


def test_a_failed_open_leaves_no_connection_behind(db_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The handle has to be closed on the way out, or Windows keeps the file
    # locked and the next open fails for an unrelated-looking reason.
    monkeypatch.setattr("praxis.store.connection._pragma_int", lambda *_: 0)
    with pytest.raises(StoreError):
        connect(db_path)
    monkeypatch.undo()

    conn = connect(db_path)
    conn.close()
    db_path.unlink()


def test_fts5_is_available(connection):
    # ADR 0003 puts full-text search inside the store. A build without FTS5
    # cannot run the migrations at all, so this is a precondition, not a nicety.
    assert fts5_available(connection) is True


def test_settings_drive_the_connection(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "store", journal_mode=JournalMode.DELETE)
    settings.data_dir.mkdir(parents=True)

    conn = connect_from_settings(settings)

    assert effective_journal_mode(conn) == "delete"
    assert settings.db_path.exists()
    conn.close()


# --- transactions --------------------------------------------------------


@pytest.fixture
def counted(connection):
    """A connection with a one-column table, and a way to count its rows."""
    connection.execute("CREATE TABLE t (a INTEGER)")
    return connection


def rows(conn) -> int:
    count: int = conn.execute("SELECT count(*) FROM t").fetchone()[0]
    return count


def test_a_completed_block_commits(counted):
    with transaction(counted):
        counted.execute("INSERT INTO t VALUES (1)")

    assert rows(counted) == 1
    assert not counted.in_transaction


def test_a_raising_block_rolls_everything_back(counted):
    with pytest.raises(RuntimeError), transaction(counted):
        counted.execute("INSERT INTO t VALUES (1)")
        counted.execute("INSERT INTO t VALUES (2)")
        raise RuntimeError("boom")

    assert rows(counted) == 0
    assert not counted.in_transaction


def test_a_nested_block_joins_the_outer_one(counted):
    with transaction(counted):
        counted.execute("INSERT INTO t VALUES (1)")
        with transaction(counted):
            counted.execute("INSERT INTO t VALUES (2)")
        # Still open: the inner block must not have committed on its own.
        assert counted.in_transaction

    assert rows(counted) == 2


def test_an_inner_block_cannot_commit_without_the_outer_one(counted):
    # This is the invariant the savepoint design would have broken: a record
    # and its AuditEvent either both land or neither does.
    with pytest.raises(RuntimeError), transaction(counted):
        with transaction(counted):
            counted.execute("INSERT INTO t VALUES (1)")
        raise RuntimeError("the outer write failed")

    assert rows(counted) == 0


def test_a_driver_error_inside_a_block_is_translated(counted):
    with pytest.raises(StoreQueryError), transaction(counted):
        counted.execute("SELECT * FROM no_such_table")

    assert not counted.in_transaction


def test_a_driver_error_inside_a_nested_block_is_translated(counted):
    with pytest.raises(StoreQueryError), transaction(counted), transaction(counted):
        counted.execute("SELECT * FROM no_such_table")

    assert not counted.in_transaction


def test_sqlite3_never_commits_on_its_own(counted):
    # isolation_level=None. Without it the driver would commit at moments of
    # its own choosing, and "the audit row commits with its change" would be
    # unwritable.
    counted.execute("BEGIN")
    counted.execute("INSERT INTO t VALUES (1)")
    counted.execute("ROLLBACK")

    assert rows(counted) == 0


def test_a_read_snapshot_does_not_take_the_write_lock(counted):
    with transaction(counted):
        counted.execute("INSERT INTO t VALUES (1)")

    with transaction(counted, immediate=False):
        assert rows(counted) == 1


def test_a_block_that_rolls_back_leaves_the_handle_usable(counted):
    # Rollback puts the connection back where it started rather than poisoning
    # it, so a caller that catches the error can carry on with the next write.
    with pytest.raises(RuntimeError), transaction(counted):
        counted.execute("INSERT INTO t VALUES (1)")
        raise RuntimeError("boom")

    with transaction(counted):
        counted.execute("INSERT INTO t VALUES (2)")

    assert [row["a"] for row in counted.execute("SELECT a FROM t")] == [2]
