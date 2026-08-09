# Phase N — <title>

## What changed and why

<!-- The diff says what. This section says why, and what it unlocks. -->

## How it was tested

<!-- Which suites, which properties, what was verified by hand and what by CI. -->

## Metrics delta vs. previous phase

<!-- Once the eval harness exists (Phase 10), paste the table. Before that,
     state coverage, test count and any runtime/cost numbers. -->

| Metric | Previous | This phase | Δ |
| ------ | -------- | ---------- | - |

## ADRs recorded in this phase

<!-- docs/adr/NNNN-*.md, one line each, with the decision in a sentence. -->

## Checklist

- [ ] `ruff check` / `ruff format --check` / `mypy --strict` clean
- [ ] Tests green, coverage not regressed
- [ ] `docs/reports/phase-N.md` written
- [ ] `CLAUDE.md` / `ARCHITECTURE.md` updated if anything structural changed
- [ ] `BACKLOG.md` updated with anything deferred, and why
- [ ] Dogfood estimate closed out with an actual outcome
