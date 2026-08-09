---
id: ADR-0010
status: accepted
date: 2026-08-09
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0010 — Keep the store off a syncing filesystem

## Chosen

Four changes, all landing in Phase 1:

1. **`data_dir` defaults to the platform data directory**, not `./.praxis`:
   `%LOCALAPPDATA%\praxis` on Windows, `$XDG_DATA_HOME/praxis` (falling back to
   `~/.local/share/praxis`) elsewhere. `PRAXIS_DATA_DIR` still overrides it.
2. **`praxis doctor` warns** when the resolved `data_dir` sits under a known
   sync root — `OneDrive`, `Dropbox`, `Google Drive`, `iCloud Drive`. A
   warning, not a failure: a deliberate override is legitimate.
3. **`busy_timeout = 5000ms`** on every connection, so a brief external lock
   retries instead of raising.
4. **WAL stays the journal mode**, which is safe once the store is off the
   synced tree.

Path resolution uses `platformdirs`, promoted from a transitive dev dependency
to a declared runtime one.

## Rejected

| Option | Why not |
| ------ | ------- |
| Keep the store at `./.praxis` inside the repository | The repository lives under `C:\Users\sihan\OneDrive\Desktop\Praxis Agents`. WAL keeps `-wal` and `-shm` sidecars whose contents must stay consistent with the main `.db`, and a sync client uploads all three independently — a restored or sync-resolved set can pair a `.db` with a `-wal` from a different instant. That is corruption, and it surfaces as a malformed-database error long after the write that caused it. |
| `journal_mode=DELETE` and stay inside the synced folder | Removes the sidecar-consistency problem and nothing else. The lock-contention failure remains, and it is the nastier of the two because it is intermittent and reads as an application bug. Kept in reserve if a future deployment genuinely must live on a synced path. |
| Ask the owner to exclude the folder from sync | Not reliably scriptable, silently reversible by the sync client's own UI, and it makes a correctness property depend on a setting nobody can see from inside the program. |
| Hard-fail when `data_dir` is under a sync root | Overreach. Someone may knowingly accept the risk on a throwaway corpus, and a tool that refuses to run is a tool that gets `--force`d. |
| Hand-roll the platform paths instead of adding `platformdirs` | Tempting under ADR 0002's few-dependencies stance, and ~10 lines. But the XDG fallback rules and the macOS convention are exactly the kind of detail that is quietly wrong for a year. `platformdirs` is already in the lockfile as a transitive dev dependency, has no dependencies of its own, and is the reference implementation of these conventions. Promoting it costs one line in `pyproject.toml`. |

## What was known at the time

The repository sits on a OneDrive-synced path, which was flagged in
[ADR 0003](0003-sqlite-as-the-graph-store.md) assumption 5 as a risk to watch.
Phase 1 is the phase that first writes a database, so the risk stops being
theoretical there. The store is gitignored and fully rebuildable from the
corpus, so the blast radius of corruption is a rebuild, not data loss. Access
is single-process and single-writer, so nothing in the system needs WAL's
reader/writer concurrency — WAL is kept because it is the better default, not
because it is required.

No corruption has been observed, because no database exists yet. This decision
is made on the documented behaviour of file-sync clients and SQLite's
documented WAL requirements, not on an incident.

Not known: whether the sync-root detection in (2) will generalise beyond the
four names listed, and whether `%LOCALAPPDATA%` is itself redirected to a
synced location on this machine — some enterprise OneDrive configurations
redirect known folders, and the check in (2) is what would catch it.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | The platform data directory is not itself synced | `data_dir_under_sync_root == false` | `on_event("praxis doctor warns about the default data_dir")` |
| 2 | Moving the store off the repository breaks nothing that assumed a repo-relative path | `tests_passing_after_data_dir_move == true` | `on_event("Phase 1 implements this ADR")` |
| 3 | No corruption occurs once the store is off the synced tree | `db_corruption_events == 0` | `when(phases_completed >= 6)` |
| 4 | `busy_timeout` of 5s is enough for any transient external lock | `sqlite_busy_errors == 0` | `when(phases_completed >= 6)` |
| 5 | The four sync roots named cover this project's realistic environments | `unrecognised_sync_root_incidents == 0` | `on_event("the project is developed on another machine")` |

## Consequences

**Accepted costs.** The store is no longer visible next to the code, which is
mildly worse for casual inspection — `praxis store stats` and a `doctor` line
reporting the resolved path both exist partly to compensate. One new runtime
dependency. And the store no longer travels with the synced folder, so moving
between machines means re-ingesting rather than finding the database already
there; given that the corpus is the source of truth and the store is derived,
that is the correct direction anyway.

**Reversal cost.** Low. `data_dir` is a single setting with a single default,
every path in the system is derived from it, and `PRAXIS_DATA_DIR` already
overrides it. If the decision turns out to be wrong, changing the default back
is one line plus a superseding ADR.

## Implementation note

This ADR is accepted now so Phase 1 starts from a settled decision instead of
rediscovering the problem while debugging a locked database. The code lands in
Phase 1 — see [`docs/reports/NEXT.md`](../reports/NEXT.md).
