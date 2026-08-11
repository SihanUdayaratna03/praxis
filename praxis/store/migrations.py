"""Forward-only numbered migrations, without a migration framework.

ADR 0009. The reasoning is ADR 0004's: a dependency that owns the shape of the
data is harder to reason about than the fifty lines it replaces, and this
project's determinism guarantee means every schema change has to be legible.

What a framework would have given back, and how each is bought here instead:

- **Knowing what ran.** A `schema_version` ledger, written in the same
  transaction as the migration it records, so a crash cannot leave a migrated
  database that thinks it is unmigrated.
- **Knowing that what ran is what ships.** Every applied migration's checksum is
  stored and re-checked. Without it two machines can report schema version 2
  with different schemas, and every bug after that is a ghost.
- **Refusing to guess.** Migrations are forward-only, so a store from a newer
  build is an error with a clear message rather than a best-effort downgrade.
  Attempting one against an unknown schema is how a store that merely could not
  be read becomes a store that cannot be recovered.

The `schema_version` table itself is created by this module rather than by a
migration, because it is the table that answers "which migrations have run" and
cannot be one of the answers.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from importlib.resources import files
from typing import Final

from praxis.obs.logging import get_logger
from praxis.store.errors import (
    MigrationError,
    MigrationSequenceError,
    SchemaTooNewError,
    StoreError,
    translating_sqlite_errors,
)

_log = get_logger(__name__)

SCHEMA_ANCHOR: Final = "praxis.store"
SCHEMA_DIRECTORY: Final = "schema"

_FILENAME_RE: Final = re.compile(r"^(?P<version>\d{3})_(?P<name>[a-z0-9]+(?:_[a-z0-9]+)*)\.sql$")

_BOOTSTRAP: Final = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL PRIMARY KEY,
    name       TEXT    NOT NULL,
    checksum   TEXT    NOT NULL,
    applied_at TEXT    NOT NULL
) STRICT;

CREATE TRIGGER IF NOT EXISTS schema_version_is_append_only_update
BEFORE UPDATE ON schema_version
BEGIN SELECT RAISE(ABORT, 'append-only: the migration ledger is never rewritten'); END;

CREATE TRIGGER IF NOT EXISTS schema_version_is_append_only_delete
BEFORE DELETE ON schema_version
BEGIN SELECT RAISE(ABORT, 'append-only: the migration ledger is never erased'); END;
"""


@dataclass(frozen=True, slots=True)
class Migration:
    """One numbered `.sql` file that this build ships.

    Attributes:
        version: The number in the filename. Applied in ascending order.
        name: The slug after the number, kept for the ledger and the log.
        sql: The statements, newline-normalised.
        checksum: SHA-256 of `sql`, which is what detects a file edited after
            it was applied somewhere.
    """

    version: int
    name: str
    sql: str
    checksum: str

    @property
    def filename(self) -> str:
        """The name on disk, reconstructed rather than stored twice."""
        return f"{self.version:03d}_{self.name}.sql"


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """What one call to `migrate` did.

    Attributes:
        from_version: The schema version before the call. Zero for a new store.
        to_version: The schema version after it.
        applied: The migrations run, in the order they ran.
    """

    from_version: int
    to_version: int
    applied: tuple[Migration, ...]

    @property
    def changed(self) -> bool:
        """Whether anything was applied. False on the second run, always."""
        return bool(self.applied)


def _checksum(sql: str) -> str:
    """Hash a migration's text, ignoring how the platform ends its lines.

    Git may check the same file out with CRLF on Windows and LF elsewhere. A
    checksum that noticed would report drift on every developer machine, which
    is a check nobody keeps.
    """
    normalised = sql.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def discover_migrations() -> tuple[Migration, ...]:
    """Return every migration this build ships, in ascending version order.

    Read through `importlib.resources` rather than by walking `__file__`, so an
    installed wheel and a source checkout resolve the same files.

    Raises:
        MigrationSequenceError: if the filenames are not a gapless sequence
            starting at 1, or two files claim the same number. Both would make
            `schema_version` ambiguous about what the schema actually is.
    """
    directory = files(SCHEMA_ANCHOR) / SCHEMA_DIRECTORY
    migrations: list[Migration] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith(".sql"):
            continue
        match = _FILENAME_RE.match(entry.name)
        if match is None:
            message = (
                f"{entry.name!r} is not a migration filename; they are numbered like 001_core.sql"
            )
            raise MigrationSequenceError(message)
        sql = entry.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=int(match.group("version")),
                name=match.group("name"),
                sql=sql,
                checksum=_checksum(sql),
            )
        )

    expected = list(range(1, len(migrations) + 1))
    found = [migration.version for migration in migrations]
    if found != expected:
        message = f"migration numbers must be {expected}, found {found}"
        raise MigrationSequenceError(message)
    return tuple(migrations)


def latest_version() -> int:
    """The highest schema version this build knows how to produce."""
    return discover_migrations()[-1].version


def _bootstrap(connection: sqlite3.Connection) -> None:
    """Create the ledger if this database has never been migrated."""
    with translating_sqlite_errors():
        connection.executescript(f"BEGIN IMMEDIATE;\n{_BOOTSTRAP}\nCOMMIT;")


def current_version(connection: sqlite3.Connection) -> int:
    """Return the schema version of a store, or 0 if it has none yet."""
    _bootstrap(connection)
    with translating_sqlite_errors():
        row = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()
    return int(row[0])


def applied_checksums(connection: sqlite3.Connection) -> dict[int, str]:
    """Return the checksum recorded for each applied migration."""
    _bootstrap(connection)
    with translating_sqlite_errors():
        rows = connection.execute("SELECT version, checksum FROM schema_version").fetchall()
    return {int(row[0]): str(row[1]) for row in rows}


def verify(connection: sqlite3.Connection) -> None:
    """Check that this store's history matches the migrations this build ships.

    Called before applying anything, and by `praxis doctor` on its own. The two
    failures it exists to catch are a store from a newer build, and a migration
    file edited after it had already been applied somewhere.

    Raises:
        SchemaTooNewError: if the store is ahead of this build.
        MigrationSequenceError: if an applied migration's text has changed.
    """
    known = {migration.version: migration for migration in discover_migrations()}
    recorded = applied_checksums(connection)

    highest = max(recorded, default=0)
    if highest > latest_version():
        raise SchemaTooNewError(found=highest, known=latest_version())

    for version, checksum in sorted(recorded.items()):
        migration = known[version]
        if migration.checksum != checksum:
            message = (
                f"{migration.filename} has changed since it was applied to this store "
                f"(recorded {checksum[:12]}, shipped {migration.checksum[:12]}); "
                f"migrations are forward-only, so write a new one instead of editing it"
            )
            raise MigrationSequenceError(message)


def migrate(connection: sqlite3.Connection) -> MigrationResult:
    """Bring a store up to the schema version this build ships.

    Idempotent: on a store that is already current, nothing runs and the result
    reports no change. Each migration is applied in its own transaction
    together with its ledger row, so an interrupted run leaves a store at a
    version that is genuinely the version it claims.

    Raises:
        MigrationError: if a migration failed. Nothing from that migration is
            left behind.
        SchemaTooNewError: if the store came from a newer build.
        MigrationSequenceError: if the shipped files disagree with the ledger.
    """
    verify(connection)
    started_at = current_version(connection)
    pending = [m for m in discover_migrations() if m.version > started_at]

    for migration in pending:
        _apply(connection, migration)
        _log.info("migration_applied", version=migration.version, name=migration.name)

    return MigrationResult(
        from_version=started_at,
        to_version=current_version(connection),
        applied=tuple(pending),
    )


def _apply(connection: sqlite3.Connection, migration: Migration) -> None:
    """Run one migration and record it, in a single transaction.

    `executescript` performs an implicit COMMIT before it runs and no
    transaction control of its own, so the BEGIN goes inside the script and the
    COMMIT stays here. That is what lets the ledger row -- which needs bound
    parameters, and so cannot be part of the script -- be written inside the
    same transaction as the schema change it records.
    """
    try:
        with translating_sqlite_errors():
            connection.executescript(f"BEGIN IMMEDIATE;\n{migration.sql}")
            connection.execute(
                "INSERT INTO schema_version (version, name, checksum, applied_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    datetime.now(UTC).isoformat(),
                ),
            )
            connection.commit()
    except BaseException as exc:
        with contextlib.suppress(sqlite3.Error):
            connection.rollback()
        if isinstance(exc, StoreError):
            message = f"{migration.filename} failed and was rolled back: {exc}"
            raise MigrationError(message) from exc
        raise
