---
id: ADR-0038
status: accepted
date: 2026-09-03
decision_maker: Sihan Udayaratna
impact: medium
supersedes: null
superseded_by: null
---

# 0038 — The routing table stands, and a priced projection is what says so

## Chosen

**No agent changes tier.** ADR 0006's routing table, extended piecemeal across
Phases 4 to 9, was read as a whole for the first time in Phase 12 and every one
of the fifteen routed agents keeps the role it has.

The review is not "we looked and it seemed fine". It produced a number the
repository did not have: **what this pipeline would cost per document at the
real prices in `praxis/config/models.py`**, computed from the token counts an
offline run actually sends. `praxis eval` prints it beside the zero column that
was there before, and `praxis.store.traces.tokens_by_agent` and
`praxis.eval.metrics.projected_cost_per_document` are how.

| Agent | Role | Projected USD / document |
| ----- | ---- | ------------------------ |
| `AssumptionFormalizer` | reason | 0.019719 |
| `OutcomeMatcher` | extract | 0.018480 |
| `AssumptionExtractor` | reason | 0.016761 |
| `DecisionStructurer` | extract | 0.011455 |
| `WorkClassifier` | scan | 0.004336 |
| `EstimateExtractor` | scan | 0.003119 |
| `DecisionScout` | scan | 0.001710 |
| `ContradictionDetector` | reason | 0.001364 |
| `ChallengerAgent` | reason | 0.000788 |
| `SegmenterAgent` | scan | 0.000594 |
| **Total** | | **0.078326** |

By role: **reason 0.038632 (49.3%), extract 0.029935 (38.2%), scan 0.009759
(12.5%)**. 82 documents, seed 20260809, the coherently-citing mock.

**Two things fall out of that table immediately.** The `scan` tier is an eighth
of the bill, so re-tiering anything *down to* it saves at most a rounding error
unless the agent moved is one of the four expensive ones. And an 82-document
run projects to **$6.42**, which is above the `cost_ceiling_usd` default of
**$5.00** — the first live run of the eval harness will stop part-way unless
that ceiling is raised deliberately. Nobody could have known that before this
number existed.

## Rejected

| Option | Why not |
| ------ | ------- |
| Move `AssumptionFormalizer` from `reason` to `extract` | The strongest candidate and the one that was actually argued. It is the single most expensive agent at 25% of the bill, and its job — turn one sentence into `subject op number` — reads like schema-filling, which is `extract`'s stated brief. It stays for a reason that is about failure mode rather than difficulty: **a wrong predicate is silent**. It parses, it evaluates, the monitor believes it, and a decision is then held or breached on an expression nobody re-read. Choosing the subject name is also a coordination problem — the same quantity has to get the same name across ADRs or two predicates about one thing never meet. Both are the "a wrong answer here is worse than an expensive one" case `ModelRole.REASON` was written for. |
| Move `OutcomeMatcher` from `extract` to `scan` | Second most expensive, 24%. Declined because ADR 0021 already took the cheap half: match *quality* is arithmetic, so what remains at the model is only the pairing — which outcome answers which estimate — and a wrong pairing writes a wrong ratio into the calibration history that every later factor is computed from. That is the input to the product's second half, and `scan` is specified as high-recall and low-precision. |
| Move `ArchaeologistAgent` from `reason` to `extract` | Tempting on principle: ADR 0020 has it select two ordinals from a numbered listing and never generate, which sounds like the cheapest job in the system. It stays for two reasons, and the second is the decisive one. A wrong selection attaches a real recorded reason to the wrong question, which ADR 0020 already calls the expensive outcome against a cheap refusal. And it is **not in the table above at all**, because it makes no calls in a corpus run — it fires once per `praxis why`, interactively. Re-tiering it saves nothing that can be measured. |
| Move `ContradictionDetector` or `ChallengerAgent` down | Together they are **0.002152 per document, 2.7% of the bill**. Moving both to `extract` would save under a cent per hundred documents and would put the two agents whose whole output is a judgement about other agents' output on a tier chosen for schema-filling. ADR 0030 already decided the challenger's route on its merits and nothing here disturbs it. |
| Move something anyway, to have a result | The phase brief ruled this out in advance and the data agrees with the brief. See the section below: there is no evidence in this repository that could tell a good tier change from a bad one. |
| Fit the routing to the ablation table | **The ablation table cannot answer this question and it is important to say why.** Every `Cost/doc` cell in Phase 10's ladder reads `0.000000`, because the mock is free, and **no rung varies a model tier** — the ladder ablates components. So the table contains no observation of any agent on any other tier, and a tier change justified by it would be a change justified by nothing. This ADR exists partly to stop that reading. |

## What was known at the time

**The routing table was never designed as a whole.** ADR 0006 set it in Phase 0
against agents that did not exist yet, and the amendments since were made one
agent at a time: `CalibratorAgent` left in Phase 7 (ADR 0025), `FusionBridge`
and `CollateralAgent` in Phase 8 (ADRs 0026, 0027), `CuratorAgent` and
`AbstentionGate` in Phase 9 (ADRs 0031, 0032). Phase 12 is the first time the
fifteen remaining rows were read together.

**The cost optimisation that mattered already happened, and it was not a
re-tiering.** Five agents did not move to a cheaper model; they left the table
entirely and became arithmetic. That is a reduction to zero, permanently, on
five of twenty agents, and no tier change available today is in the same class.
The pattern behind all five is the same and it is worth stating as the finding:
each was paying a model to make a judgement **somebody upstream had already been
paid to make**. That is the question to ask of the remaining fifteen, and it is
a better question than "could this run on a smaller model".

**What is not known, and cannot be from here.** Whether any agent's precision
or recall would survive a cheaper tier. The offline provider ignores which model
was asked, so re-tiering and re-running the harness produces byte-identical
numbers — the eval harness is structurally incapable of detecting a quality
regression from a tier change. That is not a gap in the harness; it is what
invariant 1 costs, and ADR 0005 assumption 3 is where it gets paid.

**So the review's real output is a shortlist with its own experiment attached.**
`AssumptionFormalizer` and `OutcomeMatcher` are 49% of the bill between them and
are the only two moves worth measuring. The measurement is the same for both:
run the corpus twice against a real provider, once on the current tier and once
one tier down, and compare `predicates_parsed` / `checkable_rate` for the first
and `match_rate` for the second. Neither is a guess that needs making today.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | The reason tier is where the money is, so cost work that ignores it is not cost work | `reason_share_of_projected_cost >= 0.40` | `on_event("the first live extraction run reports a cost")` |
| 2 | An 82-document run costs more than the configured ceiling allows | `projected_usd_per_eval_run > cost_ceiling_usd` | `on_event("cost_ceiling_usd is changed")` |
| 3 | The scan tier is small enough that re-tiering into it is not worth a phase | `scan_share_of_projected_cost <= 0.20` | `on_event("the first live extraction run reports a cost")` |

## Consequences

**Accepted costs.** The pipeline stays on its current, more expensive routing
through the first live run, and if `AssumptionFormalizer` really is an `extract`
job then the project pays 25% more than it needs to until somebody measures it.
That is accepted knowingly: the alternative is paying an unknown amount of
precision for a known amount of money, which is the worse trade on a system
whose output is meant to be read by a person deciding something.

The `cost_ceiling_usd` default is left at 5.00 rather than raised to fit the
projection. Raising a limit because a projected bill exceeded it, before any
real bill exists, would be fitting the safety rail to the estimate.

**Reversal cost.** One line in `_ROUTING` per agent, and a re-run of the eval
harness. The routing table is a mapping in one file precisely so that this
decision costs nothing to revisit — which is why it was correct to make it on
the evidence available rather than to defer it.
