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

docs/adr/0008-*.md               id scheme and versioning strategy
docs/adr/0009-*.md               migrations without a framework
docs/adr/0010-*.md               store durability under a syncing filesystem
docs/adr/README.md               index rows
ARCHITECTURE.md                  data model section: target → built
docs/reports/phase-1.md
docs/dogfood/outcomes.jsonl      OUT-0002, at phase close
```

Roughly 10 source files, 7 test files, 3 ADRs. Sub-branches are not needed —
this phase builds no agents, so one phase branch is the right shape.

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

### Recommended resolution — decide in Phase 1, record as ADR 0010

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

Do **not** silently keep the store inside the synced folder and hope. If the
owner prefers it there, that is their call — record it as a rejected option in
the ADR with this reasoning attached.

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

## Open questions for the owner

Neither blocks the start of Phase 1; both want an answer before it closes.

1. Is moving the store to `%LOCALAPPDATA%\praxis` acceptable? (Recommended —
   it is invisible in normal use and removes the corruption risk.)
2. Should the repository be made public before submission? It would enable
   real branch protection for free and let judges browse the network graph;
   the cost is that the work is visible early.
