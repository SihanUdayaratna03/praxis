---
id: ADR-0004
status: accepted
date: 2026-08-09
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0004 — Own the orchestrator rather than adopt a framework

## Chosen

A small async orchestrator written for this project: an explicit state machine
over a typed message bus, with every agent transition recorded to the audit
trail. No agent framework as a dependency, now or later.

## Rejected

| Option | Why not |
| ------ | ------- |
| LangGraph / LangChain | The orchestration layer is where deterministic replay lives, and frameworks own the control flow that replay has to intercept. Getting byte-identical reruns out of a framework means fighting its abstractions at exactly the point the project cares most about. |
| CrewAI / AutoGen | Optimised for conversational multi-agent patterns. Praxis's pipeline is a mostly-linear DAG with a fusion join, which is a poor fit, and both make the same replay problem worse. |
| Temporal or a real workflow engine | Correct for durable distributed workflows and enormously oversized for a single-process pipeline over a few thousand nodes. Contradicts the zero-install stance of ADR 0003. |
| Plain sequential function calls, no orchestrator | Works until the first parallel fan-out over spans, and gives no natural place to record per-agent audit events, cost accounting, or the abstention decisions the eval harness reports. |

## What was known at the time

The agent roster is about twenty agents in a pipeline that is largely linear
with one join (`FusionBridge`) and one fan-out (per-span scanning). Determinism
is a hard requirement: the eval harness's ablation table is only meaningful if
two runs over one corpus produce identical numbers, which means the
orchestrator must control ordering rather than let the event loop decide it.

Not known: whether the Phase 8 fusion layer needs cyclic execution — an
assumption breach triggering re-evaluation that produces another breach — which
would make a plain DAG insufficient.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | The agent graph stays acyclic, or cycles stay bounded | `max_cycle_iterations <= 3` | `on_event("Phase 8 fusion layer is implemented")` |
| 2 | The orchestrator stays small enough to be an asset rather than a liability | `orchestrator_loc <= 800` | `when(orchestrator_loc > 600)` |
| 3 | Single-process execution is fast enough for the eval corpus | `eval_run_minutes <= 15` | `on_event("Phase 10 eval harness runs end to end")` |
| 4 | Deterministic ordering holds under concurrent fan-out | `identical_reruns_on_same_seed == true` | `when(phases_completed >= 4)` |

## Consequences

**Accepted costs.** Everything a framework would have given free — retries,
scheduling, tracing, visualisation — has to be written. That is the actual bet:
this project is judged partly on owning the layer where its novelty lives, and
the orchestrator is that layer.

**Reversal cost.** High and rising. Agents will be written against this
orchestrator's interfaces, so adopting a framework later means rewriting the
integration surface of every agent. Assumption 2 exists to catch the case where
the orchestrator grows into a bad framework of its own — at which point the
honest move is to say so in a superseding ADR rather than keep building.
