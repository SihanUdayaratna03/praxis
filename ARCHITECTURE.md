# Architecture

> Status: this describes the system Praxis is being built toward. Phase 0
> shipped the foundation — config, logging, CLI, CI, hooks — Phase 1 the data
> model and the store, and Phase 2 the model access layer. Sections marked
> *(built)* exist and are tested; everything else is the target, not the
> present. Each phase updates this file when something structural lands.

## The shape of the thing

```
                    ┌──────────────────────────────────────────┐
   sources ────────▶│ SourceAdapter → SegmenterAgent           │  ingestion
   (md, txt, json)  │   Document → Span (stable ids, offsets)  │
                    └────────────────────┬─────────────────────┘
                                         │
              ┌──────────────────────────┴───────────────────────────┐
              ▼                                                      ▼
   ┌──────────────────────┐                          ┌───────────────────────────┐
   │ HALF A — provenance  │                          │ HALF B — calibration      │
   │                      │                          │                           │
   │ DecisionScout        │                          │ EstimateExtractor         │
   │ DecisionStructurer   │                          │ WorkClassifier            │
   │ AssumptionExtractor  │                          │ OutcomeMatcher            │
   │ AssumptionFormalizer │                          │ BiasDetective   (no LLM)  │
   │ AssumptionMonitor    │                          │ CalibratorAgent           │
   │ ContradictionDetector│                          │ ScoringAgent    (no LLM)  │
   │ ArchaeologistAgent   │                          │                           │
   └──────────┬───────────┘                          └─────────────┬─────────────┘
              │                                                    │
              │         ┌────────────────────────────────┐         │
              └────────▶│ FUSION                         │◀────────┘
                        │  FusionBridge                  │
                        │  CollateralAgent               │
                        │  ReviewTriageAgent             │
                        └───────────────┬────────────────┘
                                        ▼
                        ┌────────────────────────────────┐
                        │ GOVERNANCE                     │
                        │  VerifierAgent      (no LLM)   │
                        │  ChallengerAgent               │
                        │  CuratorAgent                  │
                        │  AbstentionGate                │
                        │  ReporterAgent                 │
                        └────────────────────────────────┘
```

Nothing enters the store without passing `VerifierAgent`, and no high-severity
finding reaches a human without surviving `ChallengerAgent`.

## The fusion mechanism

This is the part that justifies the project, so it is worth stating precisely.

1. `AssumptionFormalizer` compiles an assumption into a predicate:
   `migration_weeks <= 6`.
2. `FusionBridge` recognises that the predicate's subject is a **quantified
   forward-looking claim** — that is, an estimate wearing an assumption's
   clothes — and writes an `estimated_as` edge between the `Assumption` and the
   `Estimate`.
3. `AssumptionMonitor`, evaluating that predicate, now has a second source of
   evidence beyond current facts: the estimator's calibration history for that
   *work class*.
4. `BiasDetective` answers with a factor, an `n`, and an interval — and refuses
   to answer at all when `n < 5`.
5. If the calibrated value violates the predicate, an `AssumptionBreach` is
   emitted against every `Decision` linked by an `assumes` edge.

And in reverse: when an `Outcome` misses its `Estimate` badly, `CollateralAgent`
walks `justified_by` edges to find every decision that leaned on it.

Neither half can do this alone. Provenance without calibration cannot tell a
stale assumption from a live one that was always optimistic. Calibration
without provenance can tell you a team is 1.8x optimistic on migrations and
cannot tell you which decisions that fact invalidates.

## Data model *(Phase 1, built)*

Versioned, append-only, fully audited. Nothing is updated in place; a change is
a new version plus an `AuditEvent`.

| Record | Holds |
| ------ | ----- |
| `Document` | A normalised source with a content hash |
| `Span` | An addressable range with exact source offsets and a stable id |
| `Decision` | Chosen option, rejected options, maker, date, scope |
| `Assumption` | Plain statement, compiled predicate, expiry condition, status |
| `Estimate` | Quantity, unit, owner, work class, stated confidence, conditions |
| `Outcome` | Actual value, unit, match quality, or explicitly `unresolved` |
| `Link` | A typed edge (see below) |
| `Finding` | Prosecution, challenge, verdict, severity, confidence |
| `AuditEvent` | Who or what wrote what, when, and why |

Edge types: `assumes`, `justified_by`, `contradicts`, `supersedes`,
`estimated_as`, `collateral_of`.

Every extracted claim carries a `span_id`. `VerifierAgent` re-reads that span
and rejects the claim if the span does not actually contain it — this is the
hallucinated-citation gate, and it is deterministic code, not a model.

Ids are typed per kind, and there are two schemes rather than one. Most records
get a sequential, human-readable id (`D-0042`) allocated by the store. `Span`
and `Link` derive theirs from their own coordinates, because for those two the
coordinates *are* the identity: two agents citing the same byte range, or
asserting the same edge, arrive at the same id without coordinating. That makes
citation de-duplication and edge idempotence structural, which is why re-running
an agent cannot fork the graph. See
[ADR 0008](docs/adr/0008-typed-ids-and-append-only-versioning.md).

## Storage *(Phase 1, built)*

One SQLite file. Entity tables, one typed edge table, FTS5 for text search,
recursive CTEs for graph walks. No server. See
[ADR 0003](docs/adr/0003-sqlite-as-the-graph-store.md).

All access goes through a repository layer, so the backend is replaceable
without touching an agent. Three things hold that boundary up:

- **Append-only is enforced by the schema, not by the repository.** Every table
  carries `BEFORE UPDATE` and `BEFORE DELETE` triggers that `RAISE(ABORT)`, so
  invariant 7 is true of anything holding a connection — including a person with
  the `sqlite3` shell and a good reason.
- **The audit row is written in the same transaction as its change.** One
  cannot be lost without the other, so "every mutation produced exactly one
  `AuditEvent`" is true by construction rather than by discipline.
- **No `sqlite3` import leaves the package**, in the failure path as well as the
  query path: driver errors are translated into `praxis.store.errors` by result
  code, never by matching message text.

Migrations are numbered SQL files with a checksummed ledger and no framework —
[ADR 0009](docs/adr/0009-forward-only-migrations-without-a-framework.md). The
store lives in
the platform data directory rather than the repository, because the repository
is on a synced filesystem and WAL sidecars corrupt under one —
[ADR 0010](docs/adr/0010-store-location-under-a-syncing-filesystem.md).

## Orchestration *(Phase 4)*

A small async state machine over a typed message bus, written for this project.
No agent framework. Determinism is the requirement that drives the design: two
runs over one corpus with one seed must produce identical numbers, or the
ablation table means nothing. See
[ADR 0004](docs/adr/0004-custom-async-orchestrator.md).

## Model access *(Phase 2, built)*

```
        agent asks for a ROLE, never a model
                       │
                       ▼
        praxis/config/models.py   ← the only file with a model id
                       │
                       ▼
                 LLMProvider
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   MockProvider  AnthropicProvider  ReplayProvider
   (default,       (live)          (fixtures)
    offline)
```

Every call records agent name, model, prompt hash, token counts, latency, cost
and full input/output to the trace store — one row per *attempt*, failures
included, in a table of its own. The trace store records what an agent was
*told*; the audit trail records what it *changed*. They stay two tables. See
[ADR 0005](docs/adr/0005-offline-first-llm-provider.md) and
[ADR 0006](docs/adr/0006-model-routing-table.md).

`provider_for()` is the only code that reads `PRAXIS_LLM_PROVIDER`, so going
live is an edit to `.env` and nothing else. `MockProvider` does not return
fixed strings: it walks the response schema and builds an answer out of the
prompt, quoting sentences and span ids that really occur in it, so
`VerifierAgent` has something real to check offline — and can still reject it.
`praxis doctor` makes one such call rather than reading the setting, because a
configuration that looks credential-free and a pipeline that answers without a
key are different claims.

`praxis/llm/anthropic.py` is the only module in the repository that imports a
vendor SDK or an HTTP client, and `tests/test_boundaries.py` parses every
module to keep it that way.

## What is deterministic, and why it matters

These never call a model:

| Component | Why |
| --------- | --- |
| `SourceAdapter` | Normalisation is parsing |
| `VerifierAgent` | A hallucination check that could hallucinate is not a check |
| `BiasDetective` | Bias, sample size and intervals are arithmetic |
| `ScoringAgent` | Brier, log score and MAPE are arithmetic |
| Predicate evaluator | A predicate whose truth depends on sampling is not a predicate |

All of it is property-tested with `hypothesis`. `praxis doctor` fails if any of
these acquires a model route.

## Present layout

```
praxis/
  cli.py               typer app: version, config, doctor, init, store stats
  config/settings.py   pydantic-settings; PRAXIS_* environment
  config/models.py     model ids, prices, roles, routing  ← the only place
  domain/ids.py        typed ids; sequential and content-addressed
  domain/records.py    the nine Pydantic records
  domain/links.py      the six edge types and their grammar
  domain/spans.py      the span/document integrity check
  store/location.py    where the database lives, and whether that is safe
  store/connection.py  pragmas, and the transaction every write sits inside
  store/schema/*.sql   numbered migrations: core, then full-text search
  store/migrations.py  forward-only runner, checksummed ledger
  store/mapping.py     record ↔ row, one table driving both directions
  store/repository.py  add / revise / retract, reads. No update, no delete
  store/audit.py       audit writes, always inside the caller's transaction
  store/graph.py       edge reads and the two recursive walks
  store/reports.py     search and counting
  store/errors.py      the store's exception vocabulary; where sqlite3 stops
  store/traces.py      the llm_trace table; append-only, exact decimal cost
  llm/types.py         request, response, usage, stop and outcome vocabulary
  llm/errors.py        the seam's exceptions; where a vendor SDK stops
  llm/hashing.py       the replay key: one identity per request
  llm/accounting.py    what a call cost, and the ceiling checked before it
  llm/trace.py         the trace row and the sink protocol
  llm/provider.py      the seam: route, price, time, trace, raise
  llm/synthesis.py     a plausible answer built from a schema and the prompt
  llm/mock.py          the offline default; deterministic, free, counted
  llm/replay.py        recorded fixtures, and a loud miss
  llm/anthropic.py     the only module that imports an SDK  ← the boundary
  llm/factory.py       the only code that reads PRAXIS_LLM_PROVIDER
  obs/logging.py       structured JSON logging
tests/                 pytest + hypothesis
docs/adr/              decisions, in Praxis's own schema
docs/dogfood/          Praxis's predictions about its own construction
docs/reports/          one report per phase
.claude/hooks/         deterministic enforcement of the rules that matter
```
