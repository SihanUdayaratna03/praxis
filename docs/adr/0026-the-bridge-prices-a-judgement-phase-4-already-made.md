---
id: ADR-0026
status: accepted
date: 2026-08-31
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0026 — the bridge prices a judgement Phase 4 already made

## Chosen

`FusionBridge` **makes no model call**. It moves out of ADR 0006's `reason` row
— the most expensive tier in the table — and into `NON_LLM_AGENTS`, alongside
`BiasDetective`, `CalibratorAgent`, `ScoringAgent`, `SourceAdapter` and
`VerifierAgent`.

The reason is not that the fusion mechanism contains no judgement. It contains
exactly one, and **it was already made and already paid for**: deciding that an
assumption is a quantified forward-looking claim — an estimate wearing an
assumption's clothes — happens in `AssumptionExtractor`, on the `reason` tier,
in a call that is already holding the assumption and its quantity, and the
answer is written down as an `estimated_as` edge. ADR 0016 made that choice and
Phase 4 shipped it; the eval harness has been grading those edges since.

What is left for this phase, once that edge exists, is:

1. a graph walk over `estimated_as` edges the store already holds;
2. one indexed read per `(owner, work_class)` group, through
   `BiasDetective.factor_for`, which is itself deterministic;
3. one multiplication, `raw * factor`; and
4. two evaluations of the same predicate through `praxis.predicates`.

Step 4 is already closed to a model by ADR 0019: only arithmetic can reach
`BREACHED`. Steps 1 to 3 are a query, a query and a multiply. **There is nothing
left in this component for a model to decide.**

The finding fires on a **flip** — the raw estimate satisfies the predicate and
the calibrated one violates it — and not on a correction. `UNKNOWN` on either
side is its own verdict and raises nothing.

## Rejected

| Option | Why not |
| ------ | ------- |
| Keep the `reason` route and have `FusionBridge` re-decide which assumptions are estimates | This is the decision the record reverses, and it would mean paying the most expensive tier in the table to re-derive an answer already stored as an edge. Worse than the cost: the two answers could disagree. An assumption Phase 4 linked and Phase 8 declined to link would be a contradiction with no arbiter, and the corpus grades Phase 4's answer, so Phase 8's would be the ungraded one winning. |
| Use a model for the *cross-document* half of the recognition, keeping the same class | The cross-document half is real work and is still outstanding — ADR 0016 says so and `BACKLOG.md` carries it. It is a different job with a different failure mode: assembling one claim out of two documents, which ADR 0015's single-document offering deliberately makes hard. Putting it inside the component that walks existing edges would make one class both a detector and a pricer, and the refusals of the two have nothing in common. It gets its own component and its own record. |
| Have a model decide whether a flip is worth reporting | That is the finding threshold, and it is arithmetic: two three-valued verdicts compared. ADR 0024 already refused an override on the calibration threshold, and an editorial judgement about what to surface is the same override wearing a different hat — ADR 0025 rejected it in those words for the calibrator. |
| Have a model write the finding's prosecution | ADR 0025 settled this for the calibrator eight days ago and the argument transfers unchanged: every number in the sentence — the raw quantity, the calibrated one, the band, `n`, the confidence — exists before the sentence does. A fluent paragraph citing the wrong digit reads better than the correct one and no test separates them. |
| Leave `FusionBridge` in `_ROUTING` unused | `praxis doctor` asserts that `NON_LLM_AGENTS` and the routing table never overlap, so this is not a state the system permits. It would also leave the cost table implying a per-assumption model spend that never happens. |
| Supersede ADR 0006 | Its actual decision — agents ask for a role, one file holds every model id — is untouched. This amends one row, as ADR 0025 did, and both files say so. |

## What was known at the time

`FusionBridge` was assigned `reason` in Phase 0, before any agent existed, on
the strength of `ARCHITECTURE.md`'s own description of it: *"`FusionBridge`
recognises that the predicate's subject is a quantified forward-looking claim
— that is, an estimate wearing an assumption's clothes — and writes an
`estimated_as` edge."* Read cold, that is judgement about prose, and the most
expensive tier was the right call for it.

Two things changed between then and building it, and neither was a surprise so
much as an accumulation:

- **ADR 0016, Phase 4.** The extractor was already reading the assumption and
  already holding its quantity, so making the same judgement a second time later
  would have been paying twice for one answer. It wrote the edge.
- **ADR 0019, Phase 5.** Only arithmetic can breach. Whatever `FusionBridge`
  concluded, the violation itself had to be evaluated by the predicate engine.

Between them they took the judgement off the front of this component and the
verdict off the back, and what was left in the middle turned out to be a join.

**This is the fifth consecutive phase in which pricing the arithmetic/judgement
boundary as its own line item moved a component to the arithmetic side**:
`VerifierAgent` in Phase 3, `match_quality` in Phase 6 (ADR 0021),
`CalibratorAgent` in Phase 7 (ADR 0025), and now this. `EST-0009` priced it as a
line item for the fifth time and named this exact possibility — "the strong
suspicion, priced in, is that recognition for the edges that ALREADY EXIST needs
no model at all, because Phase 4 paid for the judgement when it wrote the edge."
That is the first time this project's estimate has predicted where the boundary
would fall before the work was done rather than recorded it afterwards.

Not known, and deliberately left open: whether the cross-document recogniser
that is still outstanding will need a model. It almost certainly will, and it is
a different component. Its route stays in the table under its own name.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | The bridge never acquires a model route | `fusion_module_llm_imports == 0` | `on_event("cross-document recognition moves into FusionBridge")` |
| 2 | Phase 4's edges are the only ones the bridge walks | `bridge_written_estimated_as_edges == 0` | `on_event("a cross-document recogniser ships")` |
| 3 | A flip is rare enough to be worth a person's attention | `flips_per_priced_edge <= 0.2` | `when(priced_edges >= 100)` |
| 4 | The undecided branch stays unreachable through the subject gate | `undecided_verdicts_from_the_bridge == 0` | `on_event("constraints_of names the subject of a compound predicate")` |

## Consequences

**Accepted costs.** The routing table now has two entries fewer than the
architecture diagram has agents, and both absences look like omissions. ADR 0006
carries amendment notes pointing at 0025 and here, and `praxis doctor` settles
the live answer.

The component that carries the project's headline claim spends nothing on
models, which will read as anticlimactic in a demo. It is the correct outcome
and it is also the strongest one available: the sentence *"Decision D-0001 is
probably built on a 40% under-estimate"* is reproducible, costs nothing to
recompute, and cannot vary between two runs over the same store.

Assumption 4 records something found while building this and worth carrying:
the `UNDECIDED` branch cannot currently be reached through `FusionBridge`,
because `constraints_of` yields a subject only for simple `name OP literal`
comparisons and every predicate an evaluator could leave `UNKNOWN` is refused as
`NO_SUBJECT` several steps earlier. The branch is kept and tested directly
through `moved`. It is a standing constraint on the predicate language rather
than dead code, and its expiry condition is written to fire the day that
language grows.

**Reversal cost.** Low mechanically — one line in `_ROUTING`, one in
`NON_LLM_AGENTS`, a provider argument on the constructor. High in what it would
cost afterwards: a fusion finding is an allegation against a decision somebody
made, and a bridge that could re-decide which assumptions count as estimates
would be able to produce a finding Phase 4's graded edges do not support, with
no way after the fact to tell which of the two was right.
