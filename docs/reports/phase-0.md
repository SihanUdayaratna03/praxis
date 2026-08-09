# Phase 0 — Foundation

## What was built

No business logic, by design. What shipped is the machinery that makes the
next twelve phases enforceable rather than aspirational.

**Toolchain.** Python 3.12 pinned via `uv`, `ruff` for lint and format,
`mypy --strict` over `praxis/`, `pytest` with `hypothesis`. One command
(`uv sync --all-groups`) takes a fresh clone to a working install, fetching the
interpreter itself.

**CI.** Three GitHub Actions jobs: lint and types; a test matrix across
Ubuntu 3.12/3.13 and Windows 3.12; and a packaging job that builds the wheel,
installs it into a clean environment and invokes the console script. No secret
is referenced anywhere in the workflow — if CI ever fails for want of a
credential, the offline guarantee has broken and the pipeline says so.

**Configuration.** `praxis/config/settings.py` loads typed, frozen settings
from `PRAXIS_*` and `.env`. `praxis/config/models.py` is the only file in the
repository permitted to contain a model identifier; it holds the three-tier
routing table, prices as `Decimal`, and `NON_LLM_AGENTS`.

**Observability.** Structured JSON logging with run context bound through
contextvars, UTC timestamps, and a console renderer for local runs.

**CLI.** `praxis version`, `praxis config`, `praxis doctor`.

**Enforcement.** Three Claude Code hooks and a pre-commit configuration that
mirrors CI. Two subagent definitions (`suite-runner`, `api-researcher`).

**Documentation.** `README.md`, `ARCHITECTURE.md`, `CLAUDE.md`, `BACKLOG.md`,
seven ADRs, and the dogfood estimate log.

## Key decisions

| ADR | Decision |
| --- | -------- |
| [0001](../adr/0001-record-architecture-decisions.md) | ADRs in Praxis's own schema, so `docs/adr/` is a valid corpus from commit one |
| [0002](../adr/0002-python-toolchain.md) | Python 3.12 with uv, ruff, mypy strict |
| [0003](../adr/0003-sqlite-as-the-graph-store.md) | SQLite + FTS5 as the embedded graph store |
| [0004](../adr/0004-custom-async-orchestrator.md) | Own the orchestrator; no agent framework |
| [0005](../adr/0005-offline-first-llm-provider.md) | Offline-first `LLMProvider` with three implementations |
| [0006](../adr/0006-model-routing-table.md) | Route by intent to three model tiers |
| [0007](../adr/0007-merge-commits-never-squash.md) | Merge commits into `main`, never squash |

## Test and quality status

| Check | Result |
| ----- | ------ |
| `pytest` | 122 passed |
| Coverage (`praxis/`) | 97.14%, gate 85% |
| `ruff check` | clean |
| `ruff format --check` | clean |
| `mypy --strict` | clean, 8 source files |
| `pre-commit run --all-files` | all hooks pass |
| Commits on the phase branch | 19 |

Coverage by module: `config/models.py` 100%, `config/settings.py` 100%,
`obs/logging.py` 100%, `cli.py` 92% (uncovered lines are defensive branches in
`doctor` that only fire on a filesystem error or a corrupted routing table).

## Metrics

No previous phase, so no delta. The eval harness does not exist until Phase 10;
these are the quality numbers that stand in until then.

| Metric | Value |
| ------ | ----- |
| Tests | 122 |
| Property-based tests | 5 (all on cost arithmetic) |
| Coverage | 97.14% |
| Source files under `praxis/` | 8 |
| Statements under `praxis/` | 190 |
| Runtime dependencies | 4 |
| Full local gate | ~35s |
| Credentials required to run anything | 0 |

## Estimate versus actual

Logged as `EST-0001` in `docs/dogfood/estimates.jsonl` **before** the first
file was created:

| | |
| --- | --- |
| Estimated | 2.5 hours, confidence 0.6, work class `scaffolding` |
| Actual (engineering) | ~1.0 hour, first commit to last |
| Direction | **Over-estimated by roughly 2.5×** |

Worth recording plainly, because the interesting thing about a calibration
system's own first data point is that it went the unusual way. Scaffolding is
the work class where the steps are known in advance, which is exactly where
over-estimation is plausible — and it is a different bias direction from the
one the product's own examples assume. One data point proves nothing; that is
the whole reason `BiasDetective` refuses to report below `n = 5`.

The outcome record is deliberately **not** written yet: the phase is not closed
(see below), and logging an outcome for an unfinished phase would put a
convenient number into the corpus that the Phase 12 self-analysis would then
report as fact.

## Deferred to backlog

Six items, each with its reason, in [`BACKLOG.md`](../../BACKLOG.md). The two
that matter:

- **Branch protection on `main`** — requires a paid plan for private
  repositories. The same rules are enforced by the pre-commit
  `no-commit-to-branch` hook and the `guard_git_workflow` Claude Code hook.
- **The OneDrive-synced working path** — file-sync tools have a poor record
  around SQLite WAL files. Becomes a live risk in Phase 1; ADR 0003
  assumption 5 is written to fire.

## What went wrong

Both enforcement hooks had real bugs, and both were found by the hooks firing
on this repository rather than by review — which is the argument for building
them in Phase 0 instead of Phase 12.

1. `subprocess(text=True)` decodes with the locale encoding, cp1252 here. The
   first commit containing `ARCHITECTURE.md` crashed the secret scanner on a
   box-drawing character. The same trap sat in the quality hook, since ruff and
   mypy draw diagnostics with those characters too.
2. The scanner's exemption list matched a path prefix, but the staged scan sees
   relative paths and a tool payload carries an absolute one — so
   `.claude/hooks/` was exempt during a commit and not during an edit.

Both are fixed and both have a regression test named after the failure.

## Blocked

**The phase cannot close.** `gh` is authenticated with `gist, read:org, repo`
but not `workflow`, so GitHub refuses any push containing
`.github/workflows/ci.yml`. That blocks push → PR → CI → merge → tag. The fix
needs a browser login, which is not something I can do:

```
gh auth refresh -h github.com -s workflow
```

Everything up to the push is complete: 19 commits on
`feat/phase-0-foundation`, all checks green locally.

## Risks for Phase 1

| Risk | Mitigation |
| ---- | ---------- |
| SQLite on a OneDrive-synced path corrupting under sync | ADR 0003 assumption 5. The database is gitignored and rebuildable from the corpus, so the blast radius is a rebuild. Watch for it in the first real write path. |
| The append-only model making ordinary reads awkward | Design the repository layer's read API around "current version of X" from the start, rather than making every caller filter by version. |
| Hand-written ADR predicates not parsing once the DSL exists | ADR 0001 assumption 1. A small fixed set of files, and a Phase 5 task. |
| Property tests on graph invariants being slow | Bound `hypothesis` example counts on the store tests before they get into the pre-commit loop. |
