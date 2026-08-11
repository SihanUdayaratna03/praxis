---
id: ADR-0009
status: accepted
date: 2026-08-12
decision_maker: Sihan Udayaratna
impact: medium
supersedes: null
superseded_by: null
---

# 0009 — Forward-only numbered migrations, without a framework

## Chosen

Schema changes are numbered `.sql` files in `praxis/store/schema/`, applied in
ascending order by ~250 lines in `praxis/store/migrations.py`. No Alembic, no
ORM.

Five properties, each bought explicitly:

1. **A ledger.** `schema_version` records `(version, name, checksum,
   applied_at)`. It is created by the runner rather than by a migration,
   because it is the table that answers "which migrations have run" and so
   cannot be one of the answers.
2. **The ledger row commits with its migration.** `executescript` performs an
   implicit `COMMIT` before it runs and no transaction control of its own, so
   the `BEGIN IMMEDIATE` goes inside the script and the `COMMIT` stays in
   Python — which is what lets the parameterised ledger insert sit inside the
   same transaction as the DDL it records.
3. **Checksums, verified on every open.** A file edited after it was applied is
   an error naming the file. Line endings are normalised before hashing.
4. **Forward-only.** A store from a newer build raises `SchemaTooNewError`.
   There is no `downgrade` function to write, and therefore none to get wrong.
5. **Idempotent.** Running `migrate` on a current store applies nothing and
   reports no change, so `praxis init` is safe to run twice.

## Rejected

| Option | Why not |
| ------ | ------- |
| Alembic | The standard answer, and it assumes SQLAlchemy — which this project does not use and will not, since ADR 0003's whole claim is that the store is one SQLite file behind a repository. Adopting it would mean adopting an ORM to describe a schema that is already written in SQL, and its autogenerate step compares models to a database, which is the wrong direction when the SQL is the source of truth. |
| `yoyo-migrations` or a similar small runner | Genuinely close, and would have worked. Rejected on the same reasoning as ADR 0004: this is ~250 lines whose behaviour has to be understood exactly — what runs in which transaction, what happens on a crash between the DDL and the ledger row — and reading someone else's answer to that costs more than writing it. A dependency is also a supply-chain surface for a project whose one hard rule is that a fresh clone runs offline with no credentials. |
| `PRAGMA user_version` instead of a ledger table | One integer, free, and the usual advice for SQLite. It records only *how far* the schema got, not *what* was applied — so it cannot hold a checksum, and the failure it cannot catch is the one that matters: two machines reporting version 2 with different schemas because a file was edited after it shipped. Every bug after that point is a ghost. |
| Reversible migrations with a `downgrade` half | Doubles the SQL and halves the confidence, because a downgrade path is exercised only in the emergency it exists for. Praxis's store is derived data — gitignored and rebuildable from the corpus by `praxis init` — so the honest recovery from a bad migration is to delete the file and re-ingest, not to unwind it. |
| One `schema.sql` reapplied with `CREATE TABLE IF NOT EXISTS` | Simplest possible, and it silently does nothing when a column is added to an existing table. That is the failure mode where the code and the database disagree and neither says so. |
| Migrations as Python functions rather than `.sql` files | Would allow data migrations, which numbered SQL cannot express well. Not needed yet, and a Python migration cannot be read with `sqlite3` on the file to see what a store actually contains. Revisit when the first data migration is genuinely needed — it is in `BACKLOG.md`, not here. |

## What was known at the time

Phase 1 is the phase that creates the schema, so there is exactly one
migration's worth of history and no legacy stores anywhere. That makes this the
cheapest possible moment to choose, and it is why the schema shipped as **two**
files (`001_core.sql`, `002_search.sql`) rather than one: a runner tested on a
single migration is a runner whose sequencing has not been tested at all.

The behaviour of `sqlite3.Connection.executescript` was verified against the
installed driver rather than recalled — it commits any pending transaction
before running and performs no other transaction control, which is what makes
property 2 above possible and would have made the obvious arrangement
(`BEGIN` in Python, script inside) silently non-atomic.

The store is derived data. `data_dir` is outside the repository by ADR 0010,
gitignored, and rebuildable from `docs/` and the corpus, so the blast radius of
a bad migration is one `praxis init`, not a restore.

One refinement to ADR 0008 became clear while writing the schema. That ADR
called for a `<kind>_current` view per entity table. What shipped is three
views — `record_head`, `current_record`, `current_link` — plus a generic
repository read that joins `record_head` to the payload table by kind. The
property ADR 0008 actually wanted ("no caller filters by version by hand")
holds; nine near-identical views would have been nine things to keep in step
for no additional guarantee.

Not known: whether a data migration will be needed before Phase 12, and
therefore whether SQL-only files remain sufficient. Also not known how long a
migration takes against a corpus-sized store, since nothing has yet been
ingested at volume.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | Schema changes stay expressible as SQL, with no data migration needed | `python_migrations_needed == 0` | `on_event("a migration must rewrite existing rows")` |
| 2 | The migration count stays small enough that applying them all on a fresh store is instant | `migration_count <= 20` | `when(phases_completed >= 8)` |
| 3 | Nobody needs to downgrade a store, because deleting and re-ingesting is cheaper | `downgrade_requests == 0` | `on_event("a store holds data that cannot be re-derived")` |
| 4 | Checksum verification never fires spuriously across platforms | `spurious_checksum_failures == 0` | `when(ci_runs > 100)` |
| 5 | A partially applied migration is impossible, so no repair tooling is needed | `partial_migrations == 0` | `when(phases_completed >= 6)` |

## Consequences

**Accepted costs.** A schema change is a new file, never an edit — fixing a
typo in `001_core.sql` after it has been applied anywhere means `003_*.sql`,
which is more ceremony than editing the file and is the entire point.
`praxis doctor` and `praxis init` both re-read and re-hash every migration file,
which is negligible at this size and would not be at a thousand.

Assumption 4 is the one most likely to fire, and CI running on Windows and Linux
is what would fire it: line endings are normalised before hashing, but a file
committed with a BOM or re-encoded would still hash differently. Assumption 1 is
the one most likely to be *wrong* — an append-only store makes data migrations
rare, since old versions stay as they were written, but Phase 8's fusion work
may want to backfill edges over records that already exist.

**Reversal cost.** Low. The ledger is a table anything can read, the migrations
are plain SQL, and adopting a framework later means teaching it that versions 1
to N are already applied — one insert per row it wants. Nothing in `praxis/`
outside `store/` knows this module exists.
