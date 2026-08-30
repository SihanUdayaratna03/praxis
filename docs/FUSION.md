# The fusion layer

> The part of Praxis that justifies the rest of it. Built in Phase 8; every
> mechanism described here exists in code and is tested. Where something is not
> built, this file says so.

## The claim in one paragraph

Most decision assumptions are estimates in disguise. "We can migrate the ledger
in six weeks" is written down as a belief and behaves like a belief — it sits in
an ADR, nobody re-reads it, and it quietly justifies everything downstream. But
it is a **quantified forward-looking claim about work**, which is exactly what an
estimate is. And estimates have something beliefs do not: a track record.

So Praxis asks a question nothing else in the toolchain asks. Not "was this
decision good?" and not "is this team's estimation accurate?", but:

> **Given how wrong this person usually is about this kind of work, is this
> decision still standing on solid ground?**

## The mechanism, precisely

Five steps. **Two of them were built before Phase 8**, which is the single most
important thing to understand about this layer.

```
  Decision ──assumes──▶ Assumption ──estimated_as──▶ Estimate
      │                      │                          │
      │                      │ 1. compiled to a          │ 3. (owner, work_class)
      │                      │    predicate              ▼
      │                      ▼                     BiasDetective
      │             migration_weeks <= 6                 │
      │                      │                           │ 4. factor, n,
      │                      │                           │    band, confidence
      │                      ▼                           ▼
      │              5. evaluate twice ◀──────── raw × factor
      │                      │
      └──────────────────────┴──▶ StaleDecision, if and only if the verdict FLIPPED
```

1. **`AssumptionFormalizer` compiles the assumption into a predicate.**
   `migration_weeks <= 6`. Phase 5. It is a real expression in a small total
   language, not a string — `praxis/predicates/`.

2. **`AssumptionExtractor` recognises that the assumption is an estimate and
   writes an `estimated_as` edge.** *Phase 4*, not Phase 8. This is the
   judgement in the whole mechanism, it costs a `reason`-tier model call, and it
   happens inside a call that is already holding the assumption and its
   quantity. [ADR 0016](adr/0016-the-extractor-writes-the-first-estimated-as-edge.md).

3. **`FusionBridge` walks that edge** and resolves the estimate's
   `(owner, work_class)`.

4. **`BiasDetective` answers, or refuses.** One indexed read plus arithmetic
   over the estimator's history in that class of work. It returns a factor, an
   `n`, a multiplicative band and a confidence — and below five resolved
   estimates it **refuses, with no override, and the factor is not computed
   rather than computed and withheld**.
   [ADR 0024](adr/0024-dispersion-widens-the-band-and-only-n-refuses.md).

5. **The predicate is evaluated twice** — once against the raw estimate, once
   against `raw × factor` — and a finding is raised **if and only if the verdict
   flipped**: the raw estimate satisfied the predicate and the calibrated one
   violates it.

And in reverse, starting from something that has already happened rather than
something projected: when an `Outcome` shows an estimate missed, `CollateralAgent`
walks the impact DAG back to every decision that leaned on it.

## What it actually prints

Against a store holding a history that runs 1.8× long:

```
$ praxis fuse

  2 estimated_as edge(s) priced
  ┌───────────┬───────┬──────────────────────────────────────────────────────┐
  │ verdict   │ edges │ meaning                                              │
  ├───────────┼───────┼──────────────────────────────────────────────────────┤
  │ flipped   │     1 │ calibration turns a satisfied predicate into a       │
  │           │       │ violated one                                         │
  │ no_factor │     1 │ fewer than five resolved estimates in the group;     │
  │           │       │ no override                                          │
  └───────────┴───────┴──────────────────────────────────────────────────────┘

  1 decision(s) may rest on a mis-estimate — nothing has been measured yet;
  this is what the history implies
    A-0002 assumed migration_weeks = 4; migration work is 1.8000x under, n=5,
    confidence=0.5000 implies 7.2000 — flipped

  1 estimate(s) missed and something rested on them — this already happened
    EST-0001 missed and 2 decision(s) rest on it: D-0002, D-0001
```

## Why this is not something you already have

Four things are usually built, and each solves a different half of the problem.

| What exists | What it knows | What it cannot say |
| ----------- | ------------- | ------------------ |
| **ADR tooling** (adr-tools, Log4brains) | What was decided and why | Nothing about whether it is still true. An ADR is a document, not a claim with a truth value. |
| **Estimation / velocity tools** (Jira, LinearB, Swarmia) | That this team is 1.8× optimistic on migrations | Which decisions that fact invalidates. The estimate is a ticket, not a premise. |
| **Forecasting / calibration training** (Metaculus, Good Judgment) | That an individual's confidence is miscalibrated | Nothing about engineering artefacts at all. The predictions are standalone questions. |
| **Decision-intelligence platforms** | Which decisions exist and who owns them | They record decisions; they do not re-derive whether the numbers under them still hold. |

**Neither half can do this alone, and that is the whole argument:**

- Provenance without calibration cannot tell a **stale** assumption from one that
  was **always optimistic**. Both look like an assumption nobody has revisited.
- Calibration without provenance can tell you a team is 1.8× optimistic on
  migrations and **cannot tell you which decisions that fact invalidates**.

The novel part is not either half. It is the `estimated_as` edge — the claim
that a decision's assumption and a tracked estimate are **the same object**, so
that a fact learned on the calibration side propagates into the provenance
graph and invalidates a decision nobody was re-reading.

## Four things that keep it honest

These are what stop the mechanism being a plausible-sounding number generator.

**1. It fires on a flip, never on a correction.** The common case is a factor
that changes the number and changes nothing else: four weeks corrected to two is
still inside a six-week predicate. Emitting a finding there would produce one per
priced assumption per run and bury the case that matters.

**2. A projection is never filed as a measurement.** A calibrated flip is a
`StaleDecision`, not an `AssumptionBreach`. A breach is a predicate evaluated
false against facts a monitoring run was *given*: something happened. A flip is a
predicate evaluated false against a number **nobody has observed yet** — the work
may still land on time. They are different `FindingKind`s so a triage queue
cannot rank the projection above the measurement, and the prosecution opens by
saying *"Nothing has been measured yet."*
[ADR 0028](adr/0028-a-projection-is-not-a-breach.md).

**3. Only arithmetic can breach.** A model may recognise that an assumption is an
estimate (step 2, Phase 4). Whether the calibrated value violates the predicate
is decided by `praxis.predicates`, a total three-valued evaluator, and no model
can reach `BREACHED`.
[ADR 0019](adr/0019-only-arithmetic-can-breach-and-writes-happen-on-change.md).
`UNKNOWN` is not a flip: a predicate that goes from true to undecided has lost
information, not gained a violation.

**4. It refuses far more often than it speaks, and says so first.** Below five
resolved estimates in a group there is no factor at all. On any young corpus —
including this project's own — nearly every priced edge reports `no_factor`.
`praxis fuse` prints the refusals **before** the findings, because a tool that
showed an empty screen would read as broken when the correct reading is *"eleven
edges priced, none with enough history behind them yet."*

## Where the model calls actually are

`FusionBridge` and `CollateralAgent` both make **zero** model calls, and the
whole fusion pass is free to run. That reads as anticlimactic and it is worth
being precise about why it is true, because the naive reading — "so there is no
intelligence in the fusion layer" — is wrong.

The judgement is real. It has been **pushed earlier**, into extraction, where it
is made once and written down as an edge:

| Component | Tier | Where its judgement went |
| --------- | ---- | ------------------------ |
| `AssumptionExtractor` | `reason` | Decides an assumption is an estimate. **Pays for the whole mechanism.** |
| `FusionBridge` | none | Walks the edge that decision produced ([ADR 0026](adr/0026-the-bridge-prices-a-judgement-phase-4-already-made.md)) |
| `CollateralAgent` | none | Walks the impact DAG Phase 1 built ([ADR 0027](adr/0027-collateral-damage-is-a-walk-phase-1-already-wrote.md)) |

So the honest cost of a fusion finding **includes** the `reason`-tier call made
four phases earlier. What Phase 8 adds on top is a graph walk, one indexed read
per group, a multiplication and two predicate evaluations — which is why the
output is reproducible, costs nothing to recompute, and cannot vary between two
runs over the same store.

## What is not built

- **`ReviewTriageAgent`.** One queue over all three finding kinds, ranked. Phase
  9. Until then a reader queries the kinds separately, which is a real
  ergonomic cost.
- **Reading `Estimate.conditions`.** An estimate 2× out because its scope tripled
  and one 2× out because the estimator is optimistic produce the same ratio, and
  only the second is a calibration signal. The material that would tell them
  apart is stored and nothing reads it. It needs a model, so it belongs in a
  component that annotates a row rather than one that adjusts a factor.
- **Cross-document `estimated_as` edges as graded ground truth.** The corpus
  plants none on purpose — planting them would fix the answer before anyone
  asked the question — so the eval reports a cross-document *count* and never a
  score.

## Reading the code

| File | What it holds |
| ---- | ------------- |
| `praxis/agents/fusion.py` | `FusionBridge`: walk, price, evaluate twice, decide |
| `praxis/agents/collateral.py` | `CollateralAgent`: the reverse walk from a miss |
| `praxis/agents/crossdoc.py` | Matching an estimate's actual in another document |
| `praxis/agents/fusion_pass.py` | Both directions over a store; writes only on a change |
| `praxis/cli_fuse.py` | `praxis fuse` |
| `praxis/eval/fusion.py` | Grading it, which is mostly grading the refusals |
| `praxis/agents/bias.py` | `BiasDetective`, and the threshold that refuses |
| `praxis/store/graph.py` | `impacted_by` — the walk, built in Phase 1 |
