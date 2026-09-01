# Phase 10 — the evaluation harness

> The phase in which the metrics stop being assembled by hand and become one
> re-runnable command. Its most useful output is not the table: it is the
> assumption from Phase 3 that the table breaks.

## What shipped

| | |
| --- | --- |
| Branch | `feat/phase-10-eval-harness`, cut from `main` at `b43b21e` |
| Commits | 21, one logical change each |
| Suite | **3120 passed**, coverage **99.07%** (gate 85%) |
| New modules | `praxis/eval/stages.py`, `ablation.py`, `ablation_report.py` — every one at **100%** |
| ADRs | 0033, 0034, 0035 |
| Schema | version 4 — **no migration**, for the fifth phase running |
| Corpus | 16 topics, 82 documents, 250 items, 88 distractors |

`praxis eval --ablate` is the command. No new agents, as `EST-0011` said.

## The ablation table

82 documents, seed 20260809, the coherently-citing mock (ADR 0034), offline.

| Rung | Adds | Precision | Recall | Citations | Breach recall | MAE improvement | Abstention precision | Findings | Flips | Fusion recall | Cost/doc | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| floor | paragraph floor, extraction only | 0.0852 | 0.2346 | 0.8463 | 0.0000 | -- | 0 (0 emitted) | 0 | 0 | 0.0000 | 0.000000 | 486 |
| segmenter | learned segmentation | 0.0852 | 0.2346 | 0.8463 | 0.0000 | -- | 0 (0 emitted) | 0 | 0 | 0.0000 | 0.000000 | 568 |
| + estimation | half B: outcomes and classification | 0.0686 | 0.3025 | 0.8981 | 0.0000 | 3.3888 (0.7810 of 4.3390) | 0 (0 emitted) | 0 | 0 | 0.0000 | 0.000000 | 1292 |
| + memory | formalization, monitoring, detection | 0.0686 | 0.3025 | 0.8981 | 0.3125 | 3.3888 (0.7810 of 4.3390) | 1.0000 (0 emitted) | 34 | 0 | 0.0000 | 0.000000 | 1476 |
| + calibration | per-group bias correction | 0.0686 | 0.3025 | 0.8981 | 0.3125 | 3.3888 (0.7810 of 4.3390) | 1.0000 (0 emitted) | 35 | 0 | 0.0000 | 0.000000 | 1476 |
| + fusion | estimated_as edges and the flips they cause | 0.0686 | 0.3025 | 0.8981 | 0.3125 | 3.3888 (0.7810 of 4.3390) | 1.0000 (0 emitted) | 56 | 0 | 0.0000 | 0.000000 | 1476 |
| + governance | challenger, curator, abstention gate | 0.0686 | 0.3025 | 0.8981 | 0.3125 | 3.3888 (0.7810 of 4.3390) | 1.0000 (6 emitted) | 56 | 0 | 0.0000 | 0.000000 | 1483 |

Cumulative, one component per rung, each rung in a store of its own
([ADR 0035](../adr/0035-the-ablation-ladder-is-cumulative.md)). Every number is
the mock's, and the caveat is in the artefact rather than only here.

## The finding: ADR 0011 assumption 1 is breached

[ADR 0011](../adr/0011-semantic-segmentation-over-a-deterministic-block-grid.md)
said in Phase 3 that block grouping beats the deterministic paragraph floor:

> `segmenter_f1 - paragraph_floor_f1 >= 0.05`, expiring
> `when(phases_completed >= 10)`.

The expiry has fired, and the measurement now exists. The two rungs produce
**byte-identical** counts — 38 true positives, 408 false positives, 124 false
negatives, F1 **0.1250** each. The difference is **0.0000** against a required
0.05, and `praxis.predicates` evaluates the predicate to `Truth.FALSE` — run
through the shipped evaluator rather than judged by eye. The segmenter's only
measured effect is **82 extra model calls**.

**This is not recorded as false about the world, and the distinction is not a
hedge.** Offline the segmenter's grouping is synthesised by the mock rather than
reasoned, so the measurement is evidence about `praxis.llm.synthesis`. What it
does close is the excuse: the number was unmeasurable for seven phases and it is
measurable now, and it is not in the assumption's favour. `BACKLOG.md` carries
it for re-measurement on the first live run.

An assumption written in Phase 3 with an expiry that fired in Phase 10, caught
by a harness built in between, is the product working on its own history. It is
also the first time this project's own evidence has contradicted one of its
decisions.

## What each rung actually bought

**Half B trades precision for recall, and the trade is not obviously good.**
Recall 0.2346 → 0.3025, precision 0.0852 → 0.0686, F1 **0.1250 → 0.1118**. The
estimation half writes 11 more true positives and 257 more false positives. On
F1 it is a regression, and it is reported as one.

**Citation integrity rises with Half B**: 0.8463 → 0.8981. More claims are
offered by agents whose quotations the coherent mock draws from the passage they
cite, so the gate refuses proportionally fewer.

**Three rungs write findings and no claims.** Calibration, fusion and governance
move no extraction number by construction, and the first run of the ladder
showed them as three identical rows. The `Findings` and `Flips` columns exist
because of that run: findings go 34 → 35 → 56, and governance emits 6 of them.
A table whose rows are identical is a statement about its columns.

**Flips are zero.** The fusion rung prices its edges and files 21 findings, but
no calibrated value turned a held predicate into a violated one. Consistent with
Phase 8, and it stays zero until a group carries a factor big enough to move a
predicate across its threshold.

**Fusion recall is zero on every rung.** The mock's citations are coherent but
its passage is drawn at random, so no `estimated_as` edge joins the assumption
the key labels to the estimate it labels. Reported beside the count of edges
actually written rather than folded into it — an edge the key does not label is
unjudgeable, not wrong.

**Cost is zero per document on every rung**, because offline it is. The column
is in the table so that the first live run has a shape to fill.

## MAE improvement, and the denominator it rests on

**3.3888 absolute, 78.10% relative, from a raw error of 4.3390 — over `scored =
2`.** Two rows. The number is real arithmetic over real stored outcomes and it
is nearly meaningless as evidence, which is why `scored` travels with it in both
the markdown and the JSON.

ADR 0035 predicted this column would read *not measured* at this corpus size.
**That prediction was wrong**, and the reason is worth keeping: it was written
against the 10-document probe, and at 82 documents two groups reach
`MINIMUM_SAMPLE`. The prediction was stated before the run rather than after,
which is the only reason it can be scored at all.

The column also fills at the **`+ estimation`** rung, one rung *below*
calibration. That is correct and slightly counter-intuitive: the backtest is
arithmetic over stored outcomes (invariant 3), so it is computable as soon as
outcomes exist. `calibrate_store` writes findings, not the correction itself.

## Two defects the tests found before the table did

**`EvalResult` had no total call count.** `run.calls` is the extraction run's
alone, so the floor rung and the learned segmenter both reported 52 calls and
ADR 0035 assumption 2 looked false. `evaluate` had been summing every stage's
calls for its log line and throwing the number away. Found by
`test_the_floor_rung_makes_fewer_calls_than_the_rung_above_it`, not by reading.

**Abstention precision is 1.0000 on every rung below governance, and that is
correct.** The gate is arithmetic (ADR 0032), so it is recomputed on any store;
below the governance rung nothing has argued the findings, so every one fails
`never_challenged` and every abstention really does fail a rule. The precision
is trivially perfect and says nothing. `emitted` is the column that separates
the two, and the table carries the caveat under it. The test that first asserted
a precision of *zero* there was asserting the wrong thing.

Both were mine, both were caught by a test written against a claim rather than
against the code, and neither would have been visible in the table.

## The fusion recall defect, from the session before

`fusion_recall` divided every `estimated_as` edge in the store by the number the
corpus labels — two different sets — so the ratio could exceed 1, and did, at
**9.3333**, once the coherent mock started writing spurious edges. Latent for six
phases because no offline run had ever written more edges than the key holds.
Fixed in `d65693f`; the numerator joins through the assumption and estimate
pairings now, which is the join `praxis.eval.memory` already makes for the same
reason. Verified under the full suite, not only the file it was written against.

## Deviations, stated rather than buried

- **Seven rungs, not the ten `EST-0011` priced.** There are seven stage
  boundaries in `evaluate`. The missing three could only come from splitting the
  memory pass into its three agents, which ADR 0035 rejects on its merits: they
  depend on each other in order, so a rung between two of them would print a
  difference about the missing dependency. In `BACKLOG.md` with that reason, and
  the estimate's subject is **not** edited to match — a subject rewritten to fit
  its outcome is the one thing that would make Phase 12 worthless.
- **`praxis eval --ablate`, not `praxis eval ablate`.** A flag on the command
  that already had the corpus argument, the ground-truth check, `--json`,
  `--markdown` and the provenance line. A subcommand would have duplicated all
  five.
- **The citation gate is not a rung.** Invariant 6 has no off switch, so its
  contribution is computed counterfactually from the refusal record (ADR 0034)
  and appears as the citations column instead.

## ADR predicate parse rate

Re-checked after 0033, 0034 and 0035 landed: **139 of 140, `0.9929`**, against
ADR 0001's `>= 0.9`. All 140 expiry conditions parse. The single unreadable row
is still ADR 0015's `mis_attribution_rate / fabrication_rate outside [0.5, 2.0]`,
which is prose in a predicate column rather than a gap in the grammar.

## The estimate

`EST-0011`: **3.1h active**, corrected from a raw 6.0h, confidence 0.42,
`agent-implementation`. See `OUT-0011` for what it cost and for the third
consecutive reading of the same bias.
