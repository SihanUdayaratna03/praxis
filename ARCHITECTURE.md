# Architecture

> Status: this describes the system Praxis is being built toward. Phase 0 has
> shipped the foundation only — config, logging, CLI, CI, hooks. Sections
> marked *(not built)* are the target, not the present. Each phase updates
> this file when something structural lands.

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

## Data model *(Phase 1)*

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

## Storage *(Phase 1)*

One SQLite file. Entity tables, one typed edge table, FTS5 for text search,
recursive CTEs for graph walks. No server. See
[ADR 0003](docs/adr/0003-sqlite-as-the-graph-store.md).

All access goes through a repository layer, so the backend is replaceable
without touching an agent.

## Orchestration *(Phase 4)*

A small async state machine over a typed message bus, written for this project.
No agent framework. Determinism is the requirement that drives the design: two
runs over one corpus with one seed must produce identical numbers, or the
ablation table means nothing. See
[ADR 0004](docs/adr/0004-custom-async-orchestrator.md).

## Model access *(Phase 2)*

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
and full input/output to the trace store. See
[ADR 0005](docs/adr/0005-offline-first-llm-provider.md) and
[ADR 0006](docs/adr/0006-model-routing-table.md).

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
  cli.py              typer app: version, config, doctor
  config/settings.py  pydantic-settings; PRAXIS_* environment
  config/models.py    model ids, prices, roles, routing  ← the only place
  obs/logging.py      structured JSON logging
tests/                pytest + hypothesis
docs/adr/             decisions, in Praxis's own schema
docs/dogfood/         Praxis's predictions about its own construction
docs/reports/         one report per phase
.claude/hooks/        deterministic enforcement of the rules that matter
```
