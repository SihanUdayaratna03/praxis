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
| CI on the PR | 5/5 green, Windows included |
| CI on `main` after merge | green |
| Commits on the phase branch | 21 |

21 commits is one over the 8–20 guidance. The overflow is the CI fix
(`4381f9b`), which could not have been folded in earlier because the failure
was only observable once the workflow reached the remote. Squashing it away to
hit the number would have traded a real record for a tidy one, which ADR 0007
exists to prevent.

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
| Actual, wall clock | 2.62 hours (first commit → merge) |
| Actual, active engineering | ~1.1 hours |
| Actual, blocked | ~1.5 hours on the `workflow` OAuth scope |
| Scored | **`partial`**, not `exact` |

The wall clock landed within 5% of the estimate, and that is a coincidence
worth refusing to take credit for. The engineering was over-estimated by about
2.3×; an external block absorbed the difference. Two errors in opposite
directions cancelled.

Scoring this `exact` would teach the calibrator the wrong lesson twice — that
this estimator is well calibrated on `scaffolding` (it is not, it is
optimistic-in-reverse), and that blocked time is estimable work (it is not).
So `OUT-0001` records `active_quantity` and `blocked_quantity` alongside the
wall clock, and the dogfood schema gained those two fields because this first
outcome proved they were needed.

Direction of the *engineering* miss: **over-estimated**, which is the opposite
of the bias the product's own examples assume. Scaffolding is the work class
where the steps are known in advance, so that is plausible. One data point
proves nothing — which is exactly why `BiasDetective` refuses below `n = 5`.

## Deferred to backlog

Six items, each with its reason, in [`BACKLOG.md`](../../BACKLOG.md). The two
that matter:

- **Branch protection on `main`** — attempted, and refused:

  ```
  PUT /repos/SihanUdayaratna03/praxis/branches/main/protection
  403 Upgrade to GitHub Pro or make this repository public to enable this feature.
  ```

  The intended ruleset (strict status checks on all five CI jobs, no force
  pushes, no deletions) is recorded in `BACKLOG.md` so it can be applied
  verbatim the moment the repo goes public or the plan changes. Until then the
  same rules are enforced by the pre-commit `no-commit-to-branch` hook and the
  `guard_git_workflow` Claude Code hook — which is weaker, because it is
  client-side, and the report should say so plainly.
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

## What blocked the phase, and for how long

`gh` was authenticated with `gist, read:org, repo` but not `workflow`, so
GitHub refused every push containing `.github/workflows/ci.yml`:

```
! [remote rejected] feat/phase-0-foundation
  (refusing to allow an OAuth App to create or update workflow
   `.github/workflows/ci.yml` without `workflow` scope)
```

That blocked push → PR → CI → merge → tag for ~1.5 hours, until the owner ran
`gh auth refresh -h github.com -s workflow`, which needs a browser login.
Recorded as `blocked_quantity` on `OUT-0001` rather than folded into the
duration, for the reason given above.

The first CI run then failed at setup on all five jobs:
`unable to resolve astral-sh/setup-uv@v9`. The version had been read from the
releases API, which correctly reports `v9.0.0` — but `setup-uv` stopped
publishing a floating major tag after `v7.6`, so the release exists and the ref
does not. Checking the releases endpoint was the right instinct and the wrong
query; `tags` is the one that answers whether a ref resolves. All three actions
are now pinned to exact tags, which a project built on reproducibility should
have done anyway.

## Risks for Phase 1

| Risk | Mitigation |
| ---- | ---------- |
| SQLite on a OneDrive-synced path corrupting under sync | ADR 0003 assumption 5. The database is gitignored and rebuildable from the corpus, so the blast radius is a rebuild. Watch for it in the first real write path. |
| The append-only model making ordinary reads awkward | Design the repository layer's read API around "current version of X" from the start, rather than making every caller filter by version. |
| Hand-written ADR predicates not parsing once the DSL exists | ADR 0001 assumption 1. A small fixed set of files, and a Phase 5 task. |
| Property tests on graph invariants being slow | Bound `hypothesis` example counts on the store tests before they get into the pre-commit loop. |
