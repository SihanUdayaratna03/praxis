"""ADR 0009: the ledger, the checksums, and the atomicity between them.

The tests that matter here are the ones about failure. A migration runner that
works is easy; the properties worth asserting are that an interrupted migration
leaves nothing behind, that a store cannot report a version it did not reach,
and that a file edited after it shipped is caught rather than tolerated.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from praxis.store import migrations
from praxis.store.connection import MEMORY, connect, transaction
from praxis.store.errors import (
    AppendOnlyViolationError,
    MigrationError,
    MigrationSequenceError,
    SchemaTooNewError,
)
from praxis.store.migrations import (
    _checksum,
    applied_checksums,
    current_version,
    discover_migrations,
    latest_version,
    migrate,
    verify,
)


@pytest.fixture
def store():
    conn = connect(MEMORY)
    yield conn
    conn.close()


@pytest.fixture
def fake_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point discovery at a directory the test controls."""
    directory = tmp_path / "schema"
    directory.mkdir()
    monkeypatch.setattr(migrations, "files", lambda _anchor: tmp_path)
    discover_migrations.cache_clear()
    yield directory
    discover_migrations.cache_clear()


# --- discovery -----------------------------------------------------------


def test_the_shipped_migrations_are_a_gapless_sequence():
    found = discover_migrations()

    assert [m.version for m in found] == list(range(1, len(found) + 1))
    assert latest_version() == len(found)


def test_more_than_one_migration_ships():
    # A runner exercised on a single file has not had its sequencing tested at
    # all, which is why the initial schema shipped as two.
    assert len(discover_migrations()) >= 2


def test_a_gap_in_the_numbering_is_refused(fake_schema: Path):
    (fake_schema / "001_core.sql").write_text("SELECT 1;", encoding="utf-8")
    (fake_schema / "003_later.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(MigrationSequenceError, match="migration numbers"):
        discover_migrations()


def test_a_filename_that_is_not_a_migration_is_refused(fake_schema: Path):
    (fake_schema / "001_core.sql").write_text("SELECT 1;", encoding="utf-8")
    (fake_schema / "cleanup.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(MigrationSequenceError, match="not a migration filename"):
        discover_migrations()


def test_non_sql_files_are_ignored(fake_schema: Path):
    (fake_schema / "001_core.sql").write_text("SELECT 1;", encoding="utf-8")
    (fake_schema / "README.md").write_text("notes", encoding="utf-8")

    assert len(discover_migrations()) == 1


def test_the_checksum_ignores_line_endings():
    # Git hands the same file to Windows with CRLF and to CI with LF. A check
    # that reported drift on every developer machine is a check that gets
    # deleted, so this is what keeps assumption 4 of ADR 0009 alive.
    assert _checksum("CREATE TABLE a (x);\nSELECT 1;\n") == _checksum(
        "CREATE TABLE a (x);\r\nSELECT 1;\r\n"
    )


def test_the_checksum_still_notices_a_real_edit():
    assert _checksum("CREATE TABLE a (x);") != _checksum("CREATE TABLE a (y);")


# --- applying ------------------------------------------------------------


def test_a_fresh_store_migrates_to_the_latest_version(store):
    assert current_version(store) == 0

    result = migrate(store)

    assert result.from_version == 0
    assert result.to_version == latest_version()
    assert result.changed
    assert current_version(store) == latest_version()


def test_migrating_twice_applies_nothing(store):
    migrate(store)

    result = migrate(store)

    assert result.applied == ()
    assert not result.changed
    assert result.from_version == result.to_version == latest_version()


def test_the_ledger_records_every_migration(store):
    migrate(store)

    recorded = applied_checksums(store)

    assert recorded == {m.version: m.checksum for m in discover_migrations()}


def test_the_ledger_is_append_only(store):

    migrate(store)

    with pytest.raises(AppendOnlyViolationError), transaction(store):
        store.execute("UPDATE schema_version SET checksum = 'forged'")


def test_the_store_is_structurally_sound_afterwards(store):
    migrate(store)

    assert store.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert store.execute("PRAGMA foreign_key_check").fetchall() == []


def test_every_expected_object_exists(store):
    migrate(store)

    names = {
        row["name"]
        for row in store.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
    }

    assert {"node", "record_version", "link", "audit_event", "finding_evidence"} <= names
    assert {"record_head", "current_record", "current_link", "dependency_edge"} <= names
    assert "search" in names


def test_only_the_pending_migrations_run(store, fake_schema: Path):
    (fake_schema / "001_one.sql").write_text("CREATE TABLE one (x);", encoding="utf-8")
    migrate(store)
    discover_migrations.cache_clear()
    (fake_schema / "002_two.sql").write_text("CREATE TABLE two (x);", encoding="utf-8")

    result = migrate(store)

    assert [m.name for m in result.applied] == ["two"]
    assert result.from_version == 1
    assert result.to_version == 2


# --- refusing ------------------------------------------------------------


def test_a_store_from_a_newer_build_is_refused(store, fake_schema: Path):
    (fake_schema / "001_one.sql").write_text("CREATE TABLE one (x);", encoding="utf-8")
    (fake_schema / "002_two.sql").write_text("CREATE TABLE two (x);", encoding="utf-8")
    migrate(store)

    # The same store, opened by a build that ships only the first migration.
    (fake_schema / "002_two.sql").unlink()
    discover_migrations.cache_clear()

    with pytest.raises(SchemaTooNewError) as caught:
        migrate(store)

    assert caught.value.found == 2
    assert caught.value.known == 1
    assert "forward-only" in str(caught.value)


def test_a_migration_edited_after_it_was_applied_is_caught(store, fake_schema: Path):
    migration = fake_schema / "001_one.sql"
    migration.write_text("CREATE TABLE one (x);", encoding="utf-8")
    migrate(store)

    migration.write_text("CREATE TABLE one (x, y);", encoding="utf-8")
    discover_migrations.cache_clear()

    with pytest.raises(MigrationSequenceError, match="has changed since it was applied"):
        verify(store)


def test_a_failing_migration_leaves_nothing_behind(store, fake_schema: Path):
    (fake_schema / "001_one.sql").write_text("CREATE TABLE one (x);", encoding="utf-8")
    (fake_schema / "002_broken.sql").write_text(
        "CREATE TABLE two (x);\nCREATE TABLE two (x);", encoding="utf-8"
    )

    with pytest.raises(MigrationError, match=r"002_broken\.sql"):
        migrate(store)

    # The first migration stands, the second is entirely absent, and the ledger
    # agrees with both. A store that reported version 2 here would be the exact
    # failure the in-transaction ledger row exists to prevent.
    assert current_version(store) == 1
    assert _tables(store) == {"one", "schema_version"}
    assert not store.in_transaction


def test_a_failed_migration_can_be_retried_after_the_fix(store, fake_schema: Path):
    (fake_schema / "001_one.sql").write_text("CREATE TABLE one (x);", encoding="utf-8")
    broken = fake_schema / "002_two.sql"
    broken.write_text("CREATE TABLE two (x);\nCREATE TABLE two (x);", encoding="utf-8")
    with pytest.raises(MigrationError):
        migrate(store)

    broken.write_text("CREATE TABLE two (x);", encoding="utf-8")
    discover_migrations.cache_clear()

    assert migrate(store).to_version == 2


def _tables(connection) -> set[str]:
    return {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
