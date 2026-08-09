---
id: ADR-0002
status: accepted
date: 2026-08-09
decision_maker: Sihan Udayaratna
impact: medium
supersedes: null
superseded_by: null
---

# 0002 — Python 3.12 with uv, ruff and mypy strict

## Chosen

Python pinned to 3.12 via `.python-version`, dependencies and interpreter
managed by `uv`, lint and format by `ruff`, types by `mypy --strict` over
`praxis/`, tests by `pytest` with `hypothesis`. All four run identically in
pre-commit and in CI.

## Rejected

| Option | Why not |
| ------ | ------- |
| Python 3.14 (what is installed on the development machine) | The interpreter is fine; the ecosystem around `mypy --strict` and the scientific stack settles on a version roughly a year behind the newest. Trading a year of maturity for nothing this project needs is a bad trade, and `uv` fetches 3.12 regardless of what is on PATH. |
| `poetry` or `pip-tools` | `uv` covers dependency resolution, locking *and* interpreter management in one tool, with a cold CI install measured in seconds. Two tools where one suffices is a maintenance surface for no gain. |
| `black` + `flake8` + `isort` | Three tools, three configs, three chances to disagree with CI. `ruff` is one binary that does all three. |
| Types as advisory (`mypy` non-strict, or none) | The data model is a versioned append-only graph with typed edges, and the calibration layer is arithmetic on units that must not be mixed. `strict` is where those mistakes get caught for free. |

## What was known at the time

The development machine has Python 3.14.3 and `uv` 0.11.20. CI runs Linux; the
developer runs Windows, and the repository path contains a space. Every
dependency the project plans to use publishes 3.12 wheels.

Not known: whether any Phase 11 dashboard dependency will require a newer
runtime, and whether `mypy --strict` will become expensive against the
orchestrator's async generics in Phase 4.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | Every dependency Praxis needs supports 3.12 for the project's lifetime | `python_312_supported_by_all_deps == true` | `on_event("a required dependency drops 3.12")` |
| 2 | `mypy --strict` stays fast enough for a per-edit hook | `mypy_incremental_seconds <= 10` | `when(praxis_source_files > 60)` |
| 3 | The full local gate stays inside a tolerable inner loop | `local_gate_seconds <= 60` | `when(test_count > 400)` |
| 4 | Developing on Windows while CI runs Linux surfaces no divergence the matrix misses | `windows_only_ci_failures == 0` | `when(phases_completed >= 6)` |

## Consequences

**Accepted costs.** 3.12 forgoes newer language features; none are needed.
`mypy --strict` will occasionally demand an annotation that adds no safety,
particularly at the provider boundary — `ANN401` is already relaxed there.

**Reversal cost.** Low for the version pin: a one-line change plus a CI matrix
edit. Higher for `--strict`, which is easy to adopt now and painful to
retrofit once the codebase is large — which is why it is on from commit three
rather than later.
