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
| [0011](0011-semantic-segmentation-over-a-deterministic-block-grid.md) | Segment semantically, over a deterministic block grid | accepted |
| [0012](0012-machine-gradeable-corpus-ground-truth.md) | Ground truth as byte ranges, labelled negatives and typed edges | accepted |
| [0014](0014-prompts-as-versioned-stored-artefacts.md) | Prompts are versioned files, and every trace names the one it read | accepted |
| [0015](0015-extraction-cites-spans-by-offered-ordinal.md) | Extraction cites spans by offered ordinal, never by span id | accepted |
| [0016](0016-the-extractor-writes-the-first-estimated-as-edge.md) | `AssumptionExtractor` writes the first `estimated_as` edge | accepted |
| [0017](0017-a-small-total-predicate-language.md) | A small total predicate language with three-valued evaluation | accepted |
| [0018](0018-blocking-then-arithmetic-then-a-model.md) | Find contradictions by blocking, then arithmetic, then a model | accepted |
| [0019](0019-only-arithmetic-can-breach-and-writes-happen-on-change.md) | Only arithmetic can breach, and a pass writes only on a change | accepted |
| [0020](0020-the-archaeologist-retrieves-and-never-generates.md) | `ArchaeologistAgent` retrieves and grounds, and never generates | accepted |
| [0021](0021-match-quality-is-arithmetic-not-a-judgement.md) | `match_quality` is arithmetic, and the module holding it cannot reach a model | accepted |
| [0022](0022-an-unmatched-estimate-is-an-unresolved-outcome.md) | An unmatched estimate is an `unresolved` outcome, never a silence | accepted |
| [0023](0023-work-class-is-assigned-by-revision.md) | Work class is assigned by revising a record, against the vocabulary the store already holds | accepted |
| [0024](0024-dispersion-widens-the-band-and-only-n-refuses.md) | dispersion widens the band; only `n` refuses | accepted |
| [0025](0025-the-calibrator-explains-rather-than-generates.md) | the calibrator explains rather than generates | accepted |
| [0026](0026-the-bridge-prices-a-judgement-phase-4-already-made.md) | the bridge prices a judgement Phase 4 already made | accepted |
| [0027](0027-collateral-damage-is-a-walk-phase-1-already-wrote.md) | collateral damage is a walk Phase 1 already wrote | accepted |
| [0028](0028-a-projection-is-not-a-breach.md) | a projection is not a breach | accepted |
| [0029](0029-short-commit-messages-from-phase-9.md) | Write short, plain commit messages from Phase 9 onward | accepted |
| [0030](0030-the-challenger-keeps-its-model-route.md) | Keep `ChallengerAgent` on the `reason` tier, breaking a six-component run | accepted |
| [0031](0031-curation-is-supersedes-and-retraction.md) | Curate with `supersedes` and retraction, and compute it without a model | accepted |
| [0032](0032-abstention-is-arithmetic-and-is-never-stored.md) | Decide abstention by arithmetic, and never store the result | accepted |
| [0033](0033-short-comments-and-docstrings-from-phase-10.md) | Write short, plain comments and docstrings from Phase 10 onward | accepted |
| [0034](0034-the-eval-harness-runs-a-coherently-citing-mock.md) | The eval harness runs a coherently-citing mock | accepted |
| [0035](0035-the-ablation-ladder-is-cumulative.md) | The ablation ladder is cumulative, and every rung runs the whole corpus | accepted |
| [0036](0036-the-dashboard-is-fastapi-over-a-frontend-with-no-build-step.md) | The dashboard is FastAPI over a frontend with no build step | accepted |
| [0037](0037-the-dashboard-is-the-second-network-seam.md) | The dashboard is the second network seam, and it is named | accepted |

0010 was decided during Phase 0 because Phase 1 needed a settled answer before
it wrote its first database. Numbers are never reused or renumbered, so 0008
and 0009 sat reserved until Phase 1 filled them in their proper order.
