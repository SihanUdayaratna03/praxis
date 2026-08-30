---
id: ADR-0006
status: accepted
date: 2026-08-09
decision_maker: Sihan Udayaratna
impact: medium
supersedes: null
superseded_by: null
---

# 0006 — Route by intent to three model tiers

## Chosen

Agents request a **role**, not a model. Three roles map to three tiers in
`praxis/config/models.py`, which is the only file in the repository permitted
to contain a model identifier.

| Role | Model | USD / Mtok | What it is for |
| ---- | ----- | ---------- | -------------- |
| `scan` | `claude-haiku-4-5` | 1.00 / 5.00 | High-recall, low-precision passes over raw text |
| `extract` | `claude-sonnet-5` | 3.00 / 15.00 | Filling a schema from an already-identified span |
| `reason` | `claude-opus-5` | 5.00 / 25.00 | Judgement calls the product's claims rest on |

Assignments:

- **scan** — `SegmenterAgent`, `DecisionScout`, `EstimateExtractor`,
  `WorkClassifier`
- **extract** — `DecisionStructurer`, `AssumptionMonitor`, `OutcomeMatcher`,
  `CalibratorAgent`, `ReviewTriageAgent`, `CuratorAgent`, `AbstentionGate`,
  `ReporterAgent`
- **reason** — `AssumptionExtractor`, `AssumptionFormalizer`,
  `ContradictionDetector`, `ArchaeologistAgent`, `FusionBridge`,
  `CollateralAgent`, `ChallengerAgent`

`SourceAdapter`, `VerifierAgent`, `ScoringAgent` and `BiasDetective` are
deterministic and appear in `NON_LLM_AGENTS`. Asking for a route on one raises;
`praxis doctor` asserts the two sets never overlap.

> **Amended by [ADR 0025](0025-the-calibrator-explains-rather-than-generates.md),
> Phase 7.** `CalibratorAgent` moved from `extract` to `NON_LLM_AGENTS` once it
> was built: its explanation turned out to be a template over numbers it was
> handed, not a generation. The lists above are the Phase 2 assignment as
> reasoned before any agent existed, kept because rewriting them would destroy
> the provenance this directory exists for.
> `praxis/config/models.py` and `praxis doctor` are the live answer.

> **Amended again by
> [ADR 0026](0026-the-bridge-prices-a-judgement-phase-4-already-made.md),
> Phase 8.** `FusionBridge` moved from `reason` — the most expensive tier here —
> to `NON_LLM_AGENTS`. The judgement its route was paying for is real, and
> `AssumptionExtractor` makes it one phase earlier and writes the answer down as
> an `estimated_as` edge (ADR 0016); ADR 0019 had already closed the other end,
> since only arithmetic can breach. What was left in between is a graph walk, an
> indexed read, a multiplication and two predicate evaluations. `CollateralAgent`
> keeps its `reason` route.

Model IDs, context windows and prices were read from the Anthropic model
reference on 2026-08-09 and are cited in the module, not recalled.

## Rejected

| Option | Why not |
| ------ | ------- |
| One model everywhere | Simple, and wrong on both ends. Scanning is the highest-volume call in the pipeline, so paying the top tier for it dominates cost per document — a reported eval metric. Meanwhile the fusion inference is the product's central claim, and a cheap wrong answer there is worse than an expensive right one. |
| Model chosen per agent, at the agent | Twenty scattered identifiers, a twenty-file change on deprecation, and no single place to read the cost story off. |
| Route dynamically by measured input difficulty | Attractive and premature. It would make cost non-deterministic, which breaks the reproducibility that ADR 0004 and the ablation table depend on. Revisit once there are real measurements; recorded in `BACKLOG.md`. |
| Put `BiasDetective` on a model | It computes directional bias with confidence intervals — arithmetic. An LLM there would make the calibration numbers irreproducible and unfalsifiable, defeating the property tests that are the evidence the maths is right. |

## What was known at the time

The Anthropic catalogue and prices as of 2026-08-09. Sonnet 5 carries
introductory pricing of 2.00/10.00 through 2026-08-31; the registry records the
standard 3.00/15.00 so cost estimates do not flatter themselves. Volume per
role is unmeasured — no corpus has been run — so the assignments are reasoned
from the nature of each task, not from data.

Not known: the actual token volume per role, and therefore whether `scan`
dominates cost as expected; and whether the cheap tier's recall is adequate for
`DecisionScout`, which is the assignment most likely to be wrong.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | Scanning is the volume driver, so tiering it down materially cuts cost | `scan_share_of_total_tokens >= 0.5` | `on_event("Phase 10 eval harness reports per-role token counts")` |
| 2 | The cheap tier has adequate recall for scanning | `scout_recall >= 0.9` | `on_event("DecisionScout is evaluated against the synthetic corpus")` |
| 3 | Every routed model identifier is still valid | `all_routed_models_valid == true` | `on_event("Anthropic deprecates a routed model")` |
| 4 | Sonnet 5 pricing does not exceed the recorded standard rate | `sonnet_5_input_usd_per_mtok <= 3.00` | `after("2026-08-31")` |
| 5 | Per-document cost stays inside the budget a judge would find credible | `cost_per_document_usd <= 0.05` | `on_event("Phase 10 eval harness reports cost per document")` |

## Consequences

**Accepted costs.** Three tiers is three sets of prompt behaviour to tune, and
an agent on the wrong tier produces a quality problem that looks like a prompt
problem. Assumption 2 is the one most likely to fire: high recall on the
cheapest model is exactly the thing that tends not to hold, and the fix — move
`DecisionScout` to `extract` — is a one-line change here.

**Reversal cost.** Very low by construction. Re-tiering an agent is one line;
replacing a deprecated model is one line. That property is the whole reason the
table exists.
