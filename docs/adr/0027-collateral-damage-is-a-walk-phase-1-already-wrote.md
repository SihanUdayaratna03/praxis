---
id: ADR-0027
status: accepted
date: 2026-08-31
decision_maker: Sihan Udayaratna
impact: medium
supersedes: null
superseded_by: null
---

# 0027 — collateral damage is a walk Phase 1 already wrote

## Chosen

`CollateralAgent` **makes no model call**. It leaves ADR 0006's `reason` row and
joins `NON_LLM_AGENTS`, for the same structural reason as ADR 0026 and with less
to argue about.

The reverse direction of the fusion mechanism — *an estimate missed, which
decisions leaned on it?* — decomposes into three questions, and **every one of
them was already answered by an earlier phase**:

| Question | Answered by | Since |
| -------- | ----------- | ----- |
| Which misses are worth surveying? | `MatchQuality.MISS`, computed at write time | Phase 6, ADR 0021 |
| Which decisions rest on this estimate? | `Repository.impacted_by` over `DEPENDENCY_LINK_TYPES` | Phase 1 |
| How urgently does it need a person? | `praxis.monitor.breach.severity_for` | Phase 5 |

Phase 1 built the three dependency edges pointing the same way — dependent to
depended-upon — and `praxis/domain/links.py` says in as many words why: *"an
estimate missed — what rests on it?" is reverse reachability over
`DEPENDENCY_LINK_TYPES` from that estimate, and nothing else.* `impacted_by`'s
own docstring calls itself "the fusion query". This module asks it.

**One finding per missed estimate, not one per damaged decision**, filed against
the estimate and naming the decisions in the prosecution. `severity_for` is
imported rather than reimplemented.

## Rejected

| Option | Why not |
| ------ | ------- |
| Keep the `reason` route and let a model decide which decisions were really damaged | The graph already knows. A model asked the same question could only agree with the walk or contradict it, and a contradiction would be an ungraded opinion overruling the edges that three phases of extraction were graded on writing. |
| Let a model decide how badly an estimate missed | That is `match_quality`, and ADR 0021 made it arithmetic over two numbers precisely so it could not become an opinion. A second threshold here would be the override ADR 0024 refused, arriving through a different door. |
| Walk `estimated_as` and `assumes` by hand instead of using `impacted_by` | The mistake this record exists to have avoided. A hand-rolled walk from the estimate would most naturally follow the two-hop route through the assumption and miss `justified_by` entirely — which is the special-case-per-hop that the edge directions were arranged to prevent. `tests/agents/test_collateral.py` asserts both routes come back. |
| One finding per damaged decision | `praxis.monitor.breach` rejected this for a breached assumption and the argument is unchanged: the count of findings would become a count of citations, so a report of collateral impacts would rank an estimate cited by six decisions above one cited by two regardless of severity. |
| A new `FindingKind` | `COLLATERAL_IMPACT` has existed since Phase 1 with nothing writing it, and so has `LinkType.COLLATERAL_OF`. This is what writes them. |
| Survey `UNRESOLVED` outcomes too | An estimate nothing ever answered has not been shown to be wrong. `unresolved` exists so those estimates stay *visible* in the calibration data (ADR 0022), not so they can be prosecuted, and raising findings from silence would fill a triage queue with allegations no evidence supports. |

## What was known at the time

`CollateralAgent` was assigned `reason` in Phase 0 alongside `FusionBridge`, on
the same reading of `ARCHITECTURE.md` and with the same result.

**This is the sixth consecutive component whose route moved to the arithmetic
side, and the pattern is now worth naming rather than celebrating again.**
`VerifierAgent` (Phase 3), `match_quality` (Phase 6, ADR 0021),
`CalibratorAgent` (Phase 7, ADR 0025), `FusionBridge` and now this. Six is no
longer a run of good corrections; it is evidence about the architecture document
itself. Two readings fit and they have different consequences:

1. `ARCHITECTURE.md` describes components by *the question they answer* rather
   than by the work they do. "Recognise that an assumption is an estimate" and
   "find the decisions damaged by a miss" are both hard questions, and both turn
   out to be already-answered questions by the time the component runs. On this
   reading the routing table was never a claim about implementation and the
   corrections are bookkeeping.
2. The judgement is real and it is being pushed steadily earlier — into
   extraction, where it is paid for once and written down as an edge. On this
   reading the arithmetic components are cheap *because* Phase 4 is expensive,
   and the honest cost of a fusion finding includes the `reason`-tier call that
   wrote the `estimated_as` edge four phases earlier.

The second is the better description and it is the one the phase report carries.
It also predicts where this stops: the components that still hold a route —
`ChallengerAgent`, `ReviewTriageAgent` — are the ones whose input is prose
nobody has yet reduced to a record, and they should be expected to keep it.

Not known: whether reading `Estimate.conditions` would change which misses count
as calibration signal rather than scope change. `BACKLOG.md` carries it and
places its first consumer in Phase 9; it is deliberately not smuggled in here,
because it needs a model and this component must not.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | The agent never acquires a model route | `collateral_module_llm_imports == 0` | `on_event("conditions are read when deciding what a miss means")` |
| 2 | The walk stays a single query rather than one per hop | `collateral_graph_queries_per_miss <= 1` | `when(stored_estimates >= 500)` |
| 3 | Most misses damage nothing, so the queue stays readable | `damaging_misses / misses <= 0.5` | `when(stored_outcomes >= 40)` |
| 4 | One triage severity, computed in one place | `severity_implementations == 1` | `on_event("a third finding kind needs a severity")` |

## Consequences

**Accepted costs.** `praxis.agents` now imports from `praxis.monitor`, which is
a new direction between those two packages. It is deliberate and it is the
cheaper of the two options: the alternative was a second `severity_for`, and two
severity functions that drift apart would produce two triage orders for one set
of facts. If the dependency becomes awkward, the repair is to move
`severity_for` into a module both import, not to copy it.

The routing table now has three entries fewer than the architecture diagram has
agents. ADR 0006 carries amendment notes for all three and `praxis doctor` is
the live answer.

A miss that damages nothing still produces a survey entry, so the survey is
longer than the set of findings worth acting on. That is intended: "how often is
a miss harmless" is a question the Phase 12 demo needs an answer to, and a
survey filtered to the damaging ones cannot answer it.

**Reversal cost.** Low. One line in `_ROUTING`, one in `NON_LLM_AGENTS`, a
provider argument on the constructor. Unlike ADR 0026 the reversal here is also
low-risk: nothing about this component's output would become unverifiable,
because the walk it reports is reproducible from the store by anyone who runs
`impacted_by` themselves.
