---
id: ADR-0035
status: accepted
date: 2026-09-01
decision_maker: Sihan Udayaratna
impact: medium
supersedes: null
superseded_by: null
---

# 0035 — The ablation ladder is cumulative, and every rung runs the whole corpus

## Chosen

Phase 10's ablation table is a **cumulative ladder**. Rung 1 is the paragraph
floor with extraction and nothing above it; each rung after it is the rung below
plus exactly one component, and the last rung is the ordinary graded run. Every
rung runs the same corpus, with the same seed, in a store of its own.

A rung is a `Stages` — six booleans — passed to `evaluate`. It is not a second
harness and not a fork of the pipeline: each flag gates one call in the function
that already ordered those calls, so a rung cannot drift from what `praxis eval`
really runs.

Each rung reports the metrics the brief named: precision and recall over the
graded kinds, citation integrity, assumption-breach detection, calibration MAE
improvement, abstention precision, fusion recall, and cost per document.

## Rejected

| Option | Why not |
| ------ | ------- |
| Leave-one-out: the full pipeline minus one component per row | Reads better for attributing a single component and is the wrong shape for this pipeline. The stages are ordered by dependency — the monitor needs compiled predicates, fusion needs calibrated factors, governance needs findings to argue with — so removing a middle component either breaks the rungs above it or silently reconfigures them. A leave-one-out table would print differences that are about the dependency being missing rather than about the component. |
| A rung per agent rather than per stage | Finer, and unattributable. Three agents run inside the memory pass alone and each depends on the last; splitting them would produce rows whose differences are about ordering. The stage boundaries are where the dependencies actually are. |
| Run every rung against one shared store | Would halve the cost and destroy the measurement. Later stages write records, and a rung reading a store an earlier rung wrote would grade a pipeline nobody ran. A store per rung is what makes the difference between two rows attributable. |
| Ablate the citation gate as a rung | Invariant 6 has no convenience exception, so the gate cannot be turned off to measure it. ADR 0034 already settled this: its contribution is computed counterfactually from the refusal record, and it appears in the table as citation integrity per rung rather than as a rung of its own. |
| Report only precision and recall | The brief names seven metrics and five of them are about components that extraction precision cannot see. A table with two columns would show the last four rungs as identical rows, which is a statement about the columns rather than about the pipeline. |

## What was known at the time

The floor segmenter was built in this phase and makes **zero** model calls,
which is what lets the cost column be compared between rung 1 and rung 2 at all.
The stages were already ordered inside `evaluate` with comments saying which
could move and which could not, so gating them cost one conditional each.

Two rungs were expected to move almost nothing, and that was known before the
table was run rather than discovered in it. Calibration refuses every group in a
corpus this size — no group reaches `MINIMUM_SAMPLE` — so the MAE column is
expected to report *not measured* rather than an improvement. Governance changes
no extraction number by construction: it argues about findings and writes no
claims.

What is **not** known: whether the differences between rungs are large enough to
read at 68 documents. A corpus this size gives every rate a coarse denominator,
and a difference of one record moves a rate by more than the component might be
worth. The table is honest about the denominator on every row for that reason.

Also not known, and inherited from ADR 0034: the numbers are the coherent mock's
throughout. The ladder measures whether a component *changes* what the pipeline
produces, which is a real question offline. It does not measure whether the
component is any good, which is not.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | Turning every stage on reproduces the ordinary graded run exactly | `ablation_top_rung_differs_from_eval == 0` | `on_event("a stage is added to or removed from evaluate")` |
| 2 | The floor rung spends nothing on segmentation, so cost is comparable | `floor_rung_segmenter_cost == 0` | `on_event("the floor segmenter is given a model call")` |
| 3 | Each rung's store is its own, so no rung reads what another wrote | `ablation_shared_stores == 0` | `on_event("the ladder is run against a persistent store")` |
| 4 | The ladder stays affordable to run in CI | `ablation_runtime_seconds <= 600` | `when(corpus_documents > 200)` |

## Consequences

**Accepted costs.** The ladder runs the corpus seven times, so `praxis eval
--ablate` costs roughly seven ordinary evals. Offline that is time and nothing
else; against a real provider it would be seven times the money, which is why
the flag is opt-in and the plain `praxis eval` is unchanged.

A cumulative ladder also cannot separate two components that were added in the
same rung. The memory rung is three passes and its row is their sum. That is the
price of putting the rungs on the dependency order, and the finer split is in
`BACKLOG.md` rather than pretended at here.

**Reversal cost.** Low. `Stages` is six booleans and the ladder is a tuple of
them; a leave-one-out table would be a different tuple over the same mechanism.
Nothing in `evaluate` is shaped by the ladder's being cumulative.
