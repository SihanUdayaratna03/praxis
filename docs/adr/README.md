# Architecture Decision Records

Every non-obvious decision in this project is recorded here, and every record
is written in **Praxis's own schema**.

That is not a stylistic choice. Praxis's final phase runs the system over its
own development history and reports where its author was wrong. For that demo
to be real rather than staged, this directory has to be a valid Praxis corpus
from the first commit — decisions with rejected alternatives, assumptions
compiled into checkable predicates, and expiry conditions that can actually
fire. Writing ADRs in an ad-hoc format and converting them later would mean
the conversion, not the system, was doing the work.

So each record carries:

- **what was chosen**, and **what was rejected, with the reason** — a decision
  with no rejected alternatives is a note, not a decision;
- **what was known at the time**, so the record can be judged on the
  information available rather than with hindsight;
- **assumptions as predicates** — machine-checkable expressions over named
  facts, each with an expiry condition;
- **impact**, used by `ReviewTriageAgent` to rank what a human should re-read
  when an assumption breaks.

## The predicate language

Assumption predicates use the DSL built in Phase 5. Until then they are
written by hand in the same syntax, so the corpus needs no rewriting:

```
python_ecosystem_stable_on == "3.12"
sqlite_rows < 5_000_000
model_id_valid("claude-opus-5")
phase_actual_hours <= phase_estimated_hours * 2
```

Expiry conditions say when to re-check, not when the decision dies:
`on_event("anthropic deprecates a routed model")`, `after("2026-12-31")`,
`when(sqlite_rows > 5_000_000)`.

## Conventions

- Files are `NNNN-kebab-case-title.md`, numbered sequentially, never renumbered.
- Status is one of `proposed`, `accepted`, `superseded by NNNN`, `deprecated`.
- Records are append-only. A decision that changes gets a **new** record that
  supersedes the old one; the old file is edited only to add the supersession
  link. Rewriting history here would destroy exactly the provenance the
  product exists to preserve.
- Use [`template.md`](template.md).

## Index

| ADR | Title | Status |
| --- | ----- | ------ |
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions in Praxis's own schema | accepted |
| [0002](0002-python-toolchain.md) | Python 3.12 with uv, ruff and mypy strict | accepted |
| [0003](0003-sqlite-as-the-graph-store.md) | SQLite with FTS5 as the embedded graph store | accepted |
| [0004](0004-custom-async-orchestrator.md) | Own the orchestrator rather than adopt a framework | accepted |
| [0005](0005-offline-first-llm-provider.md) | Offline-first `LLMProvider` with three implementations | accepted |
| [0006](0006-model-routing-table.md) | Route by intent to three model tiers | accepted |
| [0007](0007-merge-commits-never-squash.md) | Merge commits into `main`, never squash | accepted |
| [0008](0008-typed-ids-and-append-only-versioning.md) | Typed ids, two id strategies, and append-only versioning | accepted |
| [0009](0009-forward-only-migrations-without-a-framework.md) | Forward-only numbered migrations, without a framework | accepted |
| [0010](0010-store-location-under-a-syncing-filesystem.md) | Keep the store off a syncing filesystem | accepted |

0010 was decided during Phase 0 because Phase 1 needed a settled answer before
it wrote its first database. Numbers are never reused or renumbered, so 0008
and 0009 sat reserved until Phase 1 filled them in their proper order.
