# Handover — start of Phase 1

Read this first, then `CLAUDE.md`. Written at the close of Phase 0 so the next
session can start working instead of re-deriving state.

---

## Where we stopped

Phase 0 is merged, tagged and green. Nothing is in flight.

| | |
| --- | --- |
| `main` | `9caf2d8`, local and remote identical |
| Tag | `v0.0-phase-0` → `9caf2d8` (verified through the API, not assumed) |
| CI on `main` | green, 5/5 jobs including Windows |
| Open PRs | none |
| Remote branches | `main` only |
| Working tree | clean |

**Shipped:** toolchain (Python 3.12 / uv / ruff / mypy strict / pytest +
hypothesis), CI, `praxis/config/{settings,models}.py`, `praxis/obs/logging.py`,
the `version`/`config`/`doctor` CLI, four enforcement hooks mirrored in
pre-commit, seven ADRs, 122 tests at 97.14% coverage.

**Deliberately not shipped:** any business logic, any storage, any LLM call.

### Two things that are true and easy to forget

1. **Branch protection does not exist.** `PUT .../branches/main/protection`
   returns `403 Upgrade to GitHub Pro`. `main` is protected only by the
   pre-commit `no-commit-to-branch` hook and `guard_git_workflow.py`, both
   client-side. Do not commit to `main` directly — branch and open a PR even
   for a one-line docs change.
2. **Long text goes to a file.** This shell has a ~965-byte parse limit and
   PowerShell 5.1 mangles embedded quotes passed to native executables. Use
   `gh pr create --body-file .praxis-tmp/pr-body.md` and
   `git commit -F .praxis-tmp/commit-msg.txt`. `.praxis-tmp/` is gitignored.

---

## What Phase 1 must deliver

Data model and store. **Zero LLM involvement** — this phase is Pydantic,
SQL and property tests.

### 1. Records

The nine record types from `ARCHITECTURE.md`, as Pydantic v2 models:
`Document`, `Span`, `Decision`, `Assumption`, `Estimate`, `Outcome`, `Link`,
`Finding`, `AuditEvent`.

Non-negotiable properties:

- **Versioned and append-only.** A change is a new version plus an
  `AuditEvent`. Nothing is updated in place. Design the read API around
  "current version of X" from the start, or every caller ends up filtering by
  version by hand — this is the single most likely design mistake in the phase.
- **Typed, stable IDs.** `D-0042`, `SPAN-...`. Distinct types so a `SpanId`
  cannot be passed where a `DecisionId` is expected; `mypy --strict` should
  catch that class of bug for free.
- **Every claim carries a `span_id`** with exact source offsets.
- **Timezone-aware datetimes only.** Ruff's `DTZ` rules already enforce it.

### 2. Store

One SQLite file: entity tables, **one typed edge table**, FTS5 indexes,
recursive CTEs for graph walks. Edge types: `assumes`, `justified_by`,
`contradicts`, `supersedes`, `estimated_as`, `collateral_of`.

- Forward-only numbered migrations with a `schema_version` table. No
  migration framework — the same reasoning as ADR 0004.
- A repository layer that is the *only* thing touching SQL, so ADR 0003's
  "backend is replaceable" claim stays true.
- The audit trail is written in the same transaction as the change it
  describes. An audit row that can be lost independently of its change is
  worse than no audit row, because it looks trustworthy.

### 3. Tests

Property-based, with `hypothesis`, on the invariants rather than the examples:

- Round-tripping any record through the store preserves it exactly.
- Append-only holds: no sequence of operations reduces the version count of
  any entity.
- Every edge references two rows that exist (referential integrity, enforced
  by SQLite *and* asserted).
- Graph walks terminate — no cycle makes `CollateralAgent`'s future traversal
  hang.
- Every mutation produces exactly one `AuditEvent`.

**Bound the hypothesis example counts on store tests before they enter the
pre-commit loop.** A 30-second hook is a hook that gets disabled.

### 4. CLI

`praxis init` (create and migrate the store) and `praxis store stats` (row and
edge counts). `praxis doctor` gains the sync-path check described below.

---

## Files Phase 1 will touch

```
praxis/domain/__init__.py
praxis/domain/ids.py             typed id newtypes and generation
praxis/domain/records.py         the nine Pydantic records
praxis/domain/links.py           edge types as an enum
praxis/store/__init__.py
praxis/store/connection.py       pragmas: foreign_keys, journal mode, busy_timeout
praxis/store/schema/001_initial.sql
praxis/store/migrations.py       forward-only runner + schema_version
praxis/store/repository.py       the only module that writes SQL
praxis/store/audit.py            audit writes, in-transaction

praxis/cli.py                    + init, + store stats, doctor sync check
praxis/config/settings.py        possibly a new data_dir default (see risk)

tests/domain/test_records.py
tests/domain/test_ids.py
tests/store/test_migrations.py
tests/store/test_repository.py
tests/store/test_graph.py
tests/store/test_audit.py
tests/store/test_properties.py   the hypothesis invariants

docs/adr/0008-*.md               id scheme and versioning strategy (to write)
docs/adr/0009-*.md               migrations without a framework (to write)
docs/adr/README.md               index rows for 0008 and 0009
pyproject.toml                   platformdirs → runtime dependency (ADR 0010)
ARCHITECTURE.md                  data model section: target → built
docs/reports/phase-1.md
docs/dogfood/outcomes.jsonl      OUT-0002, at phase close
```

Roughly 10 source files, 7 test files, 2 new ADRs plus implementing ADR 0010.
Sub-branches are not needed — this phase builds no agents, so one phase branch
is the right shape.

---

## Estimate

Logged as **`EST-0002`** in `docs/dogfood/estimates.jsonl` before any work
starts, per the dogfooding rule.

| | |
| --- | --- |
| Quantity | **2.0 hours of active engineering** |
| Work class | `data-modelling` |
| Confidence | 0.5 |

Two deliberate changes from `EST-0001`:

1. **Active time, not wall clock.** `OUT-0001` showed wall clock hides an
   external block and makes a bad estimate look good.
2. **No calibration correction applied**, and that is the correct call. The
   only prior is `scaffolding` with `n = 1`. `BiasDetective` is specified to
   refuse below `n = 5`, and this is a different work class besides — applying
   Phase 0's 2.3× over-estimate here would be exactly the unprincipled
   adjustment the product exists to replace. Lower confidence (0.5 vs 0.6)
   because the SQLite-under-sync question below is genuinely unresolved.

---

## The OneDrive / SQLite WAL risk

**This is the main technical risk of Phase 1**, and Phase 1 is where it stops
being theoretical, because it is the phase that first writes a database.

The repository lives at
`C:\Users\sihan\OneDrive\Desktop\Praxis Agents`, and the default `data_dir` is
`.praxis` **inside it**. SQLite in WAL mode keeps two sidecar files, `-wal` and
`-shm`, whose contents must stay consistent with the main `.db` file.

Two independent failure modes:

1. **Inconsistent snapshots.** OneDrive uploads the three files
   independently and at different moments. A restored or sync-resolved set can
   pair a `.db` with a `-wal` from a different instant. That is corruption, and
   it surfaces later as a malformed-database error rather than at write time.
2. **Lock contention.** The sync client opens files to upload them. SQLite
   writes then intermittently fail with `SQLITE_BUSY` / "database is locked" —
   non-deterministic, environment-specific, and very easy to misdiagnose as an
   application bug.

Tracked as **ADR 0003 assumption 5**
(`db_corruption_events == 0`, expiring at `phases_completed >= 3`).

### Resolution — decided, implement in Phase 1

Settled in [ADR 0010](../adr/0010-store-location-under-a-syncing-filesystem.md)
(accepted 2026-08-09). The four items below are the implementation checklist,
not an open proposal.

1. **Move the default `data_dir` off the synced tree.** Default to the
   platform data directory (`%LOCALAPPDATA%\praxis` on Windows,
   `~/.local/share/praxis` elsewhere) instead of `./.praxis`. This removes
   both failure modes with no action required from the owner, and the store is
   gitignored and rebuildable anyway, so nothing of value lives there.
   `PRAXIS_DATA_DIR` still overrides it.
2. **Add a `praxis doctor` check** that warns when `data_dir` resolves under a
   known sync root — `OneDrive`, `Dropbox`, `Google Drive`, `iCloud Drive`.
   Warn rather than fail: a deliberate override is legitimate.
3. **Set `busy_timeout`** (5s) on every connection so brief external locks
   retry instead of raising.
4. **Consider `journal_mode=DELETE`** if the store must live on a synced path.
   It loses reader/writer concurrency, which this single-writer system does
   not use, and it removes the sidecar-consistency problem entirely. Keep WAL
   as the default for the off-tree location.

Path resolution uses `platformdirs`, promoted from a transitive dev dependency
to a declared runtime one — see the ADR for why that beat hand-rolling ten
lines of XDG and macOS conventions.

Watch for one thing while implementing: **`%LOCALAPPDATA%` can itself be
redirected to a synced location** under some enterprise OneDrive known-folder
configurations. Item 2 is what catches that, so build the `doctor` check before
trusting the new default (ADR 0010 assumption 1).

---

## Resuming

```bash
cd "C:\Users\sihan\OneDrive\Desktop\Praxis Agents"
git checkout main && git pull
uv sync --all-groups
uv run praxis doctor          # expect OK
uv run pytest                 # expect 122 passed

git checkout -b feat/phase-1-data-model
```

Then work the phase per `CLAUDE.md` § Workflow per phase: 8–20 commits, PR with
the required body via `--body-file`, CI green, merge commit, tag
`v0.1-phase-1`, write `docs/reports/phase-1.md`, close `EST-0002` with
`OUT-0002`, summarise in Sinhala, stop.

## Owner decisions — both answered, do not re-ask

1. **Store location: approved.** Default `data_dir` moves to
   `%LOCALAPPDATA%\praxis`, with the `doctor` sync-root warning and
   `busy_timeout`. Settled in
   [ADR 0010](../adr/0010-store-location-under-a-syncing-filesystem.md) —
   **accepted, implementation is Phase 1's job.** Start from the ADR; do not
   relitigate it.
2. **Repository stays private for now**, revisited at the start of Phase 8.
   Recorded as a dated decision point in [`BACKLOG.md`](../../BACKLOG.md) with
   the trigger and the exact commands. Consequence for Phase 1: `main` still
   has no server-side protection, so the client-side hooks remain the only
   thing enforcing it.

---

## Phase 1 progress log

One line per component, appended as each lands: ruff and `mypy --strict` green,
committed, pushed. Anything not listed here is not on `origin`. Work in
progress is at most the single file named in "next".

- **2026-08-11 — `praxis/store/errors.py`** (`218c962`, pushed). The store's
  exception hierarchy plus `translating_sqlite_errors()`, which is what keeps
  `sqlite3` from leaking past the package boundary in the failure path as well
  as the query path. Result codes were verified against the installed driver,
  which confirmed two things the rest of the phase depends on: a `RAISE(ABORT)`
  trigger reports `SQLITE_CONSTRAINT_TRIGGER` (so invariant 7 can be enforced
  by the schema, not just by the repository), and `ProgrammingError` carries no
  result code (so the translator cannot assume one). Coverage 90.30%, gate 85%.
  **Next: `praxis/store/connection.py`** — pragmas (`foreign_keys`,
  `journal_mode` from settings, `busy_timeout`), then its test module.
- **2026-08-11 — `praxis/store/connection.py`** (`bb938fc`, pushed). `connect`,
  `connect_from_settings`, `transaction`, `effective_journal_mode`,
  `fts5_available`. Three things settled while writing it: `foreign_keys` is
  per-connection and defaults to *off*, so it is read back rather than assumed;
  `BEGIN IMMEDIATE` rather than `DEFERRED`, because SQLite does not honour
  `busy_timeout` on a deferred lock *upgrade* and `DEFERRED` would silently
  exempt every write from ADR 0010's retry; and `transaction` joins an open
  transaction instead of taking a savepoint, so a record and its `AuditEvent`
  cannot half-commit. FTS5 confirmed present in the bundled SQLite 3.53.1.
  **Next: `tests/store/test_connection.py`.**
- **2026-08-11 — `tests/store/test_connection.py`** (`6b431a6`, pushed). 24
  tests, asserting the pragmas by their effect rather than by the statement
  having run. Pinned down that a statement executed outside `transaction()`
  still raises `sqlite3.IntegrityError`: the context manager is the translation
  boundary, not the connection. **Next: the SQL schema.**
- **2026-08-12 — `praxis/store/schema/{001_core,002_search}.sql`** (`1d47541`,
  pushed). Two migrations from the start, so the runner is exercised on a
  sequence rather than on a single file. The Phase 8 constraint was discharged
  before the edge table was finalised: the fusion query is eight lines against
  the `dependency_edge` view, and it is written out in a comment above that view
  so a later schema change has to keep it that way. Verified by hand that the
  schema refuses an update, a delete, a skipped version, a dangling edge, an
  edge lying about its endpoint kind, a naive timestamp, an unknown enum value,
  a self-loop, an `unresolved` outcome carrying a number, and a decision with no
  rejected options. FTS5 indexing is trigger-driven, and `rejected` reasons are
  indexed too, so "why not Postgres" is answerable. One driver detail worth
  keeping: an FTS5 table cannot be aliased on the left of `MATCH`.
  **Next: `praxis/store/migrations.py` and ADR 0009.**
- **2026-08-12 — `praxis/store/migrations.py`** (`f5be811`, pushed), **ADR 0009**
  (`64f80f4`), **`tests/store/test_migrations.py`** (`b5b4dd3`). 18 tests. The
  ledger row is inserted inside the same transaction as the DDL, which works
  because `executescript` commits *before* it runs and performs no transaction
  control of its own — so the `BEGIN IMMEDIATE` goes in the script and the
  `COMMIT` stays in Python. ADR 0009 also records a refinement to ADR 0008:
  three views (`record_head`, `current_record`, `current_link`) plus a generic
  repository read, rather than the nine `<kind>_current` views 0008 called for.
  **Next: `praxis/store/repository.py` and `audit.py`.**
- **2026-08-12 — the store layer** (`f1bb0b9` mapping, `10f7031` audit,
  `0e4059f` graph, `772143a` reports, `833a775` repository; all pushed). Ruff,
  `ruff format` and `mypy --strict` green on all of it. Two decisions worth
  carrying forward. `audit_event` gained an `ordinal` column — audit rows are
  deliberately not graph nodes, so they cannot use `node.ordinal`, and lexical
  `MAX(id)` is wrong past four digits; `001_core.sql` was edited rather than
  amended because it has never been applied to a store that exists. And the
  hand-written `dependency_edge` query and `graph.impacted_by` were checked
  against each other on the same fixture and agree, so the documented query in
  the schema is not decoration. `repository.py` is 495 lines against the ~400
  guideline; it was split along real seams (`graph`, `reports`, `audit`) rather
  than to hit a number, and the remainder is docstring-heavy.
  **Next: the store test modules, then the CLI (`init`, `store stats`, the
  `doctor` sync check).**
- **2026-08-12 — `tests/store/{conftest,test_schema}.py`** (`17062c6`, pushed).
  60 tests. `conftest.py` gives every store test one migrated in-memory store, a
  fixed offset-aware `WRITTEN_AT`, and a `world` fixture holding the smallest
  graph that exercises the fusion path. `test_schema.py` reads the `CHECK`
  constraints back out of `sqlite_master` and compares them to the Python enums,
  which is the only thing that would notice those two lists drifting apart. The
  append-only trigger tests initially passed on three tables because those
  tables were empty — an empty table cannot demonstrate its own triggers — so
  the fixture gained an `Outcome` and a `Finding`. **Session ended here.**
- **2026-08-14 — `tests/store/test_repository.py`** (`a53a5a7`, pushed). 73
  tests; the suite is now 386 passing at 97.61% coverage against the 85% gate,
  with `repository.py` at 98%. Three things the writing settled. Quantity
  round-trips are asserted on `str()` rather than on equality, because
  `Decimal("1.10") == Decimal("1.1")` and the scale is precisely what invariant
  4 protects. The search tests use tokens that appear nowhere else in the
  fixture corpus — the index keeps every superseded version forever, so a
  repeated word would pass whether or not the `record_head` join is doing its
  job. And the failed-write test writes a *dangling edge* rather than a
  duplicate id, because that is the only failure that happens after the node and
  version rows are already in, which is what makes the rollback observable.
  **Next: `tests/store/test_audit.py`.**
- **2026-08-14 — `tests/store/test_audit.py`** (`794fa60`, pushed). 21 tests;
  407 passing, 97.81%. The load-bearing one compares the audit table against
  `record_version` row for row rather than counting the writes the test itself
  made — a count matches just as well when the trail describes the wrong
  versions. Two cases are recorded here because nothing else would have caught
  them: the ordering test revises at the fixture's own timestamp, so ordering by
  `occurred_at` would be arbitrary and the assertion on version order means
  something; and the counter test inserts an ordinal past four digits, where
  `AUD-9999` sorts above `AUD-10000` and allocating from `MAX(id)` would reissue
  an id that is already taken. **Next: `tests/store/test_graph.py`.**
- **2026-08-14 — `tests/store/test_graph.py`** (`4e18da3`, pushed). 27 tests;
  434 passing, 98.08%. The documented query above `dependency_edge` is now
  lifted out of the migration this build ships and run against the same fixture
  as `graph.impacted_by` at three depths, so the comment fails the suite if it
  drifts. Two directional facts are worth carrying forward: a cycle cannot be
  built over the dependency types at all, because the grammar makes every one of
  them point dependent → depended-upon, so the depth bound is tested with
  `contradicts`; and a walk towards dependents steps target → source, so a test
  edge has to point *at* the node the walk is expected to arrive from.
  **Next: `tests/store/test_properties.py`.**
- **2026-08-14 — `tests/store/test_properties.py`** (`7c50252`, pushed). 9
  properties at 50 examples each; 443 passing in 11s, 98.08%. Each example
  builds its own in-memory store — sharing one would make every example depend
  on the ids the last one drew. A generated record cannot be written alone, so
  the `chains()` strategy generates a record *and its references* in write
  order, with the record under test last; the tail builders are lambdas in a
  dict so only the drawn kind is generated. One trap worth remembering:
  `strategy.example()` inside a test raises under `filterwarnings = ["error"]`,
  which is correct — the strategy has to be composed into the `@given`.
  **Next: the CLI (`init`, `store stats`, the `doctor` sync check).**
- **2026-08-14 — the CLI** (`96ef3ab`, pushed). `praxis init`, `praxis store
  stats` and ADR 0010's sync-root warning on both `init` and `doctor`; 10 new
  CLI tests, 453 passing, 98.17%. Both store commands go through one `_opened`
  helper that turns a `StoreError` into a sentence and exit 1 — `store stats`
  opens with `create=False`, so a mistyped `PRAXIS_DATA_DIR` is reported rather
  than answered with zeros. The warning never changes the exit code. One thing
  to know when editing `cli.py`: the `post_edit_verify` hook runs `ruff --fix`,
  which deletes imports added in one edit before the edit that uses them lands,
  so add the import and its use together. **Next: `ARCHITECTURE.md` from target
  to built, then the phase close-out.**

---

## Resuming — read this first (written 2026-08-12, end of session)

Stopped for credit exhaustion, not for a problem. Nothing is in flight, nothing
is half-written, and no file is uncommitted.

| | |
| --- | --- |
| Branch | `feat/phase-1-schema` |
| HEAD | `17062c6`, local and `origin/feat/phase-1-schema` identical |
| Working tree | clean |
| Suite | **313 passed**, coverage **86.47%** (gate 85%) |
| Ruff, `ruff format --check`, `mypy --strict` | all green |
| Open PRs | none. Phase 1 is **not** merged and **not** tagged |

```bash
cd "C:\Users\sihan\OneDrive\Desktop\Praxis Agents"
git checkout feat/phase-1-schema && git pull
uv sync --all-groups
uv run pytest          # expect 313 passed
```

### `praxis/store/` — complete, committed, pushed

| Module | What it is |
| ------ | ---------- |
| `location.py` | ADR 0010 store location and the sync-root warning |
| `errors.py` | The exception vocabulary; `translating_sqlite_errors()` |
| `connection.py` | `connect`, `transaction`, pragmas, `fts5_available` |
| `schema/001_core.sql` | Nodes, versions, the nine records, one edge table, 24+1 append-only triggers, three views |
| `schema/002_search.sql` | FTS5 plus the AFTER INSERT triggers that populate it |
| `migrations.py` | ADR 0009 forward-only runner, checksummed ledger |
| `mapping.py` | Record ↔ row, one table driving both directions |
| `audit.py` | Audit writes, always inside the caller's transaction |
| `repository.py` | `add` / `revise` / `retract`, reads, allocation. No update, no delete |
| `graph.py` | `links_from/to/touching`, `impacted_by`, `depends_on` |
| `reports.py` | `search`, `stats` |

Nothing in `praxis/store/` is expected to need further work. If a later test
finds a bug, fix it there — but do not redesign it.

### Tests — what exists and what does not

Existing, all green:

- `tests/store/test_location.py` — ADR 0010
- `tests/store/test_connection.py` — 24 tests
- `tests/store/test_migrations.py` — 18 tests
- `tests/store/conftest.py` — the `store` and `world` fixtures
- `tests/store/test_schema.py` — 60 tests

**Still to write, in this order:**

1. **`tests/store/test_repository.py` — the next file, start here.** Allocation
   (`next_id` per kind; a revision must not consume one; `Span`/`Link` refuse
   allocation), round-trip exactness including `Decimal` staying `Decimal` and a
   finding's evidence spans, `add` refusing a non-version-1 record, a failed
   write leaving no audit row behind, revise/retract producing new versions with
   the old ones still readable, `VersionConflictError` on a stale revision,
   `list_all` excluding retracted records, edge reads and re-assertion being
   idempotent, search excluding superseded and retracted records, and `stats`.
2. `tests/store/test_audit.py` — exactly one `AuditEvent` per mutation, the
   audit row rolling back with its change, ordering by version then ordinal,
   `events_in_run`.
3. `tests/store/test_graph.py` — the walk in both directions, shortest-distance
   via the diamond in `world`, cycle termination against the depth bound, and
   the check that `graph.impacted_by` agrees with the hand-written
   `dependency_edge` query in `001_core.sql` (that agreement was verified once
   by hand and must become a test, or the documented query becomes decoration).
4. `tests/store/test_properties.py` — the hypothesis invariants. Reuse
   `tests/strategies.py`, which already generates valid records of every kind.
   Bound `max_examples` (~50) before these enter the pre-commit loop; a
   30-second hook is a hook that gets disabled.

### Then the CLI, which has not been started

- `praxis init` — create `data_dir`, open, migrate, report the schema version.
  `open_repository(settings, create=True)` already does all of it.
- `praxis store stats` — render `Repository.stats()` with `rich`. Must open with
  `create=False` so a mistyped `PRAXIS_DATA_DIR` is reported, not created.
- `praxis doctor` — add the ADR 0010 sync-root check. `location.sync_warning()`
  exists and is tested; `doctor` does not call it yet. **Warn, never fail** — a
  deliberate override is legitimate.

Then: `ARCHITECTURE.md` data-model section from *target* to *built*, then the
close-out (PR with `--body-file`, CI green, merge commit, `v0.1-phase-1`, verify
on the remote, `docs/reports/phase-1.md`, close `EST-0002` with `OUT-0002`).

### Things a cold session will otherwise rediscover the hard way

- **Coverage is 86.47% against an 85% gate.** `repository.py` and `graph.py` sit
  near 49% because only the fixture exercises them. The four test modules above
  will lift it, but do not add source before tests or the gate will fail.
- **`executescript` commits any open transaction before it runs**, so migration
  transaction control lives *inside* the script. Do not "tidy" that into a
  `with transaction(...)` block — it silently makes migrations non-atomic.
- **An FTS5 table cannot be aliased on the left of `MATCH`.** `reports.py`
  spells `search` out on both sides for that reason.
- **Editing a file with a Python script on this machine writes CRLF**, and the
  pre-commit `mixed line ending` hook rewrites it and aborts the commit. Just
  `git add` again and re-run the commit; the second one succeeds.
- **`ordinal_of()` parses the counter out of a sequential id**; the store never
  reissues one, because allocation reads `MAX(ordinal)` rather than counting.
- ADR 0009 records a deliberate refinement to ADR 0008: three shared views
  rather than nine `<kind>_current` views. Do not "restore" the nine.
